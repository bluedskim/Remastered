from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from remastered.config import resolve_settings
from remastered.web.db import WEB_STAGES, JobStore
from remastered.web.auth_display import format_account_email_display
from remastered.web.auth_redirect import request_prefers_auth_redirect, signup_url
from remastered.web.auth_routes import register_auth_routes
from remastered.web.identity import (
    RequestIdentity,
    client_ip,
    resolve_identity,
    session_middleware_kwargs,
)
from remastered.web.download_filenames import media_download_filename
from remastered.web.media import (
    RETENTION_MESSAGE,
    media_exists,
    media_file_response,
    resolve_job_media_path,
)
from remastered.web.job_queue import job_status_display
from remastered.web.notices import rights_notice_lines, rights_notice_short
from remastered.web.job_titles import job_display_title
from remastered.web.job_ui import enrich_job_ui
from remastered.web.reference_display import build_reference_options
from remastered.web.resilience import MediaStreamLimitMiddleware, mount_health_router
from remastered.web.static_assets import init_static_hashes, static_url
from remastered.web.static_cache import StaticAndHtmlCacheMiddleware
from remastered.web.settings import WebSettings
from remastered.web.worker import JobWorker, build_worker
from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE, VALID_STEREO_WIDTH_MODES
from remastered.youtube_url import normalize_youtube_url

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
init_static_hashes(STATIC_DIR)
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
templates.env.globals["job_display_title"] = job_display_title
templates.env.globals["static_url"] = static_url

STAGE_LABELS = {
    "download": "Download",
    "master": "Mastering",
}

TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})

DOWNLOAD_REQUIRES_SIGN_IN = "Sign in required to download the remastered file."

PRIVACY_NOTICE = (
    "We store: anonymous session id, job URLs, logs, and output file paths. "
    "If you sign up, we also store email and a password hash. "
    "Job files are deleted after the retention period."
)


def _web_settings() -> WebSettings:
    return WebSettings()


def _job_db_path() -> Path:
    settings = resolve_settings()
    return settings.work_dir / "remastered_jobs.db"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.static_hashes = init_static_hashes(STATIC_DIR)
    store = JobStore(_job_db_path())
    worker = build_worker(store)
    worker.start()
    app.state.store = store
    app.state.worker = worker
    yield
    worker.stop()


fastapi_application = FastAPI(title="Remastered", lifespan=_lifespan)
ws_init = _web_settings()
fastapi_application.add_middleware(
    ProxyHeadersMiddleware, trusted_hosts=ws_init.trusted_proxy_ips
)
fastapi_application.mount(
    "/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static"
)

from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

fastapi_application.add_middleware(
    SessionMiddleware,
    **session_middleware_kwargs(ws_init.public_base_url, ws_init.session_secret),
)
fastapi_application.add_middleware(
    MediaStreamLimitMiddleware,
    max_streams=ws_init.max_concurrent_media_streams,
)
fastapi_application.add_middleware(StaticAndHtmlCacheMiddleware)


@fastapi_application.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@fastapi_application.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    path = STATIC_DIR / "favicon.ico"
    return FileResponse(
        path,
        media_type="image/vnd.microsoft.icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_worker(request: Request) -> JobWorker:
    return request.app.state.worker


def require_identity(request: Request) -> RequestIdentity:
    return resolve_identity(request)


def _list_reference_files(references_dir: Path) -> list[str]:
    if not references_dir.is_dir():
        return []
    exts = {".wav", ".flac", ".mp3", ".aiff", ".aif"}
    return sorted(
        p.name
        for p in references_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    )


def _job_media_context(job: dict) -> dict[str, bool | str | None]:
    ws = _web_settings()
    settings = resolve_settings()
    work = settings.work_dir
    audio_path = job.get("audio_path")
    mastered_path = job.get("mastered_path")
    audio_ok = media_exists(audio_path, job, work)
    mastered_ok = media_exists(mastered_path, job, work)
    had_paths = bool(audio_path or mastered_path)
    expired = job["status"] == "completed" and had_paths and not (audio_ok or mastered_ok)
    mastered_download_filename: str | None = None
    if mastered_ok and mastered_path:
        mastered_download_filename = media_download_filename(
            job, Path(mastered_path)
        )

    return {
        "audio_ok": audio_ok,
        "mastered_ok": mastered_ok,
        "ab_compare_ok": audio_ok and mastered_ok,
        "files_expired": expired,
        "retention_message": RETENTION_MESSAGE,
        "mastered_download_filename": mastered_download_filename,
        "rights_notice_short": rights_notice_short(ws.job_retention_days),
    }


def _owner_references_dir(owner_id: str) -> Path:
    settings = resolve_settings()
    path = settings.work_dir / "users" / owner_id / "references"
    path.mkdir(parents=True, exist_ok=True)
    return path


@fastapi_application.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 429:
        return HTMLResponse(
            f"<h1>429</h1><p>{exc.detail}</p><p><a href='/'>Back to home</a></p>",
            status_code=429,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _enrich_job(job: dict, store: JobStore) -> dict:
    data = enrich_job_ui(job)
    data["status_display"] = job_status_display(job, store)
    return data


def _auth_template_context(identity: RequestIdentity) -> dict:
    if identity.account_id:
        email = identity.login_id or ""
        return {
            "auth_user": {
                "id": identity.account_id,
                "email_display": format_account_email_display(email),
                "email_full": email,
            }
        }
    return {"auth_user": None}


def _dashboard_context(
    request: Request,
    identity: RequestIdentity,
    store: JobStore,
) -> dict:
    ws = _web_settings()
    jobs = [
        _enrich_job(j, store)
        for j in store.list_jobs_for_access(
            account_id=identity.account_id,
            anon_id=identity.anon_id,
            limit=20,
        )
    ]
    owner_id = identity.job_owner_id
    shared_refs = _list_reference_files(ws.references_path)
    user_refs = _list_reference_files(_owner_references_dir(owner_id))
    return {
        "jobs": jobs,
        "shared_references": shared_refs,
        "user_references": user_refs,
        "reference_options": build_reference_options(shared_refs, user_refs),
        "stage_labels": STAGE_LABELS,
        "privacy_notice": PRIVACY_NOTICE,
        "rights_notice_lines": rights_notice_lines(ws.job_retention_days),
        "limits": ws,
        **_auth_template_context(identity),
    }


register_auth_routes(fastapi_application, templates, PRIVACY_NOTICE)


@fastapi_application.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        _dashboard_context(request, identity, store),
    )


@fastapi_application.get("/dashboard", response_class=HTMLResponse)
def dashboard_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@fastapi_application.post("/jobs")
async def create_job(
    request: Request,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
    worker: JobWorker = Depends(get_worker),
    youtube_url: str = Form(...),
    reference_name: str = Form(""),
    reference_source: str = Form("shared"),
    reference_upload: UploadFile | None = File(None),
    stereo_width_mode: str = Form(DEFAULT_STEREO_WIDTH_MODE),
) -> RedirectResponse:
    ip = client_ip(request)
    ws = _web_settings()
    reference_path: Path | None = None
    owner_id = identity.job_owner_id
    owner_ref_dir = _owner_references_dir(owner_id)

    if reference_upload and reference_upload.filename:
        safe_name = Path(reference_upload.filename).name
        dest = owner_ref_dir / safe_name
        body = await reference_upload.read()

        def _write_upload() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)

        await run_in_threadpool(_write_upload)
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
        raise HTTPException(
            status_code=400, detail="Select or upload a reference track."
        )

    youtube_url = normalize_youtube_url(youtube_url)
    duplicate_id = await run_in_threadpool(
        partial(
            store.find_recent_duplicate_job,
            owner_id,
            youtube_url,
            str(reference_path),
            within_seconds=ws.duplicate_job_window_seconds,
        )
    )
    if duplicate_id:
        return RedirectResponse(f"/jobs/{duplicate_id}", status_code=303)

    if stereo_width_mode not in VALID_STEREO_WIDTH_MODES:
        stereo_width_mode = DEFAULT_STEREO_WIDTH_MODE

    job_id = store.create_job(
        user_id=owner_id,
        youtube_url=youtube_url,
        reference_path=str(reference_path),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip=ip,
        stereo_width_mode=stereo_width_mode,
    )
    store.append_log(job_id, "Job added to the queue.")
    worker.enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@fastapi_application.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: str,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job_for_access(
        job_id,
        account_id=identity.account_id,
        anon_id=identity.anon_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": _enrich_job(job, store),
            "logs": logs,
            "stage_labels": STAGE_LABELS,
            "stages": WEB_STAGES,
            "privacy_notice": PRIVACY_NOTICE,
            **_auth_template_context(identity),
            **media_ctx,
        },
    )


@fastapi_application.get("/jobs/{job_id}/panel", response_class=HTMLResponse)
def job_panel(
    request: Request,
    job_id: str,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    job = store.get_job_for_access(
        job_id,
        account_id=identity.account_id,
        anon_id=identity.anon_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    logs = store.get_logs(job_id)
    media_ctx = _job_media_context(job)
    ctx = {
        "job": _enrich_job(job, store),
        "logs": logs,
        "stage_labels": STAGE_LABELS,
        "stages": WEB_STAGES,
        "privacy_notice": PRIVACY_NOTICE,
        **media_ctx,
        **_auth_template_context(identity),
    }
    # HTMX: 286 stops hx-trigger polling (see htmx.org/docs/#polling)
    status_code = 286 if job["status"] in TERMINAL_JOB_STATUSES else status.HTTP_200_OK
    return templates.TemplateResponse(
        request,
        "partials/job_panel_poll.html",
        ctx,
        status_code=status_code,
    )


@fastapi_application.get("/media/{job_id}/{kind}")
async def media_file(
    request: Request,
    job_id: str,
    kind: str,
    download: int | None = None,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
) -> Response:
    job = await run_in_threadpool(
        store.get_job_for_access,
        job_id,
        account_id=identity.account_id,
        anon_id=identity.anon_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    settings = resolve_settings()
    work_dir = settings.work_dir
    want_download = bool(download)

    if want_download and kind == "mastered" and not identity.account_id:
        if request_prefers_auth_redirect(request):
            return RedirectResponse(
                signup_url(f"/jobs/{job_id}"),
                status_code=303,
            )
        raise HTTPException(status_code=401, detail=DOWNLOAD_REQUIRES_SIGN_IN)

    def _resolve() -> Path:
        return resolve_job_media_path(
            job,
            kind,
            work_dir=work_dir,
            download=want_download,
        )

    path = await run_in_threadpool(_resolve)
    return media_file_response(path, kind, download=want_download, job=job)


app = mount_health_router(fastapi_application)


def create_app() -> FastAPI:
    return fastapi_application
