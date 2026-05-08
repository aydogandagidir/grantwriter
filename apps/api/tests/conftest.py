"""Shared pytest fixtures for the API test suite."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.main import create_app

# asyncpg's IO transport does not support Windows' default ProactorEventLoop
# (it expects SelectorEventLoop semantics). pytest-asyncio honours the policy
# set at conftest import time, so switch here before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
