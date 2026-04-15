from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

import pandas as pd

from .engine import assess_asset, dashboard_metrics
from .db import CompatConnection, drop_table, get_columns, identifier, read_sql_frame, table_exists, write_df
from .security import hash_password, verify_password

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / 'data'
DB_LOCK = RLock()
ROLE_OPTIONS = [
    'Pending Approval',
    'Engineer / Operator',
    'Maintenance Planner',
    'Executive',
    'Regulator / Auditor',
    'Sustainability / Transition Lead',
    'Admin',
]
CSV_MAP = {
    'users': 'users.csv',
    'sites': 'sites.csv',
    'assets': 'assets.csv',
    'maintenance_history': 'maintenance_history.csv',
    'condition_data': 'condition_data.csv',
    'alarm_faults': 'alarm_faults.csv',
    'operator_observations': 'operator_observations.csv',
    'recommendations': 'recommendations.csv',
    'audit_log': 'audit_log.csv',
    'energy_emissions': 'energy_emissions.csv',
    'training_compliance': 'training_compliance.csv',
    'transition_scenarios': 'transition_scenarios.csv',
    'compliance_standards': 'compliance_standards.csv',
}


CUSTOM_SCHEMA = {
    'work_orders': '''
        CREATE TABLE IF NOT EXISTS work_orders (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            site TEXT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT NOT NULL,
            status TEXT NOT NULL,
            assignee TEXT,
            due_date TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT,
            completed_at TEXT,
            source TEXT
        )
    ''',
    'alerts': '''
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            asset_id TEXT,
            site TEXT,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0
        )
    ''',
    'user_preferences': '''
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT PRIMARY KEY,
            preferences_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''',
}


DEFAULT_PREFERENCES = {
    'highRiskAlerts': True,
    'maintenanceDue': True,
    'auditUpdates': False,
    'weeklyDigest': True,
    'escalationAlerts': True,
    'compactView': False,
    'showPredictions': True,
    'showConfidence': True,
    'showAnomaly': True,
    'defaultRiskFilter': 'All',
    'defaultSite': '',
}


def utcnow_str() -> str:
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M')


def _conn() -> CompatConnection:
    return CompatConnection()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y'}


def _maybe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _maybe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_user_row(user: dict[str, Any]) -> dict[str, Any]:
    row = dict(user)
    row['approved'] = _to_bool(row.get('approved'))
    row['isActive'] = _to_bool(row.get('isActive'))
    row.pop('password', None)
    row.pop('password_hash', None)
    return row


def _prepare_users_df(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    frame['password_hash'] = frame['password'].astype(str).apply(hash_password)
    frame['password'] = ''
    frame['approved'] = frame['approved'].astype(str).str.lower().isin(['true', '1', 'yes'])
    frame['isActive'] = frame['isActive'].astype(str).str.lower().isin(['true', '1', 'yes'])
    return frame


def _prepare_seed_frame(table: str, df: pd.DataFrame) -> pd.DataFrame:
    if table == 'users':
        return _prepare_users_df(df)
    return df


def _seed_db(_: CompatConnection | None = None) -> None:
    for table, filename in CSV_MAP.items():
        path = DATA_DIR / filename
        df = pd.read_csv(path)
        df = _prepare_seed_frame(table, df)
        write_df(df, table, if_exists='replace')


def _ensure_custom_tables(conn: CompatConnection) -> None:
    for ddl in CUSTOM_SCHEMA.values():
        conn.execute(ddl)
    conn.commit()


def _ensure_user_migration(conn: CompatConnection) -> None:
    cols = set(get_columns('users'))
    if 'password_hash' not in cols:
        conn.execute(f'ALTER TABLE {identifier("users")} ADD COLUMN {identifier("password_hash")} TEXT')

    users_df = read_sql_frame(f'SELECT * FROM {identifier("users")}')
    if users_df.empty:
        return
    if 'password_hash' not in users_df.columns:
        users_df['password_hash'] = ''
    mask = users_df['password_hash'].fillna('').astype(str).eq('')
    if mask.any():
        users_df.loc[mask, 'password_hash'] = users_df.loc[mask, 'password'].astype(str).apply(hash_password)
    if 'password' not in users_df.columns:
        users_df['password'] = ''
    users_df['password'] = ''
    write_df(users_df, 'users', if_exists='replace')


def _ensure_default_preferences(conn: CompatConnection) -> None:
    users = read_sql_frame(f'SELECT id, site FROM {identifier("users")}')
    pref_rows = []
    for _, row in users.iterrows():
        prefs = dict(DEFAULT_PREFERENCES)
        prefs['defaultSite'] = row.get('site') or ''
        pref_rows.append({'user_id': row['id'], 'preferences_json': json.dumps(prefs), 'updated_at': utcnow_str()})
    if not pref_rows:
        return
    current = read_sql_frame(f'SELECT user_id FROM {identifier("user_preferences")}')
    current_ids = set(current['user_id'].tolist()) if not current.empty else set()
    missing = [row for row in pref_rows if row['user_id'] not in current_ids]
    if missing:
        write_df(pd.DataFrame(missing), 'user_preferences', if_exists='append')


def init_db(force_reset: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if force_reset:
        for table in list(CSV_MAP.keys()) + list(CUSTOM_SCHEMA.keys()):
            drop_table(table)
    with DB_LOCK:
        with _conn() as conn:
            if force_reset or not table_exists('users'):
                _seed_db(conn)
            _ensure_custom_tables(conn)
            _ensure_user_migration(conn)
            _ensure_default_preferences(conn)
            sync_alerts(conn)


def reset_db() -> None:
    init_db(force_reset=True)


def _read_table(table: str) -> list[dict[str, Any]]:
    with DB_LOCK:
        frame = read_sql_frame(f'SELECT * FROM {identifier(table)}')
    return frame.to_dict(orient='records')


def get_roles() -> list[str]:
    return ROLE_OPTIONS


def get_users() -> list[dict[str, Any]]:
    return [_normalize_user_row(row) for row in _read_table('users')]


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with DB_LOCK:
        with _conn() as conn:
            row = conn.execute(f'SELECT * FROM {identifier("users")} WHERE id=?', (user_id,)).fetchone()
            return _normalize_user_row(dict(row)) if row else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with DB_LOCK:
        with _conn() as conn:
            row = conn.execute(f'SELECT * FROM {identifier("users")} WHERE lower(email)=?', (email.strip().lower(),)).fetchone()
            return dict(row) if row else None


def get_sites() -> list[dict[str, Any]]:
    return _read_table('sites')


def get_user_preferences(user_id: str) -> dict[str, Any]:
    with DB_LOCK:
        with _conn() as conn:
            row = conn.execute(f'SELECT preferences_json FROM {identifier("user_preferences")} WHERE user_id=?', (user_id,)).fetchone()
            if not row:
                user = get_user_by_id(user_id)
                prefs = dict(DEFAULT_PREFERENCES)
                prefs['defaultSite'] = user.get('site', '') if user else ''
                return prefs
            prefs = json.loads(row['preferences_json'])
            merged = dict(DEFAULT_PREFERENCES)
            merged.update(prefs)
            return merged


def update_user_preferences(user_id: str, preferences: dict[str, Any]) -> dict[str, Any]:
    current = get_user_preferences(user_id)
    current.update(preferences)
    with DB_LOCK:
        with _conn() as conn:
            conn.execute(
                '''
                INSERT INTO user_preferences (user_id, preferences_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET preferences_json=excluded.preferences_json, updated_at=excluded.updated_at
                ''',
                (user_id, json.dumps(current), utcnow_str()),
            )
            conn.commit()
    return current


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    row = get_user_by_email(email)
    if not row:
        return None
    if not verify_password(password, row.get('password_hash')):
        return {'error': 'Incorrect password.'}
    user = _normalize_user_row(row)
    if not user['isActive']:
        return {'error': 'Account has been deactivated.'}
    if not user['approved']:
        return {'error': 'Account is pending admin approval.'}
    return user


def signup(payload: dict[str, Any]) -> tuple[bool, str]:
    users = get_users()
    norm = payload['email'].strip().lower()
    if any(str(u['email']).lower() == norm for u in users):
        return False, 'Email already registered.'
    new_user = {
        'id': f"USR-{len(users)+1:03d}",
        'name': payload['name'].strip(),
        'email': norm,
        'password': '',
        'password_hash': hash_password(payload['password']),
        'role': 'Pending Approval',
        'desiredRole': payload['desiredRole'],
        'site': payload['site'],
        'approved': False,
        'isActive': True,
        'createdAt': payload['createdAt'],
        'notes': payload.get('notes', ''),
    }
    with DB_LOCK:
        with _conn() as conn:
            write_df(pd.DataFrame([new_user]), 'users', if_exists='append')
            prefs = dict(DEFAULT_PREFERENCES)
            prefs['defaultSite'] = payload['site']
            conn.execute(
                f'INSERT INTO {identifier("user_preferences")} (user_id, preferences_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET preferences_json=EXCLUDED.preferences_json, updated_at=EXCLUDED.updated_at',
                (new_user['id'], json.dumps(prefs), utcnow_str()),
            )
            conn.commit()
    return True, 'Account request submitted. Admin must approve before login.'


def update_user(user_id: str, **updates: Any) -> dict[str, Any] | None:
    allowed = {'name', 'role', 'desiredRole', 'site', 'approved', 'isActive', 'notes'}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return get_user_by_id(user_id)
    with DB_LOCK:
        with _conn() as conn:
            existing = conn.execute(f'SELECT * FROM {identifier("users")} WHERE id=?', (user_id,)).fetchone()
            if not existing:
                return None
            cols = ', '.join([f'{identifier(key)}=?' for key in updates.keys()])
            values = list(updates.values()) + [user_id]
            conn.execute(f'UPDATE {identifier("users")} SET {cols} WHERE id=?', values)
            conn.commit()
    return get_user_by_id(user_id)


def update_profile(user_id: str, *, name: str, site: str) -> dict[str, Any] | None:
    updated = update_user(user_id, name=name.strip(), site=site)
    if updated:
        prefs = get_user_preferences(user_id)
        if not prefs.get('defaultSite'):
            update_user_preferences(user_id, {'defaultSite': site})
        sync_alerts()
    return updated


def change_password(user_id: str, current_password: str, new_password: str) -> tuple[bool, str]:
    with DB_LOCK:
        with _conn() as conn:
            row = conn.execute(f'SELECT * FROM {identifier("users")} WHERE id=?', (user_id,)).fetchone()
            if not row:
                return False, 'User not found.'
            user = dict(row)
            if not verify_password(current_password, user.get('password_hash')):
                return False, 'Current password is incorrect.'
            conn.execute(f'UPDATE {identifier("users")} SET password_hash=?, password=? WHERE id=?', (hash_password(new_password), '', user_id))
            conn.commit()
    return True, 'Password updated successfully.'


def _cast_asset(row: dict[str, Any]) -> dict[str, Any]:
    asset = dict(row)
    if isinstance(asset.get('historicalHealthScores'), str):
        try:
            asset['historicalHealthScores'] = json.loads(asset['historicalHealthScores'])
        except json.JSONDecodeError:
            asset['historicalHealthScores'] = [int(x) for x in asset['historicalHealthScores'].strip('[]').split(',') if x.strip()]
    for key in ['workerCertified', 'emissionCompliant']:
        if key in asset:
            asset[key] = _to_bool(asset.get(key))
    numeric_keys = ['operatingHours', 'temperature', 'pressure', 'vibration', 'stackTemp', 'steamOutput', 'fuelConsumption', 'load', 'energyInput', 'energyOutput', 'efficiencyScore', 'recentFaults', 'overdueDays', 'assetAge', 'workerExperience', 'waterHardness', 'bearingTemp', 'shaftSpeed', 'co2Emissions', 'noxLevel', 'replacementCost', 'annualMaintenanceCost']
    for key in numeric_keys:
        if key in asset:
            asset[key] = _maybe_float(asset[key])
            if key not in {'temperature', 'pressure', 'vibration', 'stackTemp', 'steamOutput', 'fuelConsumption', 'load', 'energyInput', 'energyOutput', 'efficiencyScore', 'waterHardness', 'bearingTemp', 'shaftSpeed', 'co2Emissions', 'noxLevel'}:
                asset[key] = int(round(asset[key]))
    return asset


def get_assets(site: str | None = None, risk: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
    assets = [_cast_asset(row) for row in _read_table('assets')]
    assessed = [assess_asset(asset) for asset in assets]
    if site and site != 'All':
        assessed = [a for a in assessed if a.get('site') == site]
    if risk and risk != 'All':
        assessed = [a for a in assessed if a.get('riskLevel') == risk]
    if search:
        q = search.lower().strip()
        assessed = [a for a in assessed if q in a.get('assetId', '').lower() or q in a.get('assetName', '').lower() or q in a.get('site', '').lower() or q in a.get('assetType', '').lower()]
    return assessed


def get_asset(asset_id: str) -> dict[str, Any] | None:
    for asset in get_assets():
        if asset['assetId'] == asset_id:
            return asset
    return None


def get_dashboard(site: str | None = None) -> dict[str, Any]:
    assets = get_assets(site=site)
    chart = [
        {'name': 'Low Risk', 'value': sum(1 for a in assets if a['riskLevel'] == 'Low')},
        {'name': 'Medium Risk', 'value': sum(1 for a in assets if a['riskLevel'] == 'Medium')},
        {'name': 'High Risk', 'value': sum(1 for a in assets if a['riskLevel'] == 'High')},
    ]
    metrics = dashboard_metrics(assets)
    open_alerts = len([a for a in get_alerts(status='Open') if not site or a.get('site') == site])
    open_work_orders = len([w for w in get_work_orders(status='Open') if not site or w.get('site') == site])
    if metrics:
        metrics[-2]['helper'] = 'Actions awaiting review'
    return {'metrics': metrics, 'chartData': chart, 'assets': assets, 'openAlerts': open_alerts, 'openWorkOrders': open_work_orders}


def get_audit(asset_id: str | None = None) -> list[dict[str, Any]]:
    rows = _read_table('audit_log')
    if asset_id:
        rows = [row for row in rows if row.get('assetId') == asset_id]
    return sorted(rows, key=lambda x: x.get('timestamp', ''), reverse=True)


def add_audit_entry(payload: dict[str, Any]) -> dict[str, Any]:
    rows = get_audit()
    next_id = f"AUD-{1000 + len(rows) + 1}"
    entry = {
        'id': next_id,
        'assetId': payload['asset_id'],
        'reviewer': payload['reviewer'],
        'reviewerRole': payload['reviewer_role'],
        'decision': payload['decision'],
        'comment': payload.get('comment', ''),
        'timestamp': payload['timestamp'],
        'standard': payload.get('standard', 'AS3788'),
        'compliant': payload.get('compliant', True),
    }
    with DB_LOCK:
        with _conn() as conn:
            write_df(pd.DataFrame([entry]), 'audit_log', if_exists='append')
            if payload['decision'] == 'Approved':
                conn.execute('UPDATE assets SET "reviewStatus"=? WHERE "assetId"=?', ('Approved', payload['asset_id']))
            elif payload['decision'] == 'Escalated':
                conn.execute('UPDATE assets SET "reviewStatus"=? WHERE "assetId"=?', ('Pending Escalation', payload['asset_id']))
            else:
                conn.execute('UPDATE assets SET "reviewStatus"=? WHERE "assetId"=?', ('Pending Review', payload['asset_id']))
            conn.commit()
    sync_alerts()
    return entry


def get_recommendations(site: str | None = None) -> list[dict[str, Any]]:
    assets = get_assets(site=site)
    rows = []
    for asset in assets:
        rows.append({
            'assetId': asset['assetId'],
            'assetName': asset['assetName'],
            'site': asset['site'],
            'riskLevel': asset['riskLevel'],
            'recommendedAction': asset['recommendedAction'],
            'healthScore': asset['healthScore'],
            'priority': asset['explainability']['priority'],
            'confidence': asset['confidenceScore'],
            'keyFactors': asset['explainability']['factors'],
            'reviewStatus': asset['reviewStatus'],
            'daysToFailure': asset['prediction']['daysToFailure'],
            'strategy': asset['strategyRecommendation']['name'],
        })
    return rows


def get_training_records(site: str | None = None) -> list[dict[str, Any]]:
    rows = _read_table('training_compliance')
    mapped = []
    for row in rows:
        item = dict(row)
        item['worker_certified'] = _to_bool(item.get('worker_certified'))
        item['training_compliance_flag'] = _to_bool(item.get('training_compliance_flag'))
        if site and site != 'All' and item.get('site') != site:
            continue
        mapped.append(item)
    return mapped


def upsert_training_record(asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with DB_LOCK:
        with _conn() as conn:
            existing = conn.execute(f'SELECT * FROM {identifier("training_compliance")} WHERE asset_id=?', (asset_id,)).fetchone()
            asset = conn.execute('SELECT site FROM assets WHERE "assetId"=?', (asset_id,)).fetchone()
            site = payload.get('site') or (asset['site'] if asset else '')
            record = {
                'asset_id': asset_id,
                'site': site,
                'worker_certified': bool(payload.get('worker_certified')),
                'worker_experience_years': _maybe_int(payload.get('worker_experience_years')),
                'worker_fatigue': payload.get('worker_fatigue', 'Low'),
                'training_compliance_flag': bool(payload.get('training_compliance_flag')),
            }
            if existing:
                conn.execute(
                    '''
                    UPDATE training_compliance
                    SET site=?, worker_certified=?, worker_experience_years=?, worker_fatigue=?, training_compliance_flag=?
                    WHERE asset_id=?
                    ''',
                    (record['site'], record['worker_certified'], record['worker_experience_years'], record['worker_fatigue'], record['training_compliance_flag'], asset_id),
                )
            else:
                write_df(pd.DataFrame([record]), 'training_compliance', if_exists='append')
            conn.execute(
                '''
                UPDATE assets
                SET site=?, "workerCertified"=?, "workerFatigue"=?, "workerExperience"=?
                WHERE "assetId"=?
                ''',
                (record['site'], record['worker_certified'], record['worker_fatigue'], record['worker_experience_years'], asset_id),
            )
            conn.commit()
    sync_alerts()
    return record


def get_maintenance_history(asset_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    rows = _read_table('maintenance_history')
    if asset_id:
        rows = [row for row in rows if row.get('asset_id') == asset_id]
    rows = sorted(rows, key=lambda x: x.get('service_date', ''), reverse=True)
    if limit:
        rows = rows[:limit]
    return rows


def add_maintenance_entry(payload: dict[str, Any]) -> dict[str, Any]:
    rows = get_maintenance_history()
    next_id = f"MH-{payload['asset_id']}-{len(rows) + 1:03d}"
    entry = {
        'history_id': next_id,
        'asset_id': payload['asset_id'],
        'service_date': payload['service_date'],
        'service_type': payload['service_type'],
        'technician': payload['technician'],
        'downtime_hours': _maybe_float(payload.get('downtime_hours')),
        'notes': payload.get('notes', ''),
        'overdue_days_snapshot': _maybe_int(payload.get('overdue_days_snapshot')),
    }
    with DB_LOCK:
        with _conn() as conn:
            write_df(pd.DataFrame([entry]), 'maintenance_history', if_exists='append')
            conn.execute(
                '''
                UPDATE assets
                SET "lastMaintenanceDate"=?, "overdueDays"=?, "maintenanceHistory"=?
                WHERE "assetId"=?
                ''',
                (entry['service_date'], 0, entry['notes'] or 'Maintenance updated from persistent log.', payload['asset_id']),
            )
            conn.commit()
    sync_alerts()
    return entry


def get_work_orders(asset_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    with DB_LOCK:
        with _conn() as conn:
            rows = conn.execute(f'SELECT * FROM {identifier("work_orders")}').fetchall()
    if asset_id:
        rows = [row for row in rows if row.get('asset_id') == asset_id]
    if status and status != 'All':
        rows = [row for row in rows if row.get('status') == status]
    return sorted(rows, key=lambda x: x.get('created_at', ''), reverse=True)


def create_work_order(payload: dict[str, Any]) -> dict[str, Any]:
    rows = get_work_orders()
    next_id = f"WO-{1000 + len(rows) + 1}"
    asset = get_asset(payload['asset_id'])
    work_order = {
        'id': next_id,
        'asset_id': payload['asset_id'],
        'site': payload.get('site') or (asset.get('site') if asset else ''),
        'title': payload['title'],
        'description': payload.get('description', ''),
        'priority': payload.get('priority', 'Medium'),
        'status': payload.get('status', 'Open'),
        'assignee': payload.get('assignee', ''),
        'due_date': payload.get('due_date', ''),
        'created_at': payload.get('created_at') or utcnow_str(),
        'created_by': payload.get('created_by', ''),
        'completed_at': payload.get('completed_at', ''),
        'source': payload.get('source', 'manual'),
    }
    with DB_LOCK:
        with _conn() as conn:
            write_df(pd.DataFrame([work_order]), 'work_orders', if_exists='append')
            conn.commit()
    sync_alerts()
    return work_order


def update_work_order(work_order_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {'title', 'description', 'priority', 'status', 'assignee', 'due_date', 'completed_at'}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        current = next((row for row in get_work_orders() if row['id'] == work_order_id), None)
        return current
    if updates.get('status') == 'Completed' and not updates.get('completed_at'):
        updates['completed_at'] = utcnow_str()
    with DB_LOCK:
        with _conn() as conn:
            existing = conn.execute(f'SELECT * FROM {identifier("work_orders")} WHERE id=?', (work_order_id,)).fetchone()
            if not existing:
                return None
            cols = ', '.join([f'{identifier(key)}=?' for key in updates.keys()])
            conn.execute(f'UPDATE work_orders SET {cols} WHERE id=?', list(updates.values()) + [work_order_id])
            conn.commit()
    sync_alerts()
    return next((row for row in get_work_orders() if row['id'] == work_order_id), None)


def _desired_alert_rows() -> dict[str, dict[str, Any]]:
    assets = get_assets()
    training = {row['asset_id']: row for row in get_training_records()}
    audits = get_audit()
    work_orders = get_work_orders()
    desired: dict[str, dict[str, Any]] = {}
    now = utcnow_str()

    for asset in assets:
        if asset['riskLevel'] == 'High':
            key = f"risk:{asset['assetId']}"
            desired[key] = {
                'asset_id': asset['assetId'],
                'site': asset['site'],
                'severity': 'Critical' if asset['healthScore'] < 35 else 'High',
                'title': 'High-risk asset requires action',
                'message': f"{asset['assetId']} · {asset['assetName']} is High risk with recommended action '{asset['recommendedAction']}'.",
                'status': 'Open',
                'source_type': 'asset_risk',
                'source_key': key,
                'created_at': now,
                'updated_at': now,
            }
        if _maybe_int(asset.get('overdueDays')) > 14:
            key = f"maintenance:{asset['assetId']}"
            desired[key] = {
                'asset_id': asset['assetId'],
                'site': asset['site'],
                'severity': 'High' if _maybe_int(asset.get('overdueDays')) > 30 else 'Medium',
                'title': 'Maintenance overdue',
                'message': f"{asset['assetId']} is overdue for maintenance by {asset['overdueDays']} day(s).",
                'status': 'Open',
                'source_type': 'maintenance_overdue',
                'source_key': key,
                'created_at': now,
                'updated_at': now,
            }

    for asset_id, row in training.items():
        if not row['training_compliance_flag']:
            asset = get_asset(asset_id)
            key = f"training:{asset_id}"
            desired[key] = {
                'asset_id': asset_id,
                'site': row.get('site') or (asset.get('site') if asset else ''),
                'severity': 'High' if row.get('worker_fatigue') == 'High' else 'Medium',
                'title': 'Training or fatigue non-compliance',
                'message': f"{asset_id} has a training/fatigue compliance flag that requires review.",
                'status': 'Open',
                'source_type': 'training_compliance',
                'source_key': key,
                'created_at': now,
                'updated_at': now,
            }

    for audit in audits:
        if audit.get('decision') == 'Escalated':
            key = f"audit:{audit['id']}"
            asset = get_asset(audit.get('assetId'))
            desired[key] = {
                'asset_id': audit.get('assetId'),
                'site': asset.get('site') if asset else '',
                'severity': 'High',
                'title': 'Escalated review pending',
                'message': f"Audit entry {audit['id']} for {audit.get('assetId')} remains escalated.",
                'status': 'Open',
                'source_type': 'audit_escalation',
                'source_key': key,
                'created_at': now,
                'updated_at': now,
            }

    for work_order in work_orders:
        if work_order.get('status') != 'Completed':
            due = work_order.get('due_date') or ''
            overdue = False
            if due:
                try:
                    overdue = datetime.strptime(due, '%Y-%m-%d').date() < datetime.utcnow().date()
                except ValueError:
                    overdue = False
            if overdue:
                key = f"work_order:{work_order['id']}"
                desired[key] = {
                    'asset_id': work_order.get('asset_id'),
                    'site': work_order.get('site', ''),
                    'severity': 'High' if work_order.get('priority') == 'High' else 'Medium',
                    'title': 'Work order overdue',
                    'message': f"Work order {work_order['id']} for {work_order.get('asset_id')} is overdue.",
                    'status': 'Open',
                    'source_type': 'work_order',
                    'source_key': key,
                    'created_at': now,
                    'updated_at': now,
                }

    return desired


def sync_alerts(conn: CompatConnection | None = None) -> None:
    manage_conn = conn is None
    if manage_conn:
        DB_LOCK.acquire()
        conn = _conn()
    try:
        assert conn is not None
        existing_rows = conn.execute(f'SELECT * FROM {identifier("alerts")}').fetchall() if _table_exists(conn, 'alerts') else []
        existing = {row['source_key']: row for row in existing_rows}
        desired = _desired_alert_rows()
        for source_key, alert in desired.items():
            if source_key in existing:
                current = existing[source_key]
                conn.execute(
                    '''
                    UPDATE alerts SET asset_id=?, site=?, severity=?, title=?, message=?, status=?, source_type=?, updated_at=?
                    WHERE source_key=?
                    ''',
                    (alert['asset_id'], alert['site'], alert['severity'], alert['title'], alert['message'], 'Open', alert['source_type'], alert['updated_at'], source_key),
                )
            else:
                next_id = f"ALR-{1000 + len(existing) + 1}"
                conn.execute(
                    '''
                    INSERT INTO alerts (id, asset_id, site, severity, title, message, status, source_type, source_key, created_at, updated_at, is_read)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ''',
                    (next_id, alert['asset_id'], alert['site'], alert['severity'], alert['title'], alert['message'], 'Open', alert['source_type'], source_key, alert['created_at'], alert['updated_at']),
                )
                existing[source_key] = {'id': next_id, **alert}
        obsolete = set(existing.keys()) - set(desired.keys())
        for source_key in obsolete:
            conn.execute('UPDATE alerts SET status=?, updated_at=? WHERE source_key=?', ('Resolved', utcnow_str(), source_key))
        conn.commit()
    finally:
        if manage_conn:
            conn.close()
            DB_LOCK.release()


def _table_exists(_: CompatConnection, table: str) -> bool:
    return table_exists(table)


def get_alerts(asset_id: str | None = None, status: str | None = 'Open') -> list[dict[str, Any]]:
    sync_alerts()
    with DB_LOCK:
        with _conn() as conn:
            rows = conn.execute(f'SELECT * FROM {identifier("alerts")}').fetchall()
    if asset_id:
        rows = [row for row in rows if row.get('asset_id') == asset_id]
    if status and status != 'All':
        rows = [row for row in rows if row.get('status') == status]
    for row in rows:
        row['is_read'] = _to_bool(row.get('is_read'))
    return sorted(rows, key=lambda x: x.get('updated_at', ''), reverse=True)


def mark_alert_read(alert_id: str) -> dict[str, Any] | None:
    with DB_LOCK:
        with _conn() as conn:
            conn.execute(f'UPDATE {identifier("alerts")} SET is_read=1, updated_at=? WHERE id=?', (utcnow_str(), alert_id))
            conn.commit()
            row = conn.execute(f'SELECT * FROM {identifier("alerts")} WHERE id=?', (alert_id,)).fetchone()
            return dict(row) if row else None


def get_compliance() -> dict[str, Any]:
    standards = _read_table('compliance_standards')
    energy = _read_table('energy_emissions')
    train = get_training_records()
    audits = get_audit()
    alerts = get_alerts(status='Open')
    summary = {
        'compliantAssets': sum(1 for row in energy if _to_bool(row.get('emission_compliant'))),
        'nonCompliantAssets': sum(1 for row in energy if not _to_bool(row.get('emission_compliant'))),
        'trainingFlags': sum(1 for row in train if not row['training_compliance_flag']),
        'openAuditActions': sum(1 for row in audits if row.get('decision') != 'Approved'),
        'openAlerts': len(alerts),
    }
    return {'standards': standards, 'energy': energy, 'training': train, 'audit': audits, 'summary': summary, 'alerts': alerts}


def get_transition() -> list[dict[str, Any]]:
    return _read_table('transition_scenarios')


def get_report_summary() -> dict[str, Any]:
    assets = get_assets()
    audits = get_audit()
    alerts = get_alerts(status='Open')
    work_orders = get_work_orders(status='Open')
    by_site: dict[str, dict[str, Any]] = {}
    for asset in assets:
        site = asset['site']
        site_bucket = by_site.setdefault(site, {'site': site, 'high': 0, 'medium': 0, 'low': 0, 'avgHealth': []})
        site_bucket[asset['riskLevel'].lower()] += 1
        site_bucket['avgHealth'].append(asset['healthScore'])
    site_rows = []
    for site, data in by_site.items():
        avg = round(sum(data['avgHealth']) / len(data['avgHealth']), 1) if data['avgHealth'] else 0
        site_rows.append({k: v for k, v in data.items() if k != 'avgHealth'} | {'avgHealth': avg})
    forecasting = get_forecasting()
    return {
        'totalAssets': len(assets),
        'highRiskAssets': sum(1 for asset in assets if asset['riskLevel'] == 'High'),
        'approvedReviews': sum(1 for audit in audits if audit.get('decision') == 'Approved'),
        'openAlerts': len(alerts),
        'openWorkOrders': len(work_orders),
        'criticalForecasts': forecasting['summary']['criticalWithin30Days'],
        'siteBreakdown': sorted(site_rows, key=lambda row: row['site']),
    }




def _score_to_risk(score: float) -> str:
    if score >= 70:
        return 'Low'
    if score >= 45:
        return 'Medium'
    return 'High'


def build_asset_forecast(asset: dict[str, Any], horizon_weeks: int = 6) -> dict[str, Any]:
    history = list(asset.get('historicalHealthScores') or [])
    if not history:
        history = [max(0, asset.get('healthScore', 0) + 8), max(0, asset.get('healthScore', 0) + 4)]
    current_score = int(asset.get('healthScore', history[-1]))
    baseline = history[-4:] if len(history) >= 4 else history
    slope_values = []
    for i in range(1, len(baseline)):
        slope_values.append(baseline[i] - baseline[i - 1])
    avg_delta = sum(slope_values) / len(slope_values) if slope_values else -2
    degradation = abs(avg_delta) + max(0, asset.get('recentFaults', 0)) * 0.6 + max(0, asset.get('overdueDays', 0)) / 18 + max(0, 60 - asset.get('efficiencyScore', 60)) / 30
    degradation = max(1.2, round(degradation, 2))

    weekly_projection = [{'label': 'Now', 'score': current_score, 'risk': asset.get('riskLevel', _score_to_risk(current_score))}]
    score = float(current_score)
    critical_week = None
    for week in range(1, horizon_weeks + 1):
        score = max(0, round(score - degradation, 1))
        risk = _score_to_risk(score)
        if critical_week is None and score <= 35:
            critical_week = week
        weekly_projection.append({
            'label': f'W+{week}',
            'score': score,
            'risk': risk,
            'date': (datetime.utcnow().date() + timedelta(days=week * 7)).isoformat(),
        })

    projected_30 = weekly_projection[min(4, len(weekly_projection) - 1)]['score']
    decline_30 = round(current_score - projected_30, 1)
    narrative = 'Stable trend with routine observation recommended.'
    if projected_30 < 45:
        narrative = 'Projected health drops into a high-risk band within 30 days. Prioritise intervention.'
    elif decline_30 >= 10:
        narrative = 'Noticeable degradation trend forecast over the next month. Plan maintenance early.'

    return {
        'assetId': asset['assetId'],
        'assetName': asset['assetName'],
        'site': asset['site'],
        'currentHealth': current_score,
        'currentRisk': asset.get('riskLevel', _score_to_risk(current_score)),
        'daysToFailure': asset.get('prediction', {}).get('daysToFailure', 0),
        'confidence': asset.get('prediction', {}).get('confidence', asset.get('confidenceScore', 0)),
        'projectedHealth30d': projected_30,
        'projectedRisk30d': _score_to_risk(projected_30),
        'decline30d': decline_30,
        'degradationPerWeek': degradation,
        'criticalWithinWeeks': critical_week,
        'recommendedAction': asset.get('recommendedAction', 'Inspect / Tune / Clean'),
        'weeklyProjection': weekly_projection,
        'drivers': list((asset.get('explainability') or {}).get('factors', []))[:4],
        'narrative': narrative,
    }


def get_forecasting(site: str | None = None) -> dict[str, Any]:
    assets = get_assets(site=site)
    forecasts = [build_asset_forecast(asset) for asset in assets]
    forecasts.sort(key=lambda row: (row.get('daysToFailure', 9999), row.get('projectedHealth30d', 9999)))
    critical_30 = [row for row in forecasts if row.get('projectedHealth30d', 100) < 45 or row.get('daysToFailure', 9999) <= 30]
    intervention_60 = [row for row in forecasts if row.get('daysToFailure', 9999) <= 60]
    avg_decline = round(sum(row.get('decline30d', 0) for row in forecasts) / len(forecasts), 1) if forecasts else 0
    return {
        'summary': {
            'monitoredAssets': len(forecasts),
            'criticalWithin30Days': len(critical_30),
            'interventionWithin60Days': len(intervention_60),
            'avgProjectedDecline30d': avg_decline,
            'soonestFailure': forecasts[0] if forecasts else None,
        },
        'watchlist': forecasts[:8],
        'forecasts': forecasts,
    }


def get_energy_dashboard(site: str | None = None) -> dict[str, Any]:
    assets = {row['assetId']: row for row in get_assets(site=site)}
    rows = []
    for row in _read_table('energy_emissions'):
        if site and site != 'All' and row.get('site') != site:
            continue
        asset = assets.get(row.get('asset_id'))
        rows.append({
            **row,
            'asset_name': asset.get('assetName') if asset else row.get('asset_id'),
            'risk_level': asset.get('riskLevel') if asset else 'Unknown',
            'recommended_action': asset.get('recommendedAction') if asset else 'Review',
            'emission_compliant': _to_bool(row.get('emission_compliant')),
        })
    rows.sort(key=lambda item: item.get('co2_emissions', 0), reverse=True)
    compliant = [row for row in rows if row.get('emission_compliant')]
    non_compliant = [row for row in rows if not row.get('emission_compliant')]
    total_co2 = round(sum(_maybe_float(row.get('co2_emissions')) for row in rows), 1)
    total_nox = round(sum(_maybe_float(row.get('nox_level')) for row in rows), 1)
    avg_eff = round(sum(_maybe_float(row.get('thermal_efficiency')) for row in rows) / len(rows), 1) if rows else 0

    site_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = site_map.setdefault(row['site'], {'site': row['site'], 'co2': 0.0, 'nox': 0.0, 'eff': [], 'assets': 0, 'nonCompliant': 0})
        bucket['co2'] += _maybe_float(row.get('co2_emissions'))
        bucket['nox'] += _maybe_float(row.get('nox_level'))
        bucket['eff'].append(_maybe_float(row.get('thermal_efficiency')))
        bucket['assets'] += 1
        bucket['nonCompliant'] += 0 if row.get('emission_compliant') else 1
    site_breakdown = []
    for bucket in site_map.values():
        site_breakdown.append({
            'site': bucket['site'],
            'co2': round(bucket['co2'], 1),
            'nox': round(bucket['nox'], 1),
            'avgEfficiency': round(sum(bucket['eff']) / len(bucket['eff']), 1) if bucket['eff'] else 0,
            'assets': bucket['assets'],
            'nonCompliant': bucket['nonCompliant'],
        })
    site_breakdown.sort(key=lambda item: item['site'])

    return {
        'summary': {
            'monitoredAssets': len(rows),
            'compliantAssets': len(compliant),
            'nonCompliantAssets': len(non_compliant),
            'totalCO2': total_co2,
            'totalNOx': total_nox,
            'avgThermalEfficiency': avg_eff,
        },
        'siteBreakdown': site_breakdown,
        'topEmitters': rows[:6],
        'efficiencyWatchlist': sorted(rows, key=lambda item: item.get('thermal_efficiency', 0))[:6],
        'rows': rows,
    }


def assistant_chat(message: str, user_name: str = '') -> dict[str, Any]:
    query = (message or '').strip()
    if not query:
        query = 'Give me an operational summary.'
    q = query.lower()
    assets = get_assets()
    forecasts = get_forecasting().get('forecasts', [])
    work_orders = get_work_orders()
    alerts = get_alerts(status='Open')
    energy = get_energy_dashboard()
    cards: list[dict[str, Any]] = []
    suggestions = [
        'Which assets are most likely to fail next?',
        'Show my overdue work orders.',
        'Which sites have the highest emissions?',
        'Summarise training and fatigue risks.',
    ]

    mentioned_asset = next((asset for asset in assets if asset['assetId'].lower() in q or asset['assetName'].lower() in q), None)
    if mentioned_asset:
        forecast = next((item for item in forecasts if item['assetId'] == mentioned_asset['assetId']), build_asset_forecast(mentioned_asset))
        asset_orders = [row for row in work_orders if row.get('asset_id') == mentioned_asset['assetId'] and row.get('status') != 'Completed']
        asset_alerts = [row for row in alerts if row.get('asset_id') == mentioned_asset['assetId']]
        cards = [
            {'title': mentioned_asset['assetId'], 'subtitle': mentioned_asset['assetName'], 'value': f"{mentioned_asset['riskLevel']} risk · {forecast['daysToFailure']} days to failure", 'link': f"/assets/{mentioned_asset['assetId']}"},
            {'title': 'Recommended action', 'subtitle': mentioned_asset['site'], 'value': mentioned_asset['recommendedAction']},
            {'title': 'Open work orders', 'subtitle': mentioned_asset['site'], 'value': str(len(asset_orders)), 'link': '/work-orders'},
            {'title': 'Open alerts', 'subtitle': mentioned_asset['site'], 'value': str(len(asset_alerts))},
        ]
        answer = (
            f"{mentioned_asset['assetId']} ({mentioned_asset['assetName']}) is currently {mentioned_asset['riskLevel']} risk with a health score of {mentioned_asset['healthScore']}/100. "
            f"The current recommended action is '{mentioned_asset['recommendedAction']}', and the forecast estimates {forecast['daysToFailure']} day(s) to failure with a projected 30-day health of {forecast['projectedHealth30d']}. "
            f"There are {len(asset_orders)} open work order(s) and {len(asset_alerts)} open alert(s) tied to this asset."
        )
    elif 'work order' in q or 'maintenance action' in q:
        open_orders = [row for row in work_orders if row.get('status') != 'Completed']
        overdue = []
        for row in open_orders:
            due = row.get('due_date') or ''
            try:
                overdue_flag = bool(due) and datetime.strptime(due, '%Y-%m-%d').date() < datetime.utcnow().date()
            except ValueError:
                overdue_flag = False
            if overdue_flag:
                overdue.append(row)
        cards = [
            {'title': 'Open work orders', 'subtitle': 'Portfolio-wide', 'value': str(len(open_orders)), 'link': '/work-orders'},
            {'title': 'Overdue work orders', 'subtitle': 'Action required', 'value': str(len(overdue)), 'link': '/work-orders'},
        ] + [
            {'title': row['id'], 'subtitle': f"{row.get('asset_id')} · {row.get('site')}", 'value': f"{row.get('status')} · due {row.get('due_date') or 'TBD'}", 'link': '/work-orders'}
            for row in open_orders[:3]
        ]
        answer = f"There are {len(open_orders)} open work orders across the platform, including {len(overdue)} overdue item(s). The most urgent next step is to clear overdue tasks first, especially any linked to High-risk assets."
    elif 'emission' in q or 'energy' in q or 'nger' in q or 'sustain' in q or 'co2' in q or 'nox' in q:
        top = energy['topEmitters'][:3]
        cards = [
            {'title': 'Total CO₂', 'subtitle': 'Current dataset', 'value': str(energy['summary']['totalCO2']), 'link': '/energy-emissions'},
            {'title': 'Average thermal efficiency', 'subtitle': 'Fleet', 'value': f"{energy['summary']['avgThermalEfficiency']}%", 'link': '/energy-emissions'},
        ] + [
            {'title': row['asset_id'], 'subtitle': row['site'], 'value': f"CO₂ {row['co2_emissions']} · Eff. {row['thermal_efficiency']}%", 'link': '/energy-emissions'}
            for row in top
        ]
        answer = f"The fleet energy profile shows {energy['summary']['totalCO2']} total CO₂ units and an average thermal efficiency of {energy['summary']['avgThermalEfficiency']}%. The highest emitters right now are {', '.join(row['asset_id'] for row in top)}. Use the energy dashboard to prioritise tuning or transition actions for those assets and sites."
    elif 'forecast' in q or 'fail' in q or 'predict' in q or 'next asset' in q or 'watchlist' in q:
        watchlist = forecasts[:4]
        critical = [row for row in forecasts if row['daysToFailure'] <= 30 or row['projectedHealth30d'] < 45]
        cards = [
            {'title': 'Critical within 30 days', 'subtitle': 'Forecasting view', 'value': str(len(critical)), 'link': '/failure-forecasting'},
            {'title': 'Average projected 30-day decline', 'subtitle': 'Fleet trend', 'value': str(get_forecasting()['summary']['avgProjectedDecline30d']), 'link': '/failure-forecasting'},
        ] + [
            {'title': row['assetId'], 'subtitle': row['assetName'], 'value': f"{row['daysToFailure']} days to failure · projected 30d health {row['projectedHealth30d']}", 'link': f"/assets/{row['assetId']}"}
            for row in watchlist
        ]
        answer = f"The near-term failure watchlist is led by {', '.join(row['assetId'] for row in watchlist[:3])}. There are {len(critical)} assets that need intervention within roughly 30 days based on projected health decline or days-to-failure."
    elif 'training' in q or 'fatigue' in q or 'operator' in q:
        training = get_training_records()
        flagged = [row for row in training if not row['training_compliance_flag']]
        high_fatigue = [row for row in training if row['worker_fatigue'] == 'High']
        cards = [
            {'title': 'Flagged training records', 'subtitle': 'Compliance exposure', 'value': str(len(flagged)), 'link': '/fatigue-training'},
            {'title': 'High fatigue records', 'subtitle': 'Operational risk', 'value': str(len(high_fatigue)), 'link': '/fatigue-training'},
        ]
        answer = f"Training and fatigue data currently shows {len(flagged)} flagged compliance record(s) and {len(high_fatigue)} High-fatigue operator assignment(s). These issues should be treated as operational risk multipliers, especially on assets already trending toward failure."
    else:
        high_risk = [asset for asset in assets if asset['riskLevel'] == 'High']
        watchlist = forecasts[:3]
        cards = [
            {'title': 'High-risk assets', 'subtitle': 'Current fleet', 'value': str(len(high_risk)), 'link': '/recommendations'},
            {'title': 'Open alerts', 'subtitle': 'Attention needed', 'value': str(len(alerts)), 'link': '/compliance'},
            {'title': 'Open work orders', 'subtitle': 'Execution layer', 'value': str(len([row for row in work_orders if row.get('status') != 'Completed'])), 'link': '/work-orders'},
            {'title': 'Highest emitter', 'subtitle': 'Energy dashboard', 'value': energy['topEmitters'][0]['asset_id'] if energy['topEmitters'] else 'N/A', 'link': '/energy-emissions'},
        ]
        intro = f"{user_name}, here's the current operational summary" if user_name else 'Here is the current operational summary'
        answer = (
            f"{intro}: there are {len(high_risk)} High-risk assets, {len(alerts)} open alert(s), and {len([row for row in work_orders if row.get('status') != 'Completed'])} active work order(s). "
            f"The most urgent forecasted assets are {', '.join(row['assetId'] for row in watchlist)}. You can ask me for a site, an asset ID, emissions hotspots, or work-order priorities."
        )

    return {
        'answer': answer,
        'cards': cards,
        'suggestions': suggestions,
        'generatedAt': utcnow_str(),
    }

def get_asset_detail(asset_id: str) -> dict[str, Any] | None:
    asset = get_asset(asset_id)
    if not asset:
        return None
    condition_history = sorted([row for row in _read_table('condition_data') if row.get('asset_id') == asset_id], key=lambda x: x.get('observation_date', ''))
    maintenance = get_maintenance_history(asset_id=asset_id)
    audits = get_audit(asset_id=asset_id)
    training = next((row for row in get_training_records() if row['asset_id'] == asset_id), None)
    observations = [row for row in _read_table('operator_observations') if row.get('asset_id') == asset_id]
    energy = next((row for row in _read_table('energy_emissions') if row.get('asset_id') == asset_id), None)
    work_orders = get_work_orders(asset_id=asset_id)
    alerts = get_alerts(asset_id=asset_id, status='All')
    return {
        'asset': asset,
        'conditionHistory': condition_history,
        'maintenanceHistory': maintenance,
        'auditTrail': audits,
        'trainingRecord': training,
        'observations': observations,
        'energyProfile': energy,
        'workOrders': work_orders,
        'alerts': alerts,
        'forecast': build_asset_forecast(asset),
    }
