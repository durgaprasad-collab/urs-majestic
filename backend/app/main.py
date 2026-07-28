import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import menu, orders, customers, menu_engineering, feedback, kpi, recon, ceo_brief, whatsapp_webhooks
from app.core.config import settings
from app.core.middleware import (
    HTTPSRedirectMiddleware,
    SecurityHeadersMiddleware,
    ProductionErrorMiddleware,
)
from app.web import routes as web_routes
from app.web import auth_routes, purchase_routes, mapping_routes, engine_routes, channel_upload_routes, recon_routes, daily_brief_routes, business_settings_routes, prep_routes, reorder_routes

app = FastAPI(
    title="URS Majestic",
    description="Restaurant ordering and analytics platform",
    version="0.1.0",
    # Hide /docs and /redoc in production — admin tool only
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

# ── Middleware stack (outermost first) ─────────────────────────────────────────
app.add_middleware(ProductionErrorMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(HTTPSRedirectMiddleware)

# CORS: admin origin + public site origin (for /api/feedback)
_cors_origins = [o for o in [settings.ADMIN_ORIGIN, settings.SITE_ORIGIN] if o]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

# Session: Secure+HttpOnly+SameSite=lax in production; http-compatible in dev
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    https_only=settings.is_production,
    same_site="lax",
)

# ── Static files ───────────────────────────────────────────────────────────────
_STATIC = os.path.join(os.path.dirname(__file__), "web", "static")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth_routes.router)
app.include_router(web_routes.router)
app.include_router(purchase_routes.router)
app.include_router(prep_routes.router)
app.include_router(reorder_routes.router)
app.include_router(mapping_routes.router)
app.include_router(engine_routes.router)
app.include_router(channel_upload_routes.router)
app.include_router(recon_routes.router)
app.include_router(daily_brief_routes.router)
app.include_router(business_settings_routes.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(customers.router)
app.include_router(menu_engineering.router)
app.include_router(feedback.router)
app.include_router(kpi.router)
app.include_router(recon.router)
app.include_router(ceo_brief.router)
app.include_router(whatsapp_webhooks.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
