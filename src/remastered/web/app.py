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
from remastered.web.anon_visit import ensure_identity_with_anon_visit
from remastered.web.identity import (
    RequestIdentity,
    client_ip,
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
from remastered.web.reference_prefs import (
    ReferencePrefs,
    merge_client_prefs,
    prefs_from_store_row,
    sanitize_prefs_for_owner,
    with_active_tab,
)
from remastered.web.reference_youtube import (
    CHOOSE_REFERENCE_FILE,
    ENTER_REFERENCE_YOUTUBE_URL,
    REFERENCE_SOURCE_MODES,
    SELECT_PRESET_REFERENCE,
    ensure_reference_differs_from_source,
    parse_reference_youtube_url,
    youtube_reference_path_key,
)
from remastered.web.cleanup import delete_job_media_files
from remastered.web.resilience import MediaStreamLimitMiddleware, mount_health_router
from remastered.web.static_assets import init_static_hashes, static_url
from remastered.web.static_cache import StaticAndHtmlCacheMiddleware
from remastered.web.settings import WebSettings
from remastered.web.worker import JobWorker, build_worker
from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE, VALID_STEREO_WIDTH_MODES
from remastered.web.youtube_meta import fetch_youtube_video_meta, try_fetch_youtube_video_meta
from remastered.web.youtube_thumb_cache import (
    THUMB_CACHE_CONTROL,
    THUMB_PLACEHOLDER_SVG,
    PLACEHOLDER_CACHE_CONTROL,
    ensure_thumb_cached,
    is_valid_youtube_video_id,
    thumb_cache_path,
)
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
    "Without signing in, remaster results last only for your current browser visit; "
    "reference tab, preset, and YouTube URL choices are remembered in your browser "
    "(uploaded reference files are not). "
    "Signed-in accounts keep jobs for the retention period and remember your last "
    "source and reference choices, including uploaded reference files stored with "
    "your account. "
    "If you sign up, we also store email and a password hash."
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


@fastapi_application.get("/api/youtube/meta")
async def youtube_video_meta(url: str = "") -> JSONResponse:
    raw = (url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="URL is required.")
    try:
        meta = await run_in_threadpool(fetch_youtube_video_meta, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = meta.as_dict()
    ws = _web_settings()
    payload["max_source_duration_seconds"] = ws.max_source_duration_seconds
    if meta.duration_seconds is not None:
        payload["exceeds_source_limit"] = (
            meta.duration_seconds > ws.max_source_duration_seconds
        )
    return JSONResponse(payload)


@fastapi_application.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "privacy.html",
        _privacy_template_context(),
    )


@fastapi_application.get("/opensource", response_class=HTMLResponse)
def opensource_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "opensource.html",
        {**_privacy_template_context(), **_opensource_template_context()},
    )


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


def require_identity(request: Request, store: JobStore = Depends(get_store)) -> RequestIdentity:
    return ensure_identity_with_anon_visit(request, store)


def require_account(identity: RequestIdentity = Depends(require_identity)) -> RequestIdentity:
    if not identity.account_id:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return identity


def _reference_prefs_for_identity(
    identity: RequestIdentity,
    store: JobStore,
    ws: WebSettings,
) -> dict[str, str] | None:
    if not identity.account_id:
        return None
    raw = store.get_reference_prefs(identity.account_id)
    if not raw:
        return None
    prefs = ReferencePrefs(
        tab=raw["tab"],
        preset_name=raw["preset_name"],
        preset_library=raw["preset_library"],
        youtube_url=raw["youtube_url"],
        upload_filename=raw["upload_filename"],
        source_youtube_url=raw.get("source_youtube_url", ""),
    )
    owner_id = identity.account_id
    shared_refs = _list_reference_files(ws.references_path)
    user_refs = _list_reference_files(_owner_references_dir(owner_id))
    sanitized = sanitize_prefs_for_owner(
        prefs,
        shared_refs=shared_refs,
        user_refs=user_refs,
        user_refs_dir=_owner_references_dir(owner_id),
    )
    return sanitized.to_client_dict()


def _save_reference_prefs(
    store: JobStore,
    account_id: str,
    prefs: ReferencePrefs,
    ws: WebSettings,
) -> ReferencePrefs:
    shared_refs = _list_reference_files(ws.references_path)
    user_refs = _list_reference_files(_owner_references_dir(account_id))
    sanitized = sanitize_prefs_for_owner(
        prefs,
        shared_refs=shared_refs,
        user_refs=user_refs,
        user_refs_dir=_owner_references_dir(account_id),
    )
    store.set_reference_prefs(
        account_id,
        tab=sanitized.tab,
        preset_name=sanitized.preset_name,
        preset_library=sanitized.preset_library,
        youtube_url=sanitized.youtube_url,
        upload_filename=sanitized.upload_filename,
        source_youtube_url=sanitized.source_youtube_url,
    )
    return sanitized


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


@fastapi_application.get("/api/reference-prefs")
def get_reference_prefs_api(
    identity: RequestIdentity = Depends(require_account),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    ws = _web_settings()
    prefs = _reference_prefs_for_identity(identity, store, ws)
    return JSONResponse(prefs or {})


@fastapi_application.put("/api/reference-prefs")
async def put_reference_prefs_api(
    request: Request,
    identity: RequestIdentity = Depends(require_account),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    prefs = ReferencePrefs.from_client_dict(body)
    if not prefs:
        raise HTTPException(status_code=400, detail="Invalid reference preferences.")
    ws = _web_settings()
    assert identity.account_id
    existing = prefs_from_store_row(store.get_reference_prefs(identity.account_id))
    merged = merge_client_prefs(existing, prefs)
    saved = _save_reference_prefs(store, identity.account_id, merged, ws)
    return JSONResponse(saved.to_client_dict())


@fastapi_application.post("/api/reference-upload")
async def reference_upload_api(
    identity: RequestIdentity = Depends(require_account),
    reference_upload: UploadFile | None = File(None),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    if not reference_upload or not reference_upload.filename:
        raise HTTPException(status_code=400, detail=CHOOSE_REFERENCE_FILE)
    assert identity.account_id
    owner_ref_dir = _owner_references_dir(identity.account_id)
    safe_name = Path(reference_upload.filename).name
    dest = owner_ref_dir / safe_name
    body = await reference_upload.read()

    def _write_upload() -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)

    await run_in_threadpool(_write_upload)
    ws = _web_settings()
    existing = prefs_from_store_row(store.get_reference_prefs(identity.account_id))
    merged = with_active_tab(
        existing,
        "upload",
        upload_filename=safe_name,
    )
    saved = _save_reference_prefs(store, identity.account_id, merged, ws)
    return JSONResponse(
        {"uploadFilename": safe_name, "prefs": saved.to_client_dict()}
    )


@fastapi_application.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 429:
        return HTMLResponse(
            f"<h1>429</h1><p>{exc.detail}</p><p><a href='/'>Back to home</a></p>",
            status_code=429,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _enrich_job(job: dict, store: JobStore) -> dict:
    data = enrich_job_ui(job, store=store)
    data["status_display"] = job_status_display(job, store)
    return data


def _privacy_template_context() -> dict[str, int | str]:
    ws = _web_settings()
    return {
        "privacy_retention_days": ws.job_retention_days,
        "privacy_notice": PRIVACY_NOTICE,
        "privacy_updated": "2026-10-24",
    }


def _opensource_template_context() -> dict[str, str]:
    return {"opensource_updated": "2026-10-26"}


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
        "reference_prefs": _reference_prefs_for_identity(identity, store, ws),
        "stage_labels": STAGE_LABELS,
        "privacy_notice": PRIVACY_NOTICE,
        "rights_notice_lines": rights_notice_lines(ws.job_retention_days),
        "limits": ws,
        **_auth_template_context(identity),
        **_privacy_template_context(),
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
    reference_source: str = Form("preset"),
    reference_library: str = Form("shared"),
    reference_upload: UploadFile | None = File(None),
    reference_youtube_url: str = Form(""),
    reference_youtube_title: str = Form(""),
    reference_saved_upload: str = Form(""),
    stereo_width_mode: str = Form(DEFAULT_STEREO_WIDTH_MODE),
) -> RedirectResponse:
    ip = client_ip(request)
    ws = _web_settings()
    reference_path: Path | str | None = None
    reference_source_type = "preset"
    ref_youtube_url: str | None = None
    ref_video_id: str | None = None
    ref_youtube_title: str | None = (reference_youtube_title or "").strip() or None
    owner_id = identity.job_owner_id
    owner_ref_dir = _owner_references_dir(owner_id)

    youtube_url = normalize_youtube_url(youtube_url)

    mode = (reference_source or "preset").strip().lower()
    if mode not in REFERENCE_SOURCE_MODES:
        mode = "preset"

    saved_upload_name = ""
    if mode == "upload":
        if reference_upload and reference_upload.filename:
            safe_name = Path(reference_upload.filename).name
            dest = owner_ref_dir / safe_name
            body = await reference_upload.read()

            def _write_upload() -> None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)

            await run_in_threadpool(_write_upload)
            reference_path = dest
            reference_source_type = "upload"
            saved_upload_name = safe_name
        elif reference_saved_upload.strip() and identity.account_id:
            safe_name = Path(reference_saved_upload).name
            dest = owner_ref_dir / safe_name
            if dest.is_file():
                reference_path = dest
                reference_source_type = "upload"
                saved_upload_name = safe_name
            else:
                raise HTTPException(status_code=400, detail=CHOOSE_REFERENCE_FILE)
        else:
            raise HTTPException(status_code=400, detail=CHOOSE_REFERENCE_FILE)
    elif mode == "youtube":
        ref_url_raw = (reference_youtube_url or "").strip()
        if not ref_url_raw:
            raise HTTPException(status_code=400, detail=ENTER_REFERENCE_YOUTUBE_URL)
        try:
            ref_youtube_url, ref_video_id = parse_reference_youtube_url(ref_url_raw)
            ensure_reference_differs_from_source(youtube_url, ref_youtube_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reference_path = youtube_reference_path_key(ref_video_id)
        reference_source_type = "youtube"
        if not ref_youtube_title:
            meta = await run_in_threadpool(try_fetch_youtube_video_meta, ref_youtube_url)
            if meta:
                ref_youtube_title = meta.title
    else:
        name = Path(reference_name).name
        if name:
            if reference_library == "user":
                candidate = owner_ref_dir / name
            else:
                candidate = ws.references_path / name
            if candidate.is_file():
                reference_path = candidate
        if reference_path is None:
            raise HTTPException(status_code=400, detail=SELECT_PRESET_REFERENCE)
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
        reference_source_type=reference_source_type,
        reference_youtube_url=ref_youtube_url,
        reference_video_id=ref_video_id,
        reference_youtube_title=ref_youtube_title,
    )
    store.append_log(job_id, "Job added to the queue.")
    worker.enqueue(job_id)
    if identity.account_id:
        existing = prefs_from_store_row(store.get_reference_prefs(identity.account_id))
        if mode == "preset":
            merged = with_active_tab(
                existing,
                "preset",
                preset_name=Path(reference_name).name if reference_name else "",
                preset_library=reference_library or "shared",
            )
        elif mode == "youtube":
            merged = with_active_tab(
                existing,
                "youtube",
                youtube_url=(reference_youtube_url or "").strip(),
            )
        else:
            merged = with_active_tab(
                existing,
                "upload",
                upload_filename=saved_upload_name,
            )
        merged = with_active_tab(merged, merged.tab, source_youtube_url=youtube_url)
        _save_reference_prefs(store, identity.account_id, merged, ws)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@fastapi_application.delete("/api/jobs/{job_id}")
async def delete_job_api(
    job_id: str,
    identity: RequestIdentity = Depends(require_identity),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    job = await run_in_threadpool(
        store.get_job_for_access,
        job_id,
        account_id=identity.account_id,
        anon_id=identity.anon_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    settings = resolve_settings()
    work_dir = settings.work_dir

    def _delete() -> None:
        delete_job_media_files(job, work_dir)
        store.delete_job(job_id)

    await run_in_threadpool(_delete)
    return JSONResponse({"ok": True})


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
            **_auth_template_context(identity),
            **media_ctx,
            **_privacy_template_context(),
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
        **media_ctx,
        **_auth_template_context(identity),
        **_privacy_template_context(),
    }
    # HTMX: 286 stops hx-trigger polling (see htmx.org/docs/#polling)
    status_code = 286 if job["status"] in TERMINAL_JOB_STATUSES else status.HTTP_200_OK
    return templates.TemplateResponse(
        request,
        "partials/job_panel_poll.html",
        ctx,
        status_code=status_code,
    )


@fastapi_application.get("/media/thumb/{video_id}.jpg")
async def youtube_thumb_image(video_id: str) -> Response:
    """Cached YouTube thumbnail (same-origin for job list and previews)."""
    vid = (video_id or "").strip()
    if not is_valid_youtube_video_id(vid):
        raise HTTPException(status_code=404, detail="Invalid video id.")
    settings = resolve_settings()
    work_dir = settings.work_dir

    def _serve() -> Response:
        path = thumb_cache_path(work_dir, vid)
        if path.is_file() and path.stat().st_size > 0:
            return FileResponse(
                path,
                media_type="image/jpeg",
                headers={"Cache-Control": THUMB_CACHE_CONTROL},
            )
        if ensure_thumb_cached(work_dir, vid) and path.is_file():
            return FileResponse(
                path,
                media_type="image/jpeg",
                headers={"Cache-Control": THUMB_CACHE_CONTROL},
            )
        return Response(
            content=THUMB_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": PLACEHOLDER_CACHE_CONTROL},
        )

    return await run_in_threadpool(_serve)


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
