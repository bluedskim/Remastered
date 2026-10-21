from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from remastered.config import resolve_settings
from remastered.web.db import WEB_STAGES, JobStore
from remastered.web.identity import (
    client_ip,
    get_or_create_owner_id,
    session_middleware_kwargs,
)
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

TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})

PRIVACY_NOTICE = (
    "저장 항목: 익명 세션 식별자, 작업 URL·로그·생성 파일 경로. "
    "작업 파일은 보관 기간 후 삭제됩니다."
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

app.add_middleware(
    SessionMiddleware,
    **session_middleware_kwargs(ws_init.public_base_url, ws_init.session_secret),
)


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_worker(request: Request) -> JobWorker:
    return request.app.state.worker


def get_rate_limiter(request: Request) -> JobCreateRateLimiter:
    return request.app.state.rate_limiter


def require_owner(request: Request) -> str:
    return get_or_create_owner_id(request)


def _list_reference_files(references_dir: Path) -> list[str]:
    if not references_dir.is_dir():
        return []
    exts = {".wav", ".flac", ".mp3", ".aiff", ".aif"}
    return sorted(
        p.name
        for p in references_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    )


def _job_media_context(job: dict, owner_id: str) -> dict[str, bool | str]:
    settings = resolve_settings()
    work = settings.work_dir
    audio_path = job.get("audio_path")
    mastered_path = job.get("mastered_path")
    audio_ok = media_exists(audio_path, owner_id, work)
    mastered_ok = media_exists(mastered_path, owner_id, work)
    had_paths = bool(audio_path or mastered_path)
    expired = job["status"] == "completed" and had_paths and not (audio_ok or mastered_ok)
    return {
        "audio_ok": audio_ok,
        "mastered_ok": mastered_ok,
        "files_expired": expired,
        "retention_message": RETENTION_MESSAGE,
    }


def _owner_references_dir(owner_id: str) -> Path:
    settings = resolve_settings()
    path = settings.work_dir / "users" / owner_id / "references"
    path.mkdir(parents=True, exist_ok=True)
    return path


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 429:
        return HTMLResponse(
            f"<h1>429</h1><p>{exc.detail}</p><p><a href='/'>돌아가기</a></p>",
            status_code=429,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _dashboard_context(
    request: Request,
    owner_id: str,
    store: JobStore,
) -> dict:
    ws = _web_settings()
    jobs = store.list_jobs(owner_id, limit=20)
    shared_refs = _list_reference_files(ws.references_path)
    user_refs = _list_reference_files(_owner_references_dir(owner_id))
    return {
        "jobs": jobs,
        "shared_references": shared_refs,
        "user_references": user_refs,
        "stage_labels": STAGE_LABELS,
        "privacy_notice": PRIVACY_NOTICE,
        "limits": ws,
    }


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    owner_id: str = Depends(require_owner),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        _dashboard_context(request, owner_id, store),
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@app.post("/jobs")
async def create_job(
    request: Request,
    owner_id: str = Depends(require_owner),
    store: JobStore = Depends(get_store),
    worker: JobWorker = Depends(get_worker),
    limiter: JobCreateRateLimiter = Depends(get_rate_limiter),
    youtube_url: str = Form(...),
    reference_name: str = Form(""),
    reference_source: str = Form("shared"),
    reference_upload: UploadFile | None = File(None),
) -> RedirectResponse:
    ip = client_ip(request)
    limiter.check(owner_id, ip)
    enforce_job_limits(store, owner_id, ip, youtube_url)

    ws = _web_settings()
    reference_path: Path | None = None
    owner_ref_dir = _owner_references_dir(owner_id)

    if reference_upload and reference_upload.filename:
        safe_name = Path(reference_upload.filename).name
        dest = owner_ref_dir / safe_name
        dest.write_bytes(await reference_upload.read())
        reference_path = dest
    elif reference_name:
        name = Path(reference_name).name
        if reference_source == "user":
            candidate = owner_ref_dir / name
        else:
            candidate = ws.references_path / name
        if candidate.is_file():
            reference_path = candidate

    if reference_path is None:
        raise HTTPException(status_code=400, detail="레퍼런스 트랙을 선택하거나 업로드하세요.")

    job_id = store.create_job(
        user_id=owner_id,
        youtube_url=youtube_url.strip(),
        reference_path=str(reference_path),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip=ip,
    )
    store.append_log(job_id, "작업이 대기열에 추가되었습니다.")
    worker.enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: str,
    owner_id: str = Depends(require_owner),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id, user_id=owner_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job, owner_id)
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
    owner_id: str = Depends(require_owner),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job(job_id, user_id=owner_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job, owner_id)
    ctx = {
        "job": job,
        "logs": logs,
        "stage_labels": STAGE_LABELS,
        "stages": WEB_STAGES,
        **media_ctx,
    }
    # HTMX: 286 stops hx-trigger polling (see htmx.org/docs/#polling)
    status_code = 286 if job["status"] in TERMINAL_JOB_STATUSES else status.HTTP_200_OK
    return templates.TemplateResponse(
        request,
        "partials/job_panel_poll.html",
        ctx,
        status_code=status_code,
    )


@app.get("/media/{job_id}/{kind}")
def media_file(
    job_id: str,
    kind: str,
    download: int | None = None,
    owner_id: str = Depends(require_owner),
    store: JobStore = Depends(get_store),
) -> FileResponse:
    job = store.get_job(job_id, user_id=owner_id)
    if not job:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    settings = resolve_settings()
    path = resolve_job_media_path(
        job, kind, user_id=owner_id, work_dir=settings.work_dir
    )
    return media_file_response(path, kind, download=bool(download))


def create_app() -> FastAPI:
    return app
