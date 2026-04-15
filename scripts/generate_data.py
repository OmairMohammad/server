from __future__ import annotations
import csv, json, math, random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
CLIENT_DATA = BASE_DIR / 'client' / 'src' / 'data' / 'mockData.js'
SERVER_DATA = BASE_DIR / 'server' / 'data'
FRONTEND_DATA = BASE_DIR / 'client' / 'src' / 'data' / 'mockData.generated.js'
TMP_JSON = SERVER_DATA / '_source_mock.json'

SERVER_DATA.mkdir(parents=True, exist_ok=True)

# Dump the existing JS seed module to JSON through Node so we preserve UI shape.
import subprocess
node_script = f"""
import mod from 'file://{CLIENT_DATA.as_posix()}';
"""
# ESM named import in inline code
node_code = f"""
const mod = await import('file://{CLIENT_DATA.as_posix()}');
const payload = {{
  DEFAULT_ADMIN: mod.DEFAULT_ADMIN,
  seedUsers: mod.seedUsers,
  roleOptions: mod.roleOptions,
  sites: mod.sites,
  baseAssets: mod.baseAssets,
  complianceStandards: mod.complianceStandards,
  seedAuditLog: mod.seedAuditLog,
  transitionScenarios: mod.transitionScenarios
}};
console.log(JSON.stringify(payload));
"""
out = subprocess.check_output(['node', '--input-type=module', '-e', node_code], text=True)
source = json.loads(out)

random.seed(26)

sites = source['sites'] + [{"id": "SITE-04", "name": "Sunshine West Test Centre"}]
role_options = source['roleOptions']
users = source['seedUsers']
# Add one pending user for admin workflow realism
users.append({
    "id": "USR-PEND-007", "name": "Demo Pending User", "email": "pending.user@obrienenergy.com.au",
    "password": "User123!", "role": "Pending Approval", "desiredRole": "Engineer / Operator",
    "site": "Sunshine West Test Centre", "approved": False, "isActive": True,
    "createdAt": "2026-04-01 09:00", "notes": "Need access for condition monitoring demo."
})

base_assets = source['baseAssets']
compliance_standards = source['complianceStandards']
seed_audit = source['seedAuditLog']
transition_seed = source['transitionScenarios']

asset_type_codes = {
    'Boiler': 'BLR', 'Burner': 'BUR', 'Pump': 'PMP', 'Heat Exchanger': 'HTX',
    'Controller': 'CTL', 'Chiller': 'CHR', 'Economiser': 'ECO', 'Feedwater System': 'FWS'
}

name_variants = {
    'Boiler': ['Steam Boiler', 'Process Boiler', 'Backup Boiler', 'Hot Water Boiler'],
    'Burner': ['Gas Burner', 'Burner Unit', 'Combustion Burner'],
    'Pump': ['Feedwater Pump', 'Condensate Pump', 'Transfer Pump'],
    'Heat Exchanger': ['Heat Exchanger', 'Plate HX', 'Thermal HX'],
    'Controller': ['Combustion Controller', 'Boiler Controller'],
    'Chiller': ['Industrial Chiller', 'Cooling Chiller'],
    'Economiser': ['Economiser', 'Heat Recovery Economiser'],
    'Feedwater System': ['Feedwater System', 'Deaerator Train'],
}


def clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def make_history(base: int, trend: int) -> list[int]:
    vals = []
    for i in range(6):
        vals.append(int(clamp(base + trend * (i - 5) + random.randint(-2, 2), 18, 96)))
    vals.sort(reverse=True)
    return vals


def asset_assessment(asset: dict[str, Any]) -> dict[str, Any]:
    score = float(asset['efficiencyScore'])
    recent_faults = int(asset['recentFaults'])
    overdue_days = int(asset['overdueDays'])
    vibration = float(asset['vibration'])
    temperature = float(asset['temperature'])
    bearing_temp = float(asset['bearingTemp'])
    water_hardness = float(asset['waterHardness'])
    asset_age = int(asset['assetAge'])
    worker_certified = bool(asset['workerCertified'])
    worker_fatigue = asset['workerFatigue']
    worker_experience = int(asset['workerExperience'])
    stack_temp = float(asset['stackTemp'])

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
    if worker_fatigue == 'High':
        score -= 5
    elif worker_fatigue == 'Medium':
        score -= 2
    if worker_experience < 2:
        score -= 4
    health = int(clamp(round(score), 0, 100))

    anomaly_points = 0
    flags = []
    if vibration > 50:
        anomaly_points += 30; flags.append('Critical vibration anomaly')
    elif vibration > 30:
        anomaly_points += 15; flags.append('Elevated vibration detected')
    if temperature > 115:
        anomaly_points += 25; flags.append('Temperature exceeds safe threshold')
    elif temperature > 100:
        anomaly_points += 12; flags.append('Temperature approaching limit')
    if bearing_temp > 70:
        anomaly_points += 20; flags.append('Bearing overheat anomaly')
    elif bearing_temp > 55:
        anomaly_points += 10; flags.append('Bearing temperature elevated')
    if water_hardness > 200:
        anomaly_points += 15; flags.append('Water hardness scale risk')
    if recent_faults > 3:
        anomaly_points += 20; flags.append('Multiple fault event pattern')
    elif recent_faults > 1:
        anomaly_points += 10; flags.append('Recurring fault events')
    if stack_temp > 190:
        anomaly_points += 18; flags.append('Stack temperature anomaly – possible fouling')
    anomaly = int(min(100, anomaly_points))
    anomaly_level = 'Critical' if anomaly >= 50 else 'Moderate' if anomaly >= 25 else 'Normal'

    degradation_rate = max(0.5, (100 - float(asset['efficiencyScore'])) / 8)
    days_to_failure = 365 - degradation_rate * 12
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
    if worker_fatigue == 'High':
        days_to_failure -= 20
    days_to_failure = max(7, int(round(days_to_failure)))
    confidence = int(clamp(94 - (recent_faults * 4) + (6 if health >= 80 else 0) - (6 if anomaly >= 50 else 0), 55, 96))

    if health >= 80 and recent_faults == 0 and overdue_days == 0:
        recommended = 'Continue Monitoring'
        risk = 'Low'
        priority = 'Routine'
        status = 'Within Target'
    elif health < 50 or recent_faults >= 4 or overdue_days > 21 or anomaly >= 50:
        recommended = 'Service / Escalate'
        risk = 'High'
        priority = 'Immediate'
        status = 'Intervention Required'
    else:
        recommended = 'Inspect / Tune / Clean'
        risk = 'Medium'
        priority = 'Planned'
        status = 'Review Needed'

    explain = []
    if recent_faults:
        explain.append(f'{recent_faults} recent fault event(s)')
    if overdue_days:
        explain.append(f'maintenance overdue by {overdue_days} day(s)')
    if vibration > 30:
        explain.append('vibration above target band')
    if temperature > 100:
        explain.append('temperature trend elevated')
    if stack_temp > 180:
        explain.append('stack temperature indicates efficiency loss or fouling')
    if not worker_certified:
        explain.append('operator certification risk flagged')
    if not explain:
        explain.append('no major exceptions in current operating data')

    return {
        'healthScore': health,
        'riskLevel': risk,
        'recommendedAction': recommended,
        'priority': priority,
        'reviewStatus': 'Pending Review' if risk != 'Low' else 'Approved',
        'confidenceScore': confidence,
        'anomalyScore': anomaly,
        'anomalyLevel': anomaly_level,
        'anomalyFlags': flags or ['No anomalies detected'],
        'daysToFailure': days_to_failure,
        'keyFactors': explain,
        'supportingNotes': 'Recommendation generated from rules-based scoring, anomaly signals, and maintenance history.',
    }


def build_asset_from_template(template: dict[str, Any], index: int) -> dict[str, Any]:
    asset = dict(template)
    site = sites[index % len(sites)]['name']
    asset_type = asset['assetType']
    code = asset_type_codes.get(asset_type, asset_type[:3].upper())
    asset['site'] = site
    if index >= len(base_assets):
        seq = 500 + index
        asset['assetId'] = f"{code}-{seq}"
        prefix = random.choice(name_variants.get(asset_type, [asset['assetName']]))
        asset['assetName'] = f"{prefix} {index - len(base_assets) + 1}"
        asset['installYear'] = int(clamp(int(template['installYear']) + random.randint(-3, 3), 2012, 2025))
        asset['assetAge'] = 2026 - asset['installYear']
        asset['operatingHours'] = max(1800, int(template['operatingHours']) + random.randint(-3500, 4200))
        asset['maintenanceStrategy'] = random.choice(['Reactive', 'Preventative', 'Condition-Based', 'Predictive'])
        asset['temperature'] = round(clamp(float(template['temperature']) + random.randint(-18, 18), 25, 130), 1)
        asset['pressure'] = round(clamp(float(template['pressure']) + random.randint(-15, 15), 0, 100), 1)
        asset['vibration'] = round(clamp(float(template['vibration']) + random.randint(-18, 22), 0, 80), 1)
        asset['stackTemp'] = round(clamp(float(template['stackTemp']) + random.randint(-30, 32), 0, 230), 1)
        asset['steamOutput'] = round(clamp(float(template['steamOutput']) + random.randint(-120, 140), 0, 1200), 1)
        asset['fuelConsumption'] = round(clamp(float(template['fuelConsumption']) + random.randint(-18, 22), 0, 180), 1)
        asset['load'] = round(clamp(float(template['load']) + random.randint(-20, 16), 30, 100), 1)
        asset['energyInput'] = round(clamp(float(template['energyInput']) + random.randint(-120, 150), 0, 1500), 1)
        asset['energyOutput'] = round(clamp(float(template['energyOutput']) + random.randint(-120, 150), 0, 1200), 1)
        asset['efficiencyScore'] = int(clamp(float(template['efficiencyScore']) + random.randint(-22, 16), 28, 95))
        asset['recentFaults'] = int(clamp(int(template['recentFaults']) + random.randint(-1, 3), 0, 6))
        asset['overdueDays'] = int(clamp(int(template['overdueDays']) + random.randint(-5, 28), 0, 60))
        asset['criticality'] = random.choice(['Medium', 'High', 'Critical']) if asset['assetType'] != 'Controller' else 'High'
        asset['lastMaintenanceDate'] = f"2026-{random.randint(1,3):02d}-{random.randint(1,28):02d}"
        asset['workerCertified'] = random.choice([True, True, True, False])
        asset['workerFatigue'] = random.choice(['Low', 'Medium', 'High'])
        asset['workerExperience'] = random.randint(1, 8)
        asset['operatorObservation'] = random.choice([
            'Operating within expected band.',
            'Minor efficiency drift noticed during recent checks.',
            'Fault events increasing during high load periods.',
            'Operator reports unusual vibration pattern.',
            'Stack temperature trend should be reviewed.',
        ])
        asset['maintenanceHistory'] = random.choice([
            'PM completed on schedule with no significant issues.',
            'Inspection recommended during next planned shutdown.',
            'Recent work order noted recurring issue requiring review.',
            'Maintenance overdue and escalation advised if trends continue.',
        ])
        asset['waterHardness'] = int(clamp(int(template['waterHardness']) + random.randint(-30, 40), 0, 300))
        asset['bearingTemp'] = round(clamp(float(template['bearingTemp']) + random.randint(-14, 18), 0, 95), 1)
        asset['shaftSpeed'] = round(clamp(float(template['shaftSpeed']) + random.randint(-250, 250), 0, 3600), 1)
        asset['co2Emissions'] = round(clamp(float(template['co2Emissions']) + random.randint(-40, 45), 0, 320), 1)
        asset['noxLevel'] = round(clamp(float(template['noxLevel']) + random.randint(-10, 14), 0, 100), 1)
        asset['emissionCompliant'] = bool(asset['noxLevel'] <= 65 and asset['co2Emissions'] <= 230)
        asset['replacementCost'] = int(clamp(int(template['replacementCost']) + random.randint(-15000, 18000), 12000, 180000))
        asset['annualMaintenanceCost'] = int(clamp(int(template['annualMaintenanceCost']) + random.randint(-1500, 2200), 1200, 20000))
        base_hist = int(asset['efficiencyScore']) + random.randint(-3, 4)
        trend = -random.randint(0, 5)
        asset['historicalHealthScores'] = make_history(base_hist, trend)
    else:
        # Spread originals across sites deterministically for a fuller fleet while preserving key assets
        asset['site'] = site if index >= 3 else asset['site']
    assessment = asset_assessment(asset)
    asset.update({
        'healthScore': assessment['healthScore'],
        'riskLevel': assessment['riskLevel'],
        'reviewStatus': assessment['reviewStatus'],
        'confidenceScore': assessment['confidenceScore'],
        'recommendedAction': assessment['recommendedAction'],
        'priority': assessment['priority'],
        'keyFactors': assessment['keyFactors'],
        'supportingNotes': assessment['supportingNotes'],
    })
    return asset

assets: list[dict[str, Any]] = []
for i, template in enumerate(base_assets):
    assets.append(build_asset_from_template(template, i))
for i in range(len(base_assets), 36):
    template = random.choice(base_assets)
    assets.append(build_asset_from_template(template, i))

# De-duplicate any accidental IDs
seen = set()
for idx, asset in enumerate(assets):
    if asset['assetId'] in seen:
        code = asset_type_codes.get(asset['assetType'], asset['assetType'][:3].upper())
        asset['assetId'] = f"{code}-{700+idx}"
    seen.add(asset['assetId'])

# Maintenance history and condition series
maintenance_history = []
condition_data = []
alarm_faults = []
operator_observations = []
energy_emissions = []
training_compliance = []
recommendations = []
audit_log = list(seed_audit)
transition_scenarios = list(transition_seed)

def month_label(month_idx: int) -> str:
    month = (month_idx % 12) + 1
    return f"2025-{month:02d}-15"

for asset in assets:
    # 12 maintenance entries
    overdue_days = int(asset['overdueDays'])
    for month_idx in range(12):
        mh_id = f"MH-{asset['assetId']}-{month_idx+1:02d}"
        due_gap = max(0, overdue_days - (11 - month_idx) * 2)
        service_type = random.choice(['Inspection', 'Preventive Maintenance', 'Calibration', 'Cleaning', 'Condition Review'])
        maintenance_history.append({
            'history_id': mh_id,
            'asset_id': asset['assetId'],
            'service_date': month_label(month_idx),
            'service_type': service_type,
            'technician': random.choice(['Ali Ahmad', 'Abdul Mannan Mohammed', 'Ajay Kunwar', 'Contractor Team']),
            'downtime_hours': round(clamp(random.gauss(3 if service_type != 'Cleaning' else 5, 1.3), 0.5, 9), 1),
            'notes': random.choice([
                'Routine service completed.', 'Component wear observed; monitor closely.',
                'Minor tuning carried out.', 'Deferred non-critical work item to next cycle.'
            ]),
            'overdue_days_snapshot': due_gap,
        })

    # 8 recent condition records
    for seq in range(8):
        condition_data.append({
            'condition_id': f"CD-{asset['assetId']}-{seq+1:02d}",
            'asset_id': asset['assetId'],
            'temperature': round(clamp(asset['temperature'] + random.randint(-6, 6), 0, 140), 1),
            'pressure': round(clamp(asset['pressure'] + random.randint(-5, 5), 0, 110), 1),
            'vibration': round(clamp(asset['vibration'] + random.randint(-8, 8), 0, 85), 1),
            'bearing_temperature': round(clamp(asset['bearingTemp'] + random.randint(-5, 6), 0, 100), 1),
            'stack_temperature': round(clamp(asset['stackTemp'] + random.randint(-8, 8), 0, 230), 1),
            'efficiency_score': int(clamp(asset['efficiencyScore'] + random.randint(-6, 5), 20, 96)),
            'runtime_hours': max(0, int(asset['operatingHours']) - (7 - seq) * random.randint(40, 120)),
            'observation_date': f"2026-03-{seq+1:02d}",
        })

    # Alarm faults based on recentFaults
    for seq in range(max(0, int(asset['recentFaults']))):
        sev = random.choice(['Low', 'Medium', 'High']) if asset['riskLevel'] != 'High' else random.choice(['Medium', 'High', 'High'])
        alarm_faults.append({
            'fault_id': f"FLT-{asset['assetId']}-{seq+1:02d}",
            'asset_id': asset['assetId'],
            'alarm_type': random.choice(['Temperature Deviation', 'Vibration Alert', 'Pressure Instability', 'Efficiency Drift', 'Emissions Alert']),
            'severity': sev,
            'description': random.choice([
                'Trend exceeded recommended band.', 'Recurring event observed during operation.',
                'Inspection advised if issue persists.', 'Escalation threshold approached.'
            ]),
            'event_date': f"2026-03-{random.randint(1,28):02d}",
        })

    operator_observations.append({
        'observation_id': f"OBS-{asset['assetId']}",
        'asset_id': asset['assetId'],
        'operator_name': random.choice(['Ali Ahmad', 'Plant Operator A', 'Shift Supervisor B']),
        'comments': asset['operatorObservation'],
        'recorded_at': f"2026-03-{random.randint(10,28):02d} 09:30",
    })

    energy_emissions.append({
        'asset_id': asset['assetId'],
        'site': asset['site'],
        'steam_output': asset['steamOutput'],
        'fuel_use': asset['fuelConsumption'],
        'co2_emissions': asset['co2Emissions'],
        'nox_level': asset['noxLevel'],
        'thermal_efficiency': asset['efficiencyScore'],
        'emission_compliant': asset['emissionCompliant'],
    })

    training_compliance.append({
        'asset_id': asset['assetId'],
        'site': asset['site'],
        'worker_certified': asset['workerCertified'],
        'worker_experience_years': asset['workerExperience'],
        'worker_fatigue': asset['workerFatigue'],
        'training_compliance_flag': bool(asset['workerCertified'] and asset['workerFatigue'] != 'High'),
    })

    rec = asset_assessment(asset)
    recommendations.append({
        'recommendation_id': f"REC-{asset['assetId']}",
        'asset_id': asset['assetId'],
        'health_score': rec['healthScore'],
        'risk_level': rec['riskLevel'],
        'recommended_action': rec['recommendedAction'],
        'key_factors': ' | '.join(rec['keyFactors']),
        'supporting_notes': rec['supportingNotes'],
        'confidence': rec['confidenceScore'],
        'status': rec['reviewStatus'],
        'days_to_failure': rec['daysToFailure'],
        'priority': rec['priority'],
    })

# Audit: add a few more entries and align asset IDs
for idx, asset in enumerate(assets[:12], start=1):
    audit_log.append({
        'id': f"AUD-{1100 + idx}",
        'assetId': asset['assetId'],
        'reviewer': random.choice(['Omair Mohammad', 'Ali Ahmad', 'Abdul Mannan Mohammed', 'Hamza Murtuza']),
        'reviewerRole': random.choice(['Admin', 'Engineer / Operator', 'Maintenance Planner', 'Regulator / Auditor']),
        'decision': random.choice(['Approved', 'Modified with Comment', 'Escalated']),
        'comment': random.choice([
            'Action accepted for next maintenance window.',
            'Escalated due to combined asset and compliance risk.',
            'Modified to inspect before full service escalation.'
        ]),
        'timestamp': f"2026-03-{random.randint(18,31):02d} {random.randint(8,17):02d}:{random.randint(0,59):02d}",
        'standard': random.choice(['AS3788', 'AS4343', 'NGER', 'ISO55001']),
        'compliant': random.choice([True, True, True, False]),
    })

# Add generated transition scenarios for highest risk assets
high_assets = sorted([a for a in assets if a['riskLevel'] == 'High'], key=lambda x: x['healthScore'])[:6]
for idx, asset in enumerate(high_assets, start=1):
    current_strategy = asset['maintenanceStrategy']
    proposed_strategy = 'Predictive' if current_strategy != 'Predictive' else 'Condition-Based'
    transition_scenarios.append({
        'id': f"TS-{100+idx}",
        'assetId': asset['assetId'],
        'assetName': asset['assetName'],
        'currentStrategy': current_strategy,
        'proposedStrategy': proposed_strategy,
        'currentAnnualCost': int(asset['annualMaintenanceCost'] + asset['replacementCost'] * 0.08),
        'proposedAnnualCost': int(asset['annualMaintenanceCost'] * 0.72),
        'currentDowntimeHours': int(max(12, (100 - asset['healthScore']) * 1.8)),
        'proposedDowntimeHours': int(max(4, (100 - asset['healthScore']) * 0.6)),
        'co2Reduction': int(clamp((asset['co2Emissions'] * 0.18), 8, 45)),
        'implementationCost': int(asset['annualMaintenanceCost'] * 1.4),
        'paybackMonths': random.choice([12, 15, 18, 20, 24]),
        'riskReduction': f"{asset['riskLevel']} → {'Medium' if asset['riskLevel']=='High' else 'Low'}",
        'confidence': int(clamp(asset['confidenceScore'] - 4 + random.randint(0, 8), 70, 95)),
    })

# CSV writers

def write_csv(path: Path, rows: list[dict[str, Any]]):
    if not rows:
        return
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

write_csv(SERVER_DATA / 'users.csv', users)
write_csv(SERVER_DATA / 'sites.csv', sites)
write_csv(SERVER_DATA / 'assets.csv', assets)
write_csv(SERVER_DATA / 'maintenance_history.csv', maintenance_history)
write_csv(SERVER_DATA / 'condition_data.csv', condition_data)
write_csv(SERVER_DATA / 'alarm_faults.csv', alarm_faults)
write_csv(SERVER_DATA / 'operator_observations.csv', operator_observations)
write_csv(SERVER_DATA / 'recommendations.csv', recommendations)
write_csv(SERVER_DATA / 'audit_log.csv', audit_log)
write_csv(SERVER_DATA / 'energy_emissions.csv', energy_emissions)
write_csv(SERVER_DATA / 'training_compliance.csv', training_compliance)
write_csv(SERVER_DATA / 'transition_scenarios.csv', transition_scenarios)
write_csv(SERVER_DATA / 'compliance_standards.csv', compliance_standards)

# Frontend JS module (keeps current UI simple and stable)
frontend_payload = {
    'DEFAULT_ADMIN': next(u for u in users if u['role'] == 'Admin'),
    'seedUsers': users,
    'roleOptions': role_options,
    'sites': sites,
    'baseAssets': assets,
    'complianceStandards': compliance_standards,
    'seedAuditLog': audit_log,
    'transitionScenarios': transition_scenarios,
}

with FRONTEND_DATA.open('w', encoding='utf-8') as f:
    f.write('// Auto-generated from server/scripts/generate_data.py\n')
    for key, value in frontend_payload.items():
        f.write(f"export const {key} = ")
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write(';\n\n')
    f.write('export const currentUser = DEFAULT_ADMIN;\n')

summary = {
    'assets': len(assets),
    'sites': len(sites),
    'users': len(users),
    'maintenance_history': len(maintenance_history),
    'condition_data': len(condition_data),
    'alarm_faults': len(alarm_faults),
    'transition_scenarios': len(transition_scenarios),
}
print(json.dumps(summary, indent=2))
