from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from remastered.config import resolve_settings
from remastered.web.auth_google import (
    authorization_url,
    exchange_code,
    is_google_oauth_configured,
    revoke_user_token,
    upsert_user_from_credentials,
)
from remastered.web.db import WEB_STAGES, JobStore
from remastered.web.media import (
    RETENTION_MESSAGE,
    media_exists,
    media_file_response,
    resolve_job_media_path,
)
from remastered.web.limits import JobCreateRateLimiter, enforce_job_limits
from remastered.web.settings import WebSettings
from remastered.web.worker import JobWorker, build_worker

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

STAGE_LABELS = {
    "download": "다운로드",
    "master": "마스터링",
}

PRIVACY_NOTICE = (
    "저장 항목: Google 계정 식별자·이메일, 암호화된 YouTube OAuth 토큰, 작업 URL·"
    "로그·생성 파일 경로. 토큰은 서버 SQLite에 암호화 저장되며, 로그아웃·연결 해제 시 "
    "삭제·revoke 할 수 있습니다."
)


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
    ws = _web_settings()
    app.state.store = store
    app.state.worker = worker
    app.state.rate_limiter = JobCreateRateLimiter(ws.job_create_min_interval_seconds)
    yield
    worker.stop()


app = FastAPI(title="Remastered", lifespan=_lifespan)
ws_init = _web_settings()
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=ws_init.trusted_proxy_ips)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

app.add_middleware(SessionMiddleware, secret_key=ws_init.session_secret)


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_worker(request: Request) -> JobWorker:
    return request.app.state.worker


def get_rate_limiter(request: Request) -> JobCreateRateLimiter:
    return request.app.state.rate_limiter


def current_user_id(request: Request) -> str | None:
    return request.session.get("user_id")


def require_signed_in(request: Request) -> str:
    user_id = current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user_id


def _list_reference_files(references_dir: Path) -> list[str]:
    if not references_dir.is_dir():
        return []
    exts = {".wav", ".flac", ".mp3", ".aiff", ".aif"}
    return sorted(
        p.name
        for p in references_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    )


def _job_media_context(job: dict, user_id: str) -> dict[str, bool | str]:
    settings = resolve_settings()
    work = settings.work_dir
    audio_path = job.get("audio_path")
    mastered_path = job.get("mastered_path")
    audio_ok = media_exists(audio_path, user_id, work)
    mastered_ok = media_exists(mastered_path, user_id, work)
    had_paths = bool(audio_path or mastered_path)
    expired = job["status"] == "completed" and had_paths and not (audio_ok or mastered_ok)
    return {
        "audio_ok": audio_ok,
        "mastered_ok": mastered_ok,
        "files_expired": expired,
        "retention_message": RETENTION_MESSAGE,
    }


def _user_references_dir(user_id: str) -> Path:
    settings = resolve_settings()
    path = settings.work_dir / "users" / user_id / "references"
    path.mkdir(parents=True, exist_ok=True)
    return path


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and request.url.path != "/":
        if is_google_oauth_configured():
            return RedirectResponse("/auth/google", status_code=303)
        return RedirectResponse("/auth/unavailable", status_code=303)
    if exc.status_code == 429:
        return HTMLResponse(
            f"<h1>429</h1><p>{exc.detail}</p><p><a href='/dashboard'>돌아가기</a></p>",
            status_code=429,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    if current_user_id(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "privacy_notice": PRIVACY_NOTICE,
            "google_login_enabled": is_google_oauth_configured(),
        },
    )


@app.get("/auth/unavailable", response_class=HTMLResponse)
def auth_unavailable(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "oauth_not_configured.html", {})


@app.get("/auth/google", response_model=None)
def auth_google_start(request: Request):
    if not is_google_oauth_configured():
        return templates.TemplateResponse(request, "oauth_not_configured.html", {})
    ws = _web_settings()
    redirect_uri = ws.oauth_redirect_uri()
    url, state = authorization_url(redirect_uri)
    request.session["oauth_state"] = state
    request.session["oauth_redirect_uri"] = redirect_uri
    return RedirectResponse(url)


@app.get("/oauth/callback")
def oauth_callback(request: Request, store: JobStore = Depends(get_store)) -> RedirectResponse:
    state = request.session.get("oauth_state")
    redirect_uri = request.session.get("oauth_redirect_uri")
    if not state or not redirect_uri:
        raise HTTPException(status_code=400, detail="OAuth 세션이 없습니다.")
    if request.query_params.get("state") != state:
        raise HTTPException(status_code=400, detail="OAuth state 불일치")
    creds = exchange_code(str(request.url), redirect_uri)
    user_id = upsert_user_from_credentials(store, creds)
    request.session["user_id"] = user_id
    request.session["user_email"] = store.get_user(user_id).get("email")
    request.session.pop("oauth_state", None)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/account/disconnect")
def account_disconnect(
    request: Request,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
) -> RedirectResponse:
    revoke_user_token(store, user_id)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    settings = resolve_settings()
    ws = _web_settings()
    user = store.get_user(user_id) or {}
    jobs = store.list_jobs(user_id, limit=20)
    shared_refs = _list_reference_files(ws.references_path)
    user_refs = _list_reference_files(_user_references_dir(user_id))
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "jobs": jobs,
            "shared_references": shared_refs,
            "user_references": user_refs,
            "user": user,
            "stage_labels": STAGE_LABELS,
            "privacy_notice": PRIVACY_NOTICE,
            "limits": ws,
        },
    )


@app.post("/jobs")
async def create_job(
    request: Request,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
    worker: JobWorker = Depends(get_worker),
    limiter: JobCreateRateLimiter = Depends(get_rate_limiter),
    youtube_url: str = Form(...),
    reference_name: str = Form(""),
    reference_source: str = Form("shared"),
    reference_upload: UploadFile | None = File(None),
) -> RedirectResponse:
    limiter.check(user_id)
    enforce_job_limits(store, user_id, youtube_url)

    ws = _web_settings()
    reference_path: Path | None = None
    user_ref_dir = _user_references_dir(user_id)

    if reference_upload and reference_upload.filename:
        safe_name = Path(reference_upload.filename).name
        dest = user_ref_dir / safe_name
        dest.write_bytes(await reference_upload.read())
        reference_path = dest
    elif reference_name:
        name = Path(reference_name).name
        if reference_source == "user":
            candidate = user_ref_dir / name
        else:
            candidate = ws.references_path / name
        if candidate.is_file():
            reference_path = candidate

    if reference_path is None:
        raise HTTPException(status_code=400, detail="레퍼런스 트랙을 선택하거나 업로드하세요.")

    job_id = store.create_job(
        user_id=user_id,
        youtube_url=youtube_url.strip(),
        reference_path=str(reference_path),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.append_log(job_id, "작업이 대기열에 추가되었습니다.")
    worker.enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: str,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job, user_id)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "logs": logs,
            "stage_labels": STAGE_LABELS,
            "stages": WEB_STAGES,
            **media_ctx,
        },
    )


@app.get("/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel(
    request: Request,
    job_id: str,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job, user_id)
    return templates.TemplateResponse(
        request,
        "partials/job_panel.html",
        {
            "job": job,
            "logs": logs,
            "stage_labels": STAGE_LABELS,
            "stages": WEB_STAGES,
            **media_ctx,
        },
    )


@app.get("/media/{job_id}/{kind}")
def media_file(
    job_id: str,
    kind: str,
    user_id: str = Depends(require_signed_in),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = store.get_job(job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    settings = resolve_settings()
    path = resolve_job_media_path(
        job, kind, user_id=user_id, work_dir=settings.work_dir
    )
    return media_file_response(path, kind)


def create_app() -> FastAPI:
    return app
