"""Device registration and lookup endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common import (
    DeviceCreate,
    DeviceList,
    DeviceOut,
    DeviceUpdate,
)
from app.services import device_service

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=DeviceList, summary="List devices")
def list_devices(
    device_status: Optional[str] = Query(None, alias="status",
                                         description="Filter by device status"),
    device_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> DeviceList:
    items, total = device_service.list_devices(
        session, status=device_status, device_type=device_type,
        search=search, limit=limit, offset=offset,
    )
    return DeviceList(
        items=items,
        meta={"total": total, "limit": limit, "offset": offset},
    )


@router.get("/{device_id}", response_model=DeviceOut, summary="Device details")
def get_device(device_id: str, session: Session = Depends(get_db)) -> DeviceOut:
    out = device_service.get_device_out(session, device_id)
    if out is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device '{device_id}' not found",
        )
    return out


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED,
             summary="Register a device")
def create_device(payload: DeviceCreate,
                  session: Session = Depends(get_db)) -> DeviceOut:
    try:
        device = device_service.create_device(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    session.commit()
    return device_service.get_device_out(session, device.device_id)


@router.patch("/{device_id}", response_model=DeviceOut, summary="Update a device")
def update_device(device_id: str, payload: DeviceUpdate,
                  session: Session = Depends(get_db)) -> DeviceOut:
    device = device_service.get_device(session, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device_service.update_device(session, device, payload)
    session.commit()
    return device_service.get_device_out(session, device_id)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a device and its telemetry")
def delete_device(device_id: str, session: Session = Depends(get_db)) -> None:
    device = device_service.get_device(session, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device_service.delete_device(session, device)
    session.commit()
