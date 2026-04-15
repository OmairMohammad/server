from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split

MODEL_DIR = Path(__file__).resolve().parents[1] / 'models'
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
MODEL_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_PATH = MODEL_DIR / 'benchmark_summary.json'

FEATURES = [
    'temperature', 'pressure', 'vibration', 'bearingTemp', 'stackTemp', 'fuelConsumption',
    'steamOutput', 'load', 'efficiencyScore', 'recentFaults', 'overdueDays', 'assetAge',
    'waterHardness', 'co2Emissions', 'noxLevel'
]
TARGET_MAP = {'Low': 0, 'Medium': 1, 'High': 2}


def _prepare_dataset() -> pd.DataFrame:
    assets = pd.read_csv(DATA_DIR / 'assets.csv')
    if 'historicalHealthScores' in assets.columns:
        assets = assets.drop(columns=['historicalHealthScores'])
    assets['workerCertified'] = assets['workerCertified'].astype(str).str.lower().isin(['true', '1', 'yes']).astype(int)
    assets['risk_target'] = assets['riskLevel'].map(TARGET_MAP)
    return assets


def _fallback_summary(df: pd.DataFrame, error_message: str) -> dict[str, Any]:
    feature_ranges = []
    for feature in FEATURES[:8]:
        feature_ranges.append({'feature': feature, 'importance': round(float(df[feature].std()), 4)})
    return {
        'dataset': {'assets': len(df), 'features': FEATURES},
        'randomForest': {
            'accuracy': 0.0,
            'macroF1': 0.0,
            'topFeatures': feature_ranges,
            'note': 'Benchmark training skipped; fallback summary returned.'
        },
        'xgboost': {
            'accuracy': 0.0,
            'macroF1': 0.0,
            'topFeatures': feature_ranges,
            'note': 'XGBoost training skipped; fallback summary returned.'
        },
        'isolationForest': {
            'estimatedOutliers': int(max(1, round(len(df) * 0.18))),
            'contamination': 0.18,
            'note': 'Fallback anomaly estimate used.'
        },
        'positioning': {
            'productionDecisionEngine': 'Rules-based explainable scoring',
            'offlineBenchmarks': ['Random Forest', 'XGBoost', 'Isolation Forest'],
            'why': 'The uploaded project files repeatedly position the MVP as read-only, advisory-only, and explainable before using heavier predictive models.',
            'fallbackReason': error_message,
        }
    }


def train_benchmark_models(force: bool = False) -> dict[str, Any]:
    if SUMMARY_PATH.exists() and not force:
        return json.loads(SUMMARY_PATH.read_text())

    df = _prepare_dataset()
    try:
        X = df[FEATURES]
        y = df['risk_target']
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=26, stratify=y)

        rf = RandomForestClassifier(n_estimators=250, random_state=26, class_weight='balanced')
        rf.fit(X_train, y_train)
        rf_pred = rf.predict(X_test)

        try:
            from xgboost import XGBClassifier
            xgb = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.08,
                subsample=0.9,
                colsample_bytree=0.9,
                objective='multi:softmax',
                eval_metric='mlogloss',
                random_state=26,
            )
            xgb.fit(X_train, y_train)
            xgb_pred = xgb.predict(X_test)
            xgb_section = {
                'accuracy': round(float(accuracy_score(y_test, xgb_pred)), 4),
                'macroF1': round(float(f1_score(y_test, xgb_pred, average='macro')), 4),
                'topFeatures': sorted(
                    [{'feature': feature, 'importance': round(float(importance), 4)} for feature, importance in zip(FEATURES, xgb.feature_importances_)],
                    key=lambda x: x['importance'], reverse=True,
                )[:8],
            }
        except Exception as xgb_error:
            xgb_section = {
                'accuracy': 0.0,
                'macroF1': 0.0,
                'topFeatures': [],
                'note': f'XGBoost unavailable for local benchmark: {xgb_error}',
            }

        iso = IsolationForest(random_state=26, contamination=0.18)
        iso.fit(X)
        anomaly_pred = iso.predict(X)
        anomaly_outliers = int((anomaly_pred == -1).sum())

        summary = {
            'dataset': {'assets': len(df), 'features': FEATURES},
            'randomForest': {
                'accuracy': round(float(accuracy_score(y_test, rf_pred)), 4),
                'macroF1': round(float(f1_score(y_test, rf_pred, average='macro')), 4),
                'topFeatures': sorted(
                    [{'feature': feature, 'importance': round(float(importance), 4)} for feature, importance in zip(FEATURES, rf.feature_importances_)],
                    key=lambda x: x['importance'], reverse=True,
                )[:8],
            },
            'xgboost': xgb_section,
            'isolationForest': {
                'estimatedOutliers': anomaly_outliers,
                'contamination': 0.18,
                'note': 'Used for anomaly scoring benchmark on the synthetic O’Brien-style dataset.',
            },
            'positioning': {
                'productionDecisionEngine': 'Rules-based explainable scoring',
                'offlineBenchmarks': ['Random Forest', 'XGBoost', 'Isolation Forest'],
                'why': 'The uploaded project files repeatedly position the MVP as read-only, advisory-only, and explainable before using heavier predictive models.',
            },
        }
    except Exception as error:
        summary = _fallback_summary(df, str(error))

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    return summary
