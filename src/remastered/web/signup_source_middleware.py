from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from remastered.web.settings import WebSettings
from remastered.web.signup_source import (
    SIGNUP_SOURCE_COOKIE,
    apply_signup_source_cookie,
    capture_first_touch_signup_source,
    read_first_touch_signup_source,
)


class SignupSourceCaptureMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        captured = capture_first_touch_signup_source(request)
        stored_before = read_first_touch_signup_source(request)
        response = await call_next(request)
        ws = WebSettings()
        if captured:
            apply_signup_source_cookie(response, captured, public_base_url=ws.public_base_url)
        elif stored_before and not request.cookies.get(SIGNUP_SOURCE_COOKIE):
            apply_signup_source_cookie(
                response, stored_before, public_base_url=ws.public_base_url
            )
        return response
