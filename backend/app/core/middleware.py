"""Production security middlewares: HTTPS redirect, HSTS, security headers, error masking."""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

_ERROR_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Error</title></head>
<body style="font-family:sans-serif;padding:2rem">
<h1>Something went wrong</h1>
<p>Please try again or contact support.</p>
</body></html>"""


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP → HTTPS and add HSTS in production."""

    async def dispatch(self, request: Request, call_next):
        if settings.is_production and request.url.scheme == "http":
            https_url = str(request.url).replace("http://", "https://", 1)
            return RedirectResponse(https_url, status_code=301)
        response = await call_next(request)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    _CSP = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none';"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Content-Security-Policy"] = self._CSP
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


class ProductionErrorMiddleware(BaseHTTPMiddleware):
    """Hide stack traces from users in production."""

    async def dispatch(self, request: Request, call_next):
        if not settings.is_production:
            return await call_next(request)
        try:
            return await call_next(request)
        except Exception:
            return HTMLResponse(content=_ERROR_HTML, status_code=500)
