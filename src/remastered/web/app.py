from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import InstalledAppFlow
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from remastered.config import PrivacyStatus, resolve_settings
from remastered.download import YOUTUBE_SCOPES
from remastered.web.db import STAGES, JobStore
from remastered.web.oauth_status import get_youtube_oauth_status
from remastered.web.settings import WebSettings
from remastered.web.worker import JobWorker, build_worker

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

STAGE_LABELS = {
    "download": "다운로드",
    "master": "마스터링",
    "render": "렌더",
    "upload": "업로드",
}


def _web_settings() -> WebSettings:
    return WebSettings()


def _job_db_path() -> Path:
    settings = resolve_settings()
    return settings.work_dir / "remastered_jobs.db"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    store = JobStore(_job_db_path())
    worker = build_worker(store)
    worker.start()
    app.state.store = store
    app.state.worker = worker
    yield
    worker.stop()


app = FastAPI(title="Remastered", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ws = _web_settings()
        if ws.password and not request.session.get("authenticated"):
            path = request.url.path
            if path not in ("/login", "/health") and not path.startswith("/static"):
                return RedirectResponse(url="/login", status_code=303)
        return await call_next(request)


ws_init = _web_settings()
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=ws_init.session_secret)


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_worker(request: Request) -> JobWorker:
    return request.app.state.worker


def require_user(request: Request) -> None:
    """미들웨어 이후 이중 확인 (API 일관성)."""
    ws = _web_settings()
    if ws.password and not request.session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )


def _list_reference_files(references_dir: Path) -> list[str]:
    if not references_dir.is_dir():
        return []
    exts = {".wav", ".flac", ".mp3", ".aiff", ".aif"}
    names: list[str] = []
    for path in sorted(references_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in exts:
            names.append(path.name)
    return names


def _safe_media_path(work_dir: Path, rel: str) -> Path:
    base = work_dir.resolve()
    target = (work_dir / rel).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return target


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
) -> RedirectResponse:
    ws = _web_settings()
    if ws.password and secrets.compare_digest(password, ws.password):
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    _: None = Depends(require_user),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    settings = resolve_settings()
    ws = _web_settings()
    oauth = get_youtube_oauth_status(settings)
    jobs = store.list_jobs(limit=20)
    refs = _list_reference_files(ws.references_path)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "jobs": jobs,
            "references": refs,
            "oauth": oauth,
            "stage_labels": STAGE_LABELS,
            "default_privacy": settings.default_privacy,
        },
    )


@app.post("/jobs")
async def create_job(
    request: Request,
    _: None = Depends(require_user),
    store: JobStore = Depends(get_store),
    worker: JobWorker = Depends(get_worker),
    youtube_url: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    privacy: PrivacyStatus = Form("private"),
    upload_enabled: str = Form(""),
    reference_name: str = Form(""),
    reference_upload: UploadFile | None = File(None),
) -> RedirectResponse:
    ws = _web_settings()
    ws.references_path.mkdir(parents=True, exist_ok=True)

    reference_path: Path | None = None
    if reference_upload and reference_upload.filename:
        safe_name = Path(reference_upload.filename).name
        dest = ws.references_path / safe_name
        content = await reference_upload.read()
        dest.write_bytes(content)
        reference_path = dest
    elif reference_name:
        candidate = ws.references_path / Path(reference_name).name
        if candidate.is_file():
            reference_path = candidate

    if reference_path is None:
        raise HTTPException(status_code=400, detail="레퍼런스 트랙을 선택하거나 업로드하세요.")

    upload_on = upload_enabled in ("on", "true", "1", "yes")
    job_id = store.create_job(
        youtube_url=youtube_url.strip(),
        reference_path=str(reference_path),
        title=title.strip() or None,
        description=description,
        privacy=privacy,
        upload_enabled=upload_on,
    )
    store.append_log(job_id, "작업이 대기열에 추가되었습니다.")
    worker.enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: str,
    _: None = Depends(require_user),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "logs": logs,
            "stage_labels": STAGE_LABELS,
            "stages": STAGES,
        },
    )


@app.get("/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel(
    request: Request,
    job_id: str,
    _: None = Depends(require_user),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    return templates.TemplateResponse(
        request,
        "partials/job_panel.html",
        {
            "job": job,
            "logs": logs,
            "stage_labels": STAGE_LABELS,
            "stages": STAGES,
        },
    )


@app.get("/media/{job_id}/{kind}")
def media_file(
    job_id: str,
    kind: str,
    _: None = Depends(require_user),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404)
    key = {"audio": "audio_path", "mastered": "mastered_path", "video": "video_path"}.get(
        kind
    )
    if not key or not job.get(key):
        raise HTTPException(status_code=404)
    settings = resolve_settings()
    path = Path(job[key])
    try:
        path.relative_to(settings.work_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=404)
    if not path.is_file():
        raise HTTPException(status_code=404)
    media_type = "video/mp4" if kind == "video" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/oauth/start")
def oauth_start(request: Request, _: None = Depends(require_user)) -> RedirectResponse:
    settings = resolve_settings()
    if not settings.client_secrets or not settings.client_secrets.is_file():
        raise HTTPException(status_code=400, detail="client_secrets.json이 없습니다.")
    ws = _web_settings()
    base = (ws.oauth_redirect_base or str(request.base_url)).rstrip("/")
    redirect_uri = f"{base}/oauth/callback"
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.client_secrets), YOUTUBE_SCOPES
    )
    flow.redirect_uri = redirect_uri
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session["oauth_state"] = state
    request.session["oauth_redirect_uri"] = redirect_uri
    return RedirectResponse(authorization_url)


@app.get("/oauth/callback")
def oauth_callback(request: Request, _: None = Depends(require_user)) -> RedirectResponse:
    settings = resolve_settings()
    state = request.session.get("oauth_state")
    redirect_uri = request.session.get("oauth_redirect_uri")
    if not state or not redirect_uri:
        raise HTTPException(status_code=400, detail="OAuth 세션이 없습니다. 다시 시도하세요.")
    params = dict(request.query_params)
    if params.get("state") != state:
        raise HTTPException(status_code=400, detail="OAuth state가 일치하지 않습니다.")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.client_secrets), YOUTUBE_SCOPES
    )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials
    settings.token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.token_path.write_text(creds.to_json(), encoding="utf-8")
    request.session.pop("oauth_state", None)
    return RedirectResponse("/?oauth=connected", status_code=303)


def create_app() -> FastAPI:
    """테스트용 앱 팩토리."""
    return app
