"""The OlbosTax API application."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from olbostax_efile import get_provider
from olbostax_engine import ENGINE_VERSION, available_rule_sets
from olbostax_security import install as install_redaction

from .routers import admin, auth, efile, returns, taxpayers
from .settings import get_settings

__all__ = ["create_app"]

logger = logging.getLogger("olbostax")


def create_app() -> FastAPI:
    settings = get_settings()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Installed before anything else can log. Every handler gets both the
    # redacting filter and the redacting formatter, so a taxpayer identifier
    # cannot reach the log stream through a message, a structured field, or a
    # traceback.
    install_redaction()

    app = FastAPI(
        title="OlbosTax API",
        version="0.1.0",
        description=(
            "Tax preparation for a flat price. This API prepares and validates "
            "returns; it does not transmit them to any taxing authority."
        ),
        # The interactive docs expose every schema, including request bodies
        # that carry SSNs. Useful in development, unnecessary attack surface in
        # production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)

        # The API returns JSON only, so the CSP can be maximally restrictive:
        # nothing should ever be loaded or executed from an API response, and
        # a browser that is somehow rendering one should refuse.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
        )
        # Responses carry taxpayer data; no shared or disk cache should retain
        # them, including on a browser's back button.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Log the detail, return none of it.

        An unhandled exception's message can contain a database row, a query
        with parameters, or a value from the request body. Returning it to the
        client turns a bug into a disclosure. The correlation id is what lets
        support connect a user's report to the logged detail.
        """
        import uuid

        correlation_id = str(uuid.uuid4())
        logger.exception(
            "unhandled error path=%s correlation_id=%s", request.url.path, correlation_id
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Something went wrong on our end. Nothing you entered was lost.",
                "correlation_id": correlation_id,
            },
        )

    app.include_router(auth.router)
    app.include_router(taxpayers.router)
    app.include_router(returns.router)
    app.include_router(efile.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "engine_version": ENGINE_VERSION}

    @app.get("/api/v1/system/filing-readiness", tags=["system"])
    def filing_readiness() -> dict:
        """Whether OlbosTax can currently file a return, and why not.

        Public. A taxpayer choosing this product is entitled to know before
        entering any data that filing is not yet available -- and the honest
        answer is more useful to them than a checkout page that fails at the
        last step.
        """
        rule_sets = [
            {
                "jurisdiction": jurisdiction,
                "tax_year": year,
                "certification": certification.value,
                "filable": certification.is_filable,
            }
            for jurisdiction, year, certification in available_rule_sets()
        ]
        provider = get_provider(settings.efile_provider)
        can_transmit = provider.channel.is_real_filing

        blockers = []
        if not any(r["filable"] for r in rule_sets):
            blockers.append(
                "Tax rules for the supported years have not completed verification "
                "against official IRS and Oklahoma Tax Commission sources."
            )
        if not can_transmit:
            blockers.append(
                "No authorized e-file provider is integrated, so returns cannot be "
                "transmitted to a taxing authority."
            )

        return {
            "can_file": not blockers,
            "blockers": blockers,
            "rule_sets": rule_sets,
            "efile_channel": provider.channel.value,
            "price": settings.price_display,
        }

    return app


app = create_app()
