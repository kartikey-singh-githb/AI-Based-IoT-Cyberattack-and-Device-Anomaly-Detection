"""Model status, training and dataset information."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.db.database import get_db
from app.ml import train_model
from app.schemas.common import (
    DatasetInfo,
    DatasetList,
    ModelInfo,
    TrainRequest,
    TrainResponse,
)
from app.services import model_info_service
from app.data import dataset_loader as dl

router = APIRouter(tags=["model"])
log = get_logger("api.model")


@router.get("/model", response_model=ModelInfo, summary="Model status and config")
def model_status(session: Session = Depends(get_db)) -> ModelInfo:
    info = model_info_service.model_info(session)
    return ModelInfo(**info)


@router.get("/model/history", summary="Previous training runs")
def model_history(
    limit: int = 20,
    session: Session = Depends(get_db),
) -> dict:
    rows = model_info_service.list_history(session, limit=min(max(limit, 1), 100))
    return {
        "items": [
            {
                "version": r.version,
                "algorithm": r.algorithm,
                "trained_at": r.trained_at.isoformat() + "Z" if r.trained_at else None,
                "is_active": r.is_active,
                "training_rows": r.training_rows,
                "feature_count": len(r.feature_names),
                "feature_space": r.feature_space,
                "contamination": r.contamination,
                "threshold": r.threshold,
                "dataset_name": r.dataset_name,
                "dataset_kind": r.dataset_kind,
                "metrics": r.metrics,
            }
            for r in rows
        ]
    }


@router.post("/model/train", response_model=TrainResponse,
             summary="Train (or retrain) the anomaly model")
def train(payload: TrainRequest, session: Session = Depends(get_db)) -> TrainResponse:
    try:
        outcome = train_model.train(
            dataset=payload.dataset,
            csv_path=payload.csv_path,
            contamination=payload.contamination,
            threshold=payload.threshold,
            label_column=payload.label_column,
        )
    except (ValueError, dl.DatasetError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Training failed")
        raise HTTPException(status_code=500, detail=f"Training failed: {exc}")

    model_info_service.train_and_activate(session, outcome)
    session.commit()

    return TrainResponse(
        status="trained",
        version=outcome.version,
        training_rows=outcome.training_rows,
        feature_count=outcome.feature_count,
        features=outcome.feature_names,
        contamination=outcome.contamination,
        threshold=outcome.threshold,
        dataset_name=outcome.dataset_name,
        dataset_kind=outcome.dataset_kind,
        metrics=outcome.metrics,
        metrics_are_measured=outcome.metrics_are_measured,
        message=outcome.message,
    )


@router.get("/datasets", response_model=DatasetList, summary="Supported datasets")
def datasets() -> DatasetList:
    items = []
    for entry in dl.list_datasets():
        items.append(DatasetInfo(
            id=entry.id,
            name=entry.name,
            kind="synthetic" if entry.kind == "synthetic" else "public",
            description=entry.description,
            source=entry.source,
            download_page=entry.download_page,
            license=entry.license,
            format=entry.format,
            features=entry.features,
            preprocessing=entry.preprocessing,
            available=entry.available,
            local_path=str(entry.path) if entry.available else None,
            rows=entry.rows(),
            size_bytes=entry.size_bytes(),
            notes=entry.notes,
        ))
    return DatasetList(items=items)


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo,
            summary="Dataset details")
def dataset_detail(dataset_id: str) -> DatasetInfo:
    try:
        entry = dl.get_dataset(dataset_id)
    except dl.DatasetError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DatasetInfo(
        id=entry.id,
        name=entry.name,
        kind="synthetic" if entry.kind == "synthetic" else "public",
        description=entry.description,
        source=entry.source,
        download_page=entry.download_page,
        license=entry.license,
        format=entry.format,
        features=entry.features,
        preprocessing=entry.preprocessing,
        available=entry.available,
        local_path=str(entry.path) if entry.available else None,
        rows=entry.rows(),
        size_bytes=entry.size_bytes(),
        notes=entry.notes,
    )


@router.post("/datasets/generate-sample", summary="Regenerate the synthetic sample CSV")
def generate_sample(
    n_devices: int = 8,
    rows_per_device: int = 400,
    seed: int = 42,
) -> dict:
    from app.data.synthetic import write_sample_csv, label_distribution
    from app.core.config import PROJECT_DIR

    n_devices = max(2, min(n_devices, 32))
    rows_per_device = max(50, min(rows_per_device, 5000))

    path = PROJECT_DIR / "data" / "datasets" / "synthetic_sample.csv"
    write_sample_csv(path, n_devices=n_devices, rows_per_device=rows_per_device,
                     seed=seed)
    import pandas as pd

    frame = pd.read_csv(path)
    return {
        "status": "generated",
        "path": str(path),
        "rows": int(frame.shape[0]),
        "columns": list(frame.columns),
        "label_distribution": label_distribution(frame),
        "source": "synthetic",
        "note": "Synthetic demo data generated by this project.",
    }
