"""Reproducible offline CICIoT2023 calibration experiments.

Run from ``backend/``:

    python -m scripts.evaluate_offline_model --limit-rows 50000

The script never touches the live MQTT detector or active stream artifact.  It
writes a JSON evidence file and, with ``--save-artifact``, saves the selected
offline model through the normal ``save_offline_outcome`` path.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from sklearn.ensemble import IsolationForest

from app.core.config import PROJECT_DIR, settings
from app.data import dataset_loader as dl
from app.ml import train_model
from app.ml.offline_calibration import (
    binary_metrics,
    calibrate_threshold,
    category_detection_rates,
    stratified_split_indices,
    threshold_policy_comparison,
)

DEFAULT_DATASET = PROJECT_DIR / "datasets" / "ciciot2023_train.parquet"
DEFAULT_OUTPUT = PROJECT_DIR / "docs" / "offline_model_optimization_results.json"


def _load_prepared(dataset: Path, *, limit_rows: int, label_column: str,
                   seed: int):
    frame, entry = dl.load_frame(
        str(dataset), limit_rows=limit_rows or None, seed=seed,
    )
    X_df, y, used = dl.prepare_dataset_matrix(
        frame, label_column=label_column,
    )
    if y is None:
        raise SystemExit("The selected dataset has no usable binary labels")
    X = X_df.to_numpy(dtype=np.float32)
    categories = None
    for column in ("attack_class", "Attack Category", "Label"):
        if column in frame.columns:
            categories = frame.loc[X_df.index, column].astype(str).to_numpy()
            break
    return frame, entry, X, y.to_numpy(dtype=int), used, categories


def _category_report(categories, y, preds) -> Dict[str, Any]:
    if categories is None:
        return {}
    return category_detection_rates(categories, y, preds)


def _fit_and_evaluate(
    X: np.ndarray,
    y: np.ndarray,
    categories,
    *,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    test_idx: np.ndarray,
    contamination: float,
    seed: int,
    min_precision: float,
    max_fpr,
    n_estimators: int,
) -> Dict[str, Any]:
    train_benign = y[train_idx] == 0
    if int(train_benign.sum()) < 10:
        raise SystemExit("Not enough benign training rows for a benign-only fit")
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        max_samples="auto",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X[train_idx][train_benign])

    validation_scores = -model.decision_function(X[val_idx])
    test_scores = -model.decision_function(X[test_idx])
    selection = calibrate_threshold(
        y[val_idx], validation_scores,
        min_precision=min_precision, max_fpr=max_fpr,
    )
    preds = (test_scores >= selection.threshold).astype(int)
    metrics = binary_metrics(y[test_idx], preds, test_scores)
    metrics.update({
        "contamination": contamination,
        "fit_rows": int(train_benign.sum()),
        "threshold": round(selection.threshold, 6),
        "threshold_selection": selection.as_dict(),
        "validation": selection.validation_metrics,
        "attack_categories": _category_report(
            categories[test_idx] if categories is not None else None,
            y[test_idx], preds,
        ),
    })
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--label-column", default="label")
    parser.add_argument("--limit-rows", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=settings.ml_random_seed)
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--min-precision", type=float,
                        default=settings.ml_offline_calibration_min_precision)
    parser.add_argument("--max-fpr", type=float,
                        default=settings.ml_offline_calibration_max_fpr)
    parser.add_argument("--baseline-json", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--save-artifact", action="store_true")
    args = parser.parse_args()

    frame, entry, X, y, used, categories = _load_prepared(
        args.dataset, limit_rows=args.limit_rows,
        label_column=args.label_column, seed=args.seed,
    )
    train_idx, val_idx, test_idx = stratified_split_indices(y, seed=args.seed)
    print(
        f"rows={len(y)} features={len(used)} train={len(train_idx)} "
        f"val={len(val_idx)} test={len(test_idx)} "
        f"attack_rate={float(np.mean(y)):.4f}"
    )

    contamination_results: Dict[str, Dict[str, Any]] = {}
    for contamination in (0.01, 0.03, 0.05, 0.10):
        result = _fit_and_evaluate(
            X, y, categories,
            train_idx=train_idx,
            val_idx=val_idx,
            test_idx=test_idx,
            contamination=contamination,
            seed=args.seed,
            min_precision=args.min_precision,
            max_fpr=args.max_fpr,
            n_estimators=args.n_estimators,
        )
        contamination_results[f"{contamination:.2f}"] = result
        print(
            f"contamination={contamination:.2f} "
            f"precision={result['precision']:.4f} "
            f"recall={result['recall']:.4f} f1={result['f1']:.4f} "
            f"fpr={result['false_positive_rate']:.4f} "
            f"roc_auc={result.get('roc_auc', float('nan')):.4f}"
        )

    # Threshold trade-offs are selected on validation and measured on test.
    policy_model = IsolationForest(
        n_estimators=args.n_estimators,
        contamination=settings.ml_contamination,
        max_samples="auto",
        random_state=args.seed,
        n_jobs=-1,
    )
    policy_model.fit(X[train_idx][y[train_idx] == 0])
    policy_validation = -policy_model.decision_function(X[val_idx])
    policy_test = -policy_model.decision_function(X[test_idx])
    policies = [
        {"name": "max_f1_unconstrained", "min_precision": 0.0},
        {"name": "max_f1_precision_0.90", "min_precision": 0.90},
        {"name": "max_f1_precision_0.95", "min_precision": 0.95},
        {"name": "max_f1_precision_0.97", "min_precision": 0.97},
        {"name": "max_f1_precision_0.99", "min_precision": 0.99},
        {"name": "max_f1_fpr_0.10", "min_precision": 0.0, "max_fpr": 0.10},
        {"name": "max_f1_fpr_0.05", "min_precision": 0.0, "max_fpr": 0.05},
    ]
    policy_results = threshold_policy_comparison(
        y[val_idx], policy_validation, y[test_idx], policy_test,
        policies=policies,
    )

    # Same-split comparison with the old rule: fit on all training rows and
    # set the threshold from the 5% training-score quantile.
    baseline_model = IsolationForest(
        n_estimators=args.n_estimators,
        contamination=settings.ml_contamination,
        max_samples="auto",
        random_state=args.seed,
        n_jobs=-1,
    )
    baseline_model.fit(X[train_idx])
    baseline_threshold = float(np.quantile(
        baseline_model.decision_function(X[train_idx]),
        settings.ml_flag_quantile,
    ))
    baseline_test_raw = baseline_model.decision_function(X[test_idx])
    baseline_preds = (baseline_test_raw < baseline_threshold).astype(int)
    baseline_same_split = binary_metrics(
        y[test_idx], baseline_preds, -baseline_test_raw,
    )
    baseline_same_split.update({
        "threshold": round(baseline_threshold, 6),
        "fit_rows": int(len(train_idx)),
        "protocol": (
            "same 60/20/20 split, but fitted on all training rows with the "
            "legacy training-quantile threshold"
        ),
    })

    selected = contamination_results[f"{settings.ml_contamination:.2f}"]
    artifact_info: Dict[str, Any] = {}
    if args.save_artifact:
        outcome = train_model.train(
            dataset="parquet",
            dataset_path=str(args.dataset),
            label_column=args.label_column,
            limit_rows=args.limit_rows,
            seed=args.seed,
            offline_fit_on_benign=True,
            calibration_min_precision=args.min_precision,
            calibration_max_fpr=args.max_fpr,
        )
        path = train_model.save_offline_outcome(outcome)
        artifact_info = {
            "version": outcome.version,
            "path": str(path),
            "config_path": outcome.artifact_config_path,
            "metrics": outcome.metrics,
        }
        print(f"saved offline artifact: {path}")

    evidence: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(args.dataset),
        "dataset_name": entry.name,
        "limit_rows": args.limit_rows,
        "seed": args.seed,
        "features": list(used),
        "feature_count": len(used),
        "rows": int(len(y)),
        "attack_rate": round(float(np.mean(y)), 6),
        "split": {
            "protocol": "stratified 60/20/20 train/validation/test",
            "train_rows": int(len(train_idx)),
            "validation_rows": int(len(val_idx)),
            "test_rows": int(len(test_idx)),
        },
        "calibration": {
            "min_precision": args.min_precision,
            "max_fpr": args.max_fpr,
            "strategy": "max_f1",
        },
        "contamination_experiments": contamination_results,
        "baseline_same_split": baseline_same_split,
        "selected_contamination": settings.ml_contamination,
        "selected_test_metrics": selected,
        "threshold_policies": policy_results,
        "artifact": artifact_info,
    }
    if args.baseline_json and args.baseline_json.exists():
        baseline = json.loads(args.baseline_json.read_text(encoding="utf-8"))
        evidence["baseline"] = {
            "version": baseline.get("version"),
            "metrics": baseline.get("metrics", {}),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, default=str), encoding="utf-8",
    )
    print(f"wrote evidence: {args.output}")


if __name__ == "__main__":
    main()
