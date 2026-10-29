from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from remastered.web.auth_jwt import AUTH_COOKIE_NAME, issue_token
from remastered.web.auth_service import AuthError, AuthService
from remastered.web.auth_settings import AuthSettings, auth_cookie_secure
from remastered.web.db import JobStore
from remastered.web.auth_redirect import (
    login_url,
    post_auth_redirect_target,
    safe_next_path,
    signup_url,
)
from remastered.web.identity import client_ip, resolve_identity
from remastered.web.settings import WebSettings
from remastered.web.signup_source import (
    clear_signup_source_tracking,
    resolve_signup_source_for_new_account,
)

router = APIRouter()


def _auth_service(request: Request) -> AuthService:
    store: JobStore = request.app.state.store
    return AuthService(store)


def _set_auth_cookie(
    response: RedirectResponse,
    *,
    user: dict,
    ws: WebSettings,
    auth: AuthSettings,
) -> None:
    token = issue_token(
        user_id=user["id"],
        login_id=user["login_id"],
        settings=auth,
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=auth_cookie_secure(ws.public_base_url),
        samesite="lax",
        max_age=auth.jwt_expiry_seconds,
        path="/",
    )


def register_auth_routes(
    app,
    templates: Jinja2Templates,
    privacy_notice: str,
) -> None:
    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str | None = None) -> HTMLResponse:
        identity = resolve_identity(request)
        next_path = safe_next_path(next)
        if identity.account_id:
            return RedirectResponse(
                post_auth_redirect_target(next_path),
                status_code=303,
            )
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "privacy_notice": privacy_notice,
                "auth_user": None,
                "error": None,
                "next": next_path,
                "signup_href": signup_url(next_path),
            },
        )

    @router.post("/login")
    def login_submit(
        request: Request,
        login_id: str = Form(...),
        password: str = Form(...),
        next: str = Form(""),
        auth: AuthService = Depends(_auth_service),
    ):
        identity = resolve_identity(request)
        ws = WebSettings()
        auth_settings = AuthSettings()
        try:
            user = auth.login(login_id, password, identity.anon_id)
        except AuthError as exc:
            next_path = safe_next_path(next)
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "privacy_notice": privacy_notice,
                    "auth_user": None,
                    "error": exc.message,
                    "login_id": login_id,
                    "next": next_path,
                    "signup_href": signup_url(next_path),
                },
                status_code=exc.status_code,
            )
        response = RedirectResponse(
            post_auth_redirect_target(safe_next_path(next)),
            status_code=303,
        )
        _set_auth_cookie(response, user=user, ws=ws, auth=auth_settings)
        return response

    @router.get("/signup", response_class=HTMLResponse)
    def signup_page(request: Request, next: str | None = None) -> HTMLResponse:
        identity = resolve_identity(request)
        next_path = safe_next_path(next)
        if identity.account_id:
            return RedirectResponse(
                post_auth_redirect_target(next_path),
                status_code=303,
            )
        auth_settings = AuthSettings()
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "privacy_notice": privacy_notice,
                "auth_user": None,
                "resend_cooldown_seconds": auth_settings.verification_resend_cooldown_seconds,
                "next": next_path,
                "login_href": login_url(next_path),
            },
        )

    @router.post("/auth/send-verification")
    def send_verification(
        request: Request,
        login_id: str = Form(...),
        auth: AuthService = Depends(_auth_service),
    ):
        try:
            auth.send_verification_code(login_id, client_ip(request))
        except AuthError as exc:
            return JSONResponse({"message": exc.message}, status_code=exc.status_code)
        except ValueError as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    @router.post("/signup")
    def signup_submit(
        request: Request,
        login_id: str = Form(...),
        password: str = Form(...),
        password_confirm: str = Form(...),
        verification_code: str = Form(...),
        next: str = Form(""),
        signup_source: str = Form(""),
        auth: AuthService = Depends(_auth_service),
    ):
        identity = resolve_identity(request)
        ws = WebSettings()
        auth_settings = AuthSettings()
        next_path = safe_next_path(next)
        resolved_signup_source = resolve_signup_source_for_new_account(
            request, signup_source
        )
        if password != password_confirm:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {
                    "privacy_notice": privacy_notice,
                    "auth_user": None,
                    "error": "Passwords do not match.",
                    "login_id": login_id,
                    "resend_cooldown_seconds": auth_settings.verification_resend_cooldown_seconds,
                    "next": next_path,
                    "login_href": login_url(next_path),
                },
                status_code=400,
            )
        try:
            user = auth.signup(
                login_id=login_id,
                password=password,
                verification_code=verification_code,
                anon_id=identity.anon_id,
                signup_source=resolved_signup_source,
            )
        except AuthError as exc:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {
                    "privacy_notice": privacy_notice,
                    "auth_user": None,
                    "error": exc.message,
                    "login_id": login_id,
                    "resend_cooldown_seconds": auth_settings.verification_resend_cooldown_seconds,
                    "next": next_path,
                    "login_href": login_url(next_path),
                },
                status_code=exc.status_code,
            )
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "signup.html",
                {
                    "privacy_notice": privacy_notice,
                    "auth_user": None,
                    "error": str(exc),
                    "login_id": login_id,
                    "resend_cooldown_seconds": auth_settings.verification_resend_cooldown_seconds,
                    "next": next_path,
                    "login_href": login_url(next_path),
                },
                status_code=400,
            )
        response = RedirectResponse(
            post_auth_redirect_target(next_path),
            status_code=303,
        )
        _set_auth_cookie(response, user=user, ws=ws, auth=auth_settings)
        clear_signup_source_tracking(response, request)
        return response

    @router.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(AUTH_COOKIE_NAME, path="/")
        return response

    app.include_router(router)
