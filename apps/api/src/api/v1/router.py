"""Aggregates all v1 sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.v1 import proposals

router = APIRouter(prefix="/api/v1")
router.include_router(proposals.router)
