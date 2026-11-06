from contextlib import asynccontextmanager

import os

import httpx
import pytest

from app.adapters import mock as mock_mod
from app.adapters.base import register_adapter, unregister_adapter
from app.core.config import Settings
from app.main import create_app


@asynccontextmanager
async def make_client(**overrides):
    cfg = dict(
        database_url=os.environ.get("TRACKDECK_TEST_DB", "sqlite+aiosqlite:///:memory:"),
        adapter_backoff_base=0.001,
        adapter_max_retries=3,
        cors_origins=[],
    )
    cfg.update(overrides)
    app = create_app(Settings(**cfg))
    async with app.router.lifespan_context(app):
        if "memory" not in cfg["database_url"]:
            from sqlalchemy import text
            async with app.state.engine.begin() as conn:
                await conn.execute(text("TRUNCATE transfer_tracks, transfers RESTART IDENTITY CASCADE"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


@pytest.fixture(autouse=True)
def _clean_mock_state():
    mock_mod.PLAYLISTS.clear()
    yield
    mock_mod.PLAYLISTS.clear()
    for name in ("flaky", "locked", "writefail", "down"):
        unregister_adapter(name)


@pytest.fixture
def register():
    """register(name, MockDestination) -> the same instance is returned for every build_adapter call."""

    def _register(name, instance):
        register_adapter(name, lambda creds, settings: _with_token(instance, creds))
        return instance

    def _with_token(instance, creds):
        instance.token = creds.get("access_token")
        return instance

    return _register


async def settle(app):
    await app.state.runner.join()
