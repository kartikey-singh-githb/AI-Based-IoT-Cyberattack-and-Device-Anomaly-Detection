# Offline CICIoT2023 Model Optimization

Measured comparison of the original offline Isolation Forest with a
benign-fitted, validation-calibrated model. All figures are produced by
`backend/scripts/evaluate_offline_model.py` and stored in
`docs/offline_model_optimization_results.json`.

## 1. Executive summary

The original offline model was not detecting attacks. It was fitting the full
CICIoT2023 sample, where 85.0% of rows are attacks, so Isolation Forest learned
attack traffic as the normal baseline. Its threshold was then set to the 5%
training-score quantile, which produced a 5% alert rate against an 85% attack
prevalence.

The optimized pipeline fits only benign training rows and selects the alert
threshold on a separate validation split. On the untouched test split it
raises F1 from **0.0952 to 0.9106** and recall from **0.0505 to 0.8718**.

| Metric | Legacy full-fit rule | Optimized benign-fit rule | Change |
|---|---:|---:|---:|
| Accuracy | 0.1843 | 0.8545 | +0.6702 |
| Balanced accuracy | 0.4972 | 0.8141 | +0.3169 |
| Precision | 0.8363 | 0.9531 | +0.1168 |
| Recall | 0.0505 | 0.8718 | +0.8213 |
| F1 | 0.0952 | 0.9106 | +0.8154 |
| ROC-AUC | 0.3317 | 0.8898 | +0.5581 |
| PR-AUC | 0.8030 | 0.9758 | +0.1728 |
| False-positive rate | 0.0561 | 0.2437 | +0.1876 |
| Predicted-positive rate | 0.0513 | 0.7777 | +0.7264 |

Both rows use the same stratified 60/20/20 split, the same 50,000-row seed-42
sample, and the same 10,000-row test set. The legacy row uses the old
full-training fit plus the 5% training-quantile threshold. The optimized row
uses benign-only fitting plus a validation-selected attack-score cutoff of
`-0.060914`.

The earlier saved artifact (`if-20261031-132946`) reported F1 `0.0904`,
recall `0.0478`, and ROC-AUC `0.3553` on its own 12,500-row hold-out. The
same-split comparison above is the fairer before/after measurement.

## 2. Root cause evidence

Diagnostic on the 50,000-row seed-42 sample:

* Attack prevalence: `0.85016`.
* Mean `decision_function` score: benign `0.06806598`, attack `0.08530449`.
* ROC-AUC when treating lower score as attack: `0.3607077`.
* ROC-AUC when treating higher score as attack: `0.6392923`.
* PR-AUC when treating lower score as attack: `0.8107487`.

Lower `decision_function` is anomalous. The model gave attacks *higher*
scores than benign traffic because majority attack rows defined the learned
baseline. The legacy threshold then flagged only the lowest 5% of the combined
training distribution, almost all of which were benign. This is why precision
looked acceptable while recall collapsed to about 5%.

The attack score direction itself was implemented correctly; the model was
learned on the wrong class distribution.

## 3. Method

1. Load a reproducible 50,000-row sample of `ciciot2023_train.parquet` with
   seed 42.
2. Prepare the existing 39 numeric flow features; labels and `attack_class`
   remain evaluation-only columns.
3. Split rows with stratification into 60% train, 20% validation, 20% test.
4. Fit Isolation Forest on **benign rows from the training split only**
   (`4,495` rows).
5. Score the validation split and choose the threshold that maximises F1
   subject to a validation precision floor of `0.95`. The default is
   configurable. If no candidate satisfies the floor, calibration falls back to
   unconstrained maximum F1 and records that fact.
6. Freeze the threshold, score the test split once, and report measured
   precision, recall, F1, ROC-AUC, PR-AUC, false-positive rate, confusion
   matrix, and per-category detection rates.

The test split is not used for fitting, threshold selection, or severity-band
selection.

Threshold orientation: the calibration helpers use attack scores
(`-decision_function`), where higher means more anomalous. `AnomalyDetector`
stores and compares raw `decision_function` scores, where lower means more
anomalous, so the selected attack cutoff is negated before it is persisted.
Thus the attack-score cutoff `-0.060914` corresponds to the persisted detector
threshold `+0.060914`; both produce the same predictions. Thresholds in the
experiment tables below are attack-score cutoffs.

## 4. Contamination experiments

Contamination `0.01`, `0.03`, `0.05`, and `0.10` all produced the same test
predictions once the threshold was re-calibrated on validation. This is
expected: in scikit-learn, `contamination` changes the estimator offset, not
the isolation-tree ranking. Validation calibration absorbs the offset.

| Contamination | Attack-score threshold | Precision | Recall | F1 | FPR | ROC-AUC |
|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | -0.108133 | 0.9531 | 0.8718 | 0.9106 | 0.2437 | 0.8898 |
| 0.03 | -0.076300 | 0.9531 | 0.8718 | 0.9106 | 0.2437 | 0.8898 |
| 0.05 | -0.060914 | 0.9531 | 0.8718 | 0.9106 | 0.2437 | 0.8898 |
| 0.10 | -0.035104 | 0.9531 | 0.8718 | 0.9106 | 0.2437 | 0.8898 |

Contamination is therefore not a useful performance lever for this calibrated
offline model. The selected artifact keeps `contamination=0.05`.

## 5. Threshold trade-offs

All thresholds below were selected on validation and measured on test.

| Policy | Attack-score threshold | Precision | Recall | F1 | FPR | Flagged rate |
|---|---:|---:|---:|---:|---:|---:|
| Max F1, unconstrained | -0.118583 | 0.8929 | 0.9638 | 0.9270 | 0.6562 | 0.9177 |
| Max F1, precision >= 0.90 | -0.107782 | 0.9042 | 0.9497 | 0.9264 | 0.5708 | 0.8929 |
| Max F1, precision >= 0.95 (selected) | -0.060914 | 0.9531 | 0.8718 | 0.9106 | 0.2437 | 0.7777 |
| Max F1, precision >= 0.97 | -0.029790 | 0.9724 | 0.7179 | 0.8260 | 0.1155 | 0.6277 |
| Max F1, precision >= 0.99 | 0.003932 | 0.9862 | 0.6402 | 0.7764 | 0.0507 | 0.5519 |
| Max F1, FPR <= 0.10 | -0.023394 | 0.9760 | 0.7042 | 0.8181 | 0.0981 | 0.6134 |
| Max F1, FPR <= 0.05 | -0.004147 | 0.9842 | 0.6602 | 0.7903 | 0.0601 | 0.5703 |

The unconstrained F1 maximum flags 91.8% of all traffic and is not operationally
useful despite the slightly higher F1. The saved model uses the 0.95 precision
floor: about 24% FPR for 87% recall. If false positives are more costly, use
`calibration_min_precision=0.99` or `calibration_max_fpr=0.10`; the expected
trade-off is visible in the table.

## 6. Attack-category detection

Selected attack-score cutoff `-0.060914`, test split:

| Category | Rows | Detected | Detection rate |
|---|---:|---:|---:|
| Benign | 1,498 | 365 | 0.2437 (FPR) |
| BruteForce | 91 | 54 | 0.5934 |
| DDoS | 3,397 | 3,365 | 0.9906 |
| DoS | 1,481 | 1,471 | 0.9932 |
| Mirai | 1,120 | 1,114 | 0.9946 |
| Recon | 1,509 | 974 | 0.6455 |
| Spoofing | 732 | 348 | 0.4754 |
| Web-based | 172 | 86 | 0.5000 |

The model is strong on volumetric categories (DDoS, DoS, Mirai) and weak on
Spoofing, Web-based, and reconnaissance flows. The latter categories are not
solved by threshold tuning; they need features or models that represent their
flow signatures more directly.

## 7. Leakage audit

* Test rows are excluded from model fitting by construction.
* The threshold is selected on validation rows only; test labels are used only
  for the final measurement.
* The artifact is fitted on the 60% training split, not refitted on the full
  sample after calibration.
* The existing preprocessing path computes imputation medians, drops constant
  columns, and selects wide-dataset columns before the split. On this CICI
  sample that has no measurable effect: 39 numeric features (below the
  60-feature cap), zero missing values, zero infinities, and zero constant
  columns. There is therefore no measured preprocessing leakage here.
  For future datasets with missing values, constant columns, or more than 60
  numeric columns, preprocessing statistics should be fitted on the training
  split only. That refactor is not required for the measured CICI result.
* The live MQTT detector and active stream artifact were not touched.

## 8. Artifact and reproducibility

* Artifact: `backend/app/ml/artifacts/offline/if-20261031-143305.joblib`
* Config: `backend/app/ml/artifacts/offline/if-20261031-143305.json`
* Feature space: `dataset` (offline only)
* Features: 39
* Fit rows: 4,495 benign training rows
* Detector threshold: `+0.060914`
* Attack-score cutoff: `-0.060914`
* Validation precision floor: `0.95`
* Test F1: `0.9106`
* Test recall: `0.8718`
* Test FPR: `0.2437`

Verification: reloading the saved artifact and replaying the 10,000 test rows
reproduces the stored metrics exactly (F1 `0.910621`; TN `1133`, FP `365`,
FN `1090`, TP `7412`).

Reproduce:

```powershell
cd backend
python -m scripts.evaluate_offline_model --limit-rows 50000 --save-artifact --baseline-json app\ml\artifacts\offline\if-20261031-132946.json
```

Configurable through the training API:

```json
{
  "dataset": "parquet",
  "dataset_path": "D:/New folder (2)/PS_310/datasets/ciciot2023_train.parquet",
  "label_column": "label",
  "limit_rows": 50000,
  "offline_fit_on_benign": true,
  "calibration_min_precision": 0.95,
  "calibration_max_fpr": null
}
```

Environment equivalents:
`P310_ML_OFFLINE_FIT_ON_BENIGN`,
`P310_ML_OFFLINE_CALIBRATION_MIN_PRECISION`, and
`P310_ML_OFFLINE_CALIBRATION_MAX_FPR`.

## 9. Limitations and next steps

1. The 24% false-positive rate at the selected precision floor is high because
   benign and attack flow distributions overlap. Use the threshold trade-off
   table to choose an operating point.
2. Spoofing, Web-based, BruteForce, and Recon detection remain weak. Improving
   those categories requires feature/model work, not just calibration.
3. The result is measured on a 50,000-row reproducible sample, not the full
   1.07-million-row file. Re-run the script with `--limit-rows 0` to evaluate
   the complete file.
4. The preprocessing leakage risk for other datasets remains documented in
   section 7 and should be addressed before claiming broad generalization.
