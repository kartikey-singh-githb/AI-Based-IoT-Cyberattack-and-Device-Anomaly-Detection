"""Aggregated analytics endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common import AnalyticsSummary, OverviewStats
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewStats,
            summary="Dashboard overview counters")
def overview(session: Session = Depends(get_db)) -> OverviewStats:
    return analytics_service.overview(session)


@router.get("/summary", response_model=AnalyticsSummary,
            summary="Time-series and breakdown analytics")
def summary(
    hours: int = Query(24, ge=1, le=24 * 30),
    session: Session = Depends(get_db),
) -> AnalyticsSummary:
    return analytics_service.summary(session, hours=hours)


@router.get("/performance", summary="Detection outcome statistics")
def performance(session: Session = Depends(get_db)) -> dict:
    return analytics_service.detection_performance(session)


@router.get("/timeline", summary="Recent alert timeline")
def timeline(
    hours: int = Query(6, ge=1, le=24 * 7),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_db),
) -> dict:
    items = analytics_service.timeline(session, hours=hours, limit=limit)
    return {"items": items, "count": len(items)}
