from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.mock import register_defaults
from app.adapters.musicbrainz import register_musicbrainz
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.ratelimit import SlidingWindow
from app.db.session import init_db, make_engine, make_sessionmaker
from app.services.pipeline import JobRunner, purge_expired, recover_interrupted

log = logging.getLogger("trackdeck")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        await init_db(engine)
        sm = make_sessionmaker(engine)
        app.state.engine, app.state.sm = engine, sm
        app.state.runner = JobRunner(settings.max_concurrent_matching)
        recovered = await recover_interrupted(sm)
        if recovered:
            log.warning("marked %d interrupted transfer(s) as failed/resumable", recovered)

        async def purge_once() -> None:
            try:
                n = await purge_expired(sm, settings.retention_hours)
                if n:
                    log.info("purged %d expired transfer(s)", n)
            except Exception:  # noqa: BLE001
                log.exception("purge failed")

        async def janitor() -> None:
            while True:
                await asyncio.sleep(1800)
                await purge_once()

        await purge_once()  # at boot too: free hosts restart/sleep often, so a timer alone may never fire
        jan = asyncio.create_task(janitor(), name="janitor")
        yield
        jan.cancel()
        await app.state.runner.shutdown()
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.limiter = SlidingWindow()
    app.state.reranker = None  # plug in an LLM Reranker here (only consulted for medium-confidence matches)

    register_defaults(settings)
    register_musicbrainz(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Destination-Token"],
        expose_headers=["Content-Disposition", "X-Track-Count"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
