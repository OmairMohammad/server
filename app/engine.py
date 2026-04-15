from __future__ import annotations
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rules_based_score(asset: dict[str, Any]) -> int:
    score = to_float(asset.get("efficiencyScore") or asset.get("efficiency_score"), 0.0)
    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    overdue_days = to_int(asset.get("overdueDays") or asset.get("overdue_days"))
    vibration = to_float(asset.get("vibration"))
    temperature = to_float(asset.get("temperature"))
    bearing_temp = to_float(asset.get("bearingTemp") or asset.get("bearing_temperature"))
    water_hardness = to_float(asset.get("waterHardness") or asset.get("water_hardness"))
    asset_age = to_int(asset.get("assetAge") or asset.get("asset_age"))
    worker_certified = to_bool(asset.get("workerCertified") or asset.get("worker_certified"))
    worker_fatigue = asset.get("workerFatigue") or asset.get("worker_fatigue") or "Low"
    worker_experience = to_int(asset.get("workerExperience") or asset.get("worker_experience"), 0)

    if recent_faults >= 4:
        score -= 22
    elif recent_faults >= 2:
        score -= 12
    elif recent_faults >= 1:
        score -= 6

    if overdue_days > 30:
        score -= 20
    elif overdue_days > 14:
        score -= 12
    elif overdue_days > 7:
        score -= 7
    elif overdue_days > 0:
        score -= 3

    if vibration > 55:
        score -= 14
    elif vibration > 35:
        score -= 8
    elif vibration > 25:
        score -= 4

    if temperature > 115:
        score -= 12
    elif temperature > 105:
        score -= 7
    elif temperature > 95:
        score -= 3

    if bearing_temp > 70:
        score -= 10
    elif bearing_temp > 55:
        score -= 5

    if water_hardness > 200:
        score -= 8
    elif water_hardness > 160:
        score -= 4

    if asset_age > 10:
        score -= 8
    elif asset_age > 7:
        score -= 4

    if not worker_certified:
        score -= 6
    if worker_fatigue == "High":
        score -= 5
    elif worker_fatigue == "Medium":
        score -= 2
    if worker_experience < 2:
        score -= 4

    return int(clamp(round(score), 0, 100))


def anomaly_details(asset: dict[str, Any]) -> dict[str, Any]:
    anomaly_points = 0
    flags: list[str] = []
    vibration = to_float(asset.get("vibration"))
    temperature = to_float(asset.get("temperature"))
    bearing_temp = to_float(asset.get("bearingTemp") or asset.get("bearing_temperature"))
    water_hardness = to_float(asset.get("waterHardness") or asset.get("water_hardness"))
    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    stack_temp = to_float(asset.get("stackTemp") or asset.get("stack_temperature"))

    if vibration > 50:
        anomaly_points += 30
        flags.append("Critical vibration anomaly")
    elif vibration > 30:
        anomaly_points += 15
        flags.append("Elevated vibration detected")

    if temperature > 115:
        anomaly_points += 25
        flags.append("Temperature exceeds safe threshold")
    elif temperature > 100:
        anomaly_points += 12
        flags.append("Temperature approaching limit")

    if bearing_temp > 70:
        anomaly_points += 20
        flags.append("Bearing overheat anomaly")
    elif bearing_temp > 55:
        anomaly_points += 10
        flags.append("Bearing temperature elevated")

    if water_hardness > 200:
        anomaly_points += 15
        flags.append("Water hardness scale risk")

    if recent_faults > 3:
        anomaly_points += 20
        flags.append("Multiple fault event pattern")
    elif recent_faults > 1:
        anomaly_points += 10
        flags.append("Recurring fault events")

    if stack_temp > 190:
        anomaly_points += 18
        flags.append("Stack temperature anomaly – possible fouling")

    score = int(min(100, anomaly_points))
    level = "Critical" if score >= 50 else "Moderate" if score >= 25 else "Normal"
    return {"score": score, "level": level, "flags": flags or ["No anomalies detected"]}


def predict_failure(asset: dict[str, Any]) -> dict[str, Any]:
    days_to_failure = 365.0
    efficiency_score = to_float(asset.get("efficiencyScore") or asset.get("efficiency_score"), 0.0)
    vibration = to_float(asset.get("vibration"))
    bearing_temp = to_float(asset.get("bearingTemp") or asset.get("bearing_temperature"))
    water_hardness = to_float(asset.get("waterHardness") or asset.get("water_hardness"))
    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    overdue_days = to_int(asset.get("overdueDays") or asset.get("overdue_days"))
    asset_age = to_int(asset.get("assetAge") or asset.get("asset_age"))
    worker_certified = to_bool(asset.get("workerCertified") or asset.get("worker_certified"))
    worker_fatigue = asset.get("workerFatigue") or asset.get("worker_fatigue") or "Low"

    degradation_rate = max(0.5, (100 - efficiency_score) / 8)
    days_to_failure -= degradation_rate * 12

    if vibration > 50:
        days_to_failure -= 60
    elif vibration > 30:
        days_to_failure -= 25

    if bearing_temp > 70:
        days_to_failure -= 45
    elif bearing_temp > 55:
        days_to_failure -= 20

    if water_hardness > 200:
        days_to_failure -= 30

    days_to_failure -= recent_faults * 18
    days_to_failure -= overdue_days * 1.2
    days_to_failure -= max(0, asset_age - 5) * 8

    if not worker_certified:
        days_to_failure -= 15
    if worker_fatigue == "High":
        days_to_failure -= 20

    days_to_failure = max(7, round(days_to_failure))
    confidence = int(clamp(94 - (recent_faults * 4), 55, 94))
    return {"daysToFailure": int(days_to_failure), "confidence": confidence}


def select_strategy(asset: dict[str, Any]) -> dict[str, Any]:
    asset_age = to_int(asset.get("assetAge") or asset.get("asset_age"))
    criticality = asset.get("criticality", "Medium")
    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    overdue_days = to_int(asset.get("overdueDays") or asset.get("overdue_days"))

    strategies = [
        {"name": "Reactive", "score": 0},
        {"name": "Preventative", "score": 0},
        {"name": "Condition-Based", "score": 0},
        {"name": "Predictive", "score": 0},
    ]

    if asset_age <= 3:
        strategies[3]["score"] += 30
    elif asset_age <= 6:
        strategies[2]["score"] += 30
    elif asset_age <= 9:
        strategies[1]["score"] += 25
    else:
        strategies[0]["score"] += 10

    if criticality == "Critical":
        strategies[3]["score"] += 40
        strategies[2]["score"] += 20
    elif criticality == "High":
        strategies[2]["score"] += 35
        strategies[1]["score"] += 20
    else:
        strategies[1]["score"] += 30
        strategies[0]["score"] += 15

    if recent_faults >= 4:
        strategies[3]["score"] += 20
    elif recent_faults >= 2:
        strategies[2]["score"] += 20
    elif recent_faults == 0:
        strategies[3]["score"] += 15

    if overdue_days > 20:
        strategies[3]["score"] += 18
    elif overdue_days > 7:
        strategies[2]["score"] += 14
    elif overdue_days == 0:
        strategies[1]["score"] += 8

    chosen = max(strategies, key=lambda item: item["score"])
    return chosen


def build_explanation(asset: dict[str, Any], health_score: int, anomaly: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    factors: list[str] = []
    priority = "Routine"
    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    overdue_days = to_int(asset.get("overdueDays") or asset.get("overdue_days"))
    temperature = to_float(asset.get("temperature"))
    vibration = to_float(asset.get("vibration"))
    stack_temp = to_float(asset.get("stackTemp") or asset.get("stack_temperature"))
    worker_certified = to_bool(asset.get("workerCertified") or asset.get("worker_certified"))

    if recent_faults:
        factors.append(f"{recent_faults} recent fault event(s)")
    if overdue_days:
        factors.append(f"maintenance overdue by {overdue_days} day(s)")
    if vibration > 30:
        factors.append("vibration above target band")
    if temperature > 100:
        factors.append("temperature trend elevated")
    if stack_temp > 180:
        factors.append("stack temperature indicates efficiency loss or fouling")
    if not worker_certified:
        factors.append("operator certification risk flagged")
    if not factors:
        factors.append("no major exceptions in current operating data")

    if health_score < 50 or anomaly["level"] == "Critical":
        priority = "Immediate"
    elif health_score < 80 or anomaly["level"] == "Moderate":
        priority = "Planned"

    return {
        "factors": factors,
        "priority": priority,
        "supportingNotes": "Recommendation generated from rules-based scoring, anomaly signals, maintenance history, and human-review safeguards.",
        "rulesTriggered": anomaly["flags"] + [f"Preferred strategy: {strategy['name']}"] if anomaly["flags"] else [f"Preferred strategy: {strategy['name']}"]
    }


def assess_asset(asset: dict[str, Any]) -> dict[str, Any]:
    asset = dict(asset)
    health_score = rules_based_score(asset)
    anomaly = anomaly_details(asset)
    prediction = predict_failure(asset)
    strategy = select_strategy(asset)
    explain = build_explanation(asset, health_score, anomaly, strategy)

    recent_faults = to_int(asset.get("recentFaults") or asset.get("recent_faults"))
    overdue_days = to_int(asset.get("overdueDays") or asset.get("overdue_days"))

    if health_score >= 80 and recent_faults == 0 and overdue_days == 0:
        risk = "Low"
        recommendation = "Continue Monitoring"
        review_status = asset.get("reviewStatus") or asset.get("review_status") or "Approved"
    elif health_score < 50 or recent_faults >= 4 or overdue_days > 21 or anomaly["score"] >= 50:
        risk = "High"
        recommendation = "Service / Escalate"
        review_status = asset.get("reviewStatus") or asset.get("review_status") or "Pending Review"
    else:
        risk = "Medium"
        recommendation = "Inspect / Tune / Clean"
        review_status = asset.get("reviewStatus") or asset.get("review_status") or "Pending Review"

    confidence_score = int(clamp(prediction["confidence"] + (6 if health_score >= 80 else 0) - (6 if anomaly["level"] == "Critical" else 0), 55, 96))

    asset.update({
        "healthScore": health_score,
        "riskLevel": risk,
        "recommendedAction": recommendation,
        "confidenceScore": confidence_score,
        "reviewStatus": review_status,
        "anomaly": anomaly,
        "prediction": prediction,
        "strategyRecommendation": strategy,
        "explainability": explain,
    })
    return asset


def dashboard_metrics(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(assets)
    low = sum(1 for a in assets if a.get("riskLevel") == "Low")
    med = sum(1 for a in assets if a.get("riskLevel") == "Medium")
    high = sum(1 for a in assets if a.get("riskLevel") == "High")
    pending = sum(1 for a in assets if a.get("reviewStatus") == "Pending Review")
    open_recs = sum(1 for a in assets if a.get("recommendedAction") != "Continue Monitoring")
    return [
        {"label": "Total Assets", "value": total, "subtitle": "Assets currently monitored"},
        {"label": "Healthy Assets", "value": low, "subtitle": "Assets within target condition"},
        {"label": "Medium Risk Assets", "value": med, "subtitle": "Assets requiring inspection planning"},
        {"label": "High Risk Assets", "value": high, "subtitle": "Assets requiring service escalation"},
        {"label": "Open Recommendations", "value": open_recs, "subtitle": "Actions awaiting review"},
        {"label": "Pending Review", "value": pending, "subtitle": "Approvals pending"},
    ]
