from __future__ import annotations

from datetime import datetime
import os
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import repository
from .modeling import train_benchmark_models
from .security import create_token, decode_token

PRIMARY_ADMIN_ID = 'USR-ADMIN-001'
PRIMARY_ADMIN_ROLE = 'Admin'
CORS_ORIGINS = [origin.strip() for origin in os.environ.get('CORS_ORIGINS', '*').split(',') if origin.strip()] or ['*']

app = FastAPI(title='OBrien IDI API', version='2.0.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    desiredRole: str
    site: str
    notes: str = ''


class ReviewRequest(BaseModel):
    asset_id: str
    decision: str
    comment: str = ''
    reviewer: str
    reviewer_role: str
    standard: str = 'AS3788'
    compliant: bool = True


class RoleRequest(BaseModel):
    role: str


class ProfileRequest(BaseModel):
    name: str
    site: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str


class PreferencesRequest(BaseModel):
    preferences: dict[str, Any]


class MaintenanceRequest(BaseModel):
    asset_id: str
    service_date: str
    service_type: str
    technician: str
    downtime_hours: float = 0
    notes: str = ''
    overdue_days_snapshot: int = 0


class TrainingRequest(BaseModel):
    site: str | None = None
    worker_certified: bool
    worker_experience_years: int
    worker_fatigue: str
    training_compliance_flag: bool


class WorkOrderCreateRequest(BaseModel):
    asset_id: str
    title: str
    description: str = ''
    priority: str = 'Medium'
    status: str = 'Open'
    assignee: str = ''
    due_date: str = ''
    source: str = 'manual'


class WorkOrderUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    completed_at: str | None = None


class AssistantRequest(BaseModel):
    message: str


@app.on_event('startup')
def startup() -> None:
    repository.init_db()


@app.get('/api/health')
def health() -> dict[str, Any]:
    return {'status': 'ok', 'time': datetime.utcnow().isoformat()}


@app.get('/api/sites')
def sites() -> list[dict[str, Any]]:
    return repository.get_sites()


@app.get('/api/auth/roles')
def roles() -> list[str]:
    return repository.get_roles()


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict[str, Any]:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Authentication required.')
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = repository.get_user_by_id(payload.get('sub', ''))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found.')
    if not user.get('isActive'):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account has been deactivated.')
    if not user.get('approved'):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account is pending admin approval.')
    return user


def require_roles(*allowed_roles: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def dependency(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if current_user.get('role') not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this action.')
        return current_user

    return dependency


@app.post('/api/auth/login')
def login(payload: LoginRequest) -> dict[str, Any]:
    user = repository.authenticate_user(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=404, detail='No account found for this email.')
    if 'error' in user:
        raise HTTPException(status_code=400, detail=user['error'])
    token = create_token({'sub': user['id'], 'role': user['role'], 'email': user['email']})
    return {'token': token, 'user': user, 'preferences': repository.get_user_preferences(user['id'])}


@app.post('/api/auth/signup')
def signup(payload: SignupRequest) -> dict[str, Any]:
    ok, message = repository.signup({**payload.model_dump(), 'createdAt': datetime.utcnow().strftime('%Y-%m-%d %H:%M')})
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {'message': message}


@app.get('/api/auth/me')
def me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {'user': current_user, 'preferences': repository.get_user_preferences(current_user['id'])}


@app.put('/api/auth/me/profile')
def update_profile(payload: ProfileRequest, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    updated = repository.update_profile(current_user['id'], name=payload.name, site=payload.site)
    if not updated:
        raise HTTPException(status_code=404, detail='User not found.')
    return {'user': updated, 'preferences': repository.get_user_preferences(current_user['id'])}


@app.post('/api/auth/me/password')
def update_password(payload: PasswordRequest, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    ok, message = repository.change_password(current_user['id'], payload.current_password, payload.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {'message': message}


@app.put('/api/auth/me/preferences')
def update_preferences(payload: PreferencesRequest, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    prefs = repository.update_user_preferences(current_user['id'], payload.preferences)
    return {'preferences': prefs}


@app.get('/api/auth/users')
def users(_: dict[str, Any] = Depends(require_roles('Admin'))) -> list[dict[str, Any]]:
    return repository.get_users()


@app.get('/api/assets')
def assets(site: str | None = None, risk: str | None = None, search: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_assets(site=site, risk=risk, search=search)


@app.get('/api/assets/{asset_id}')
def asset(asset_id: str, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    result = repository.get_asset(asset_id)
    if not result:
        raise HTTPException(status_code=404, detail='Asset not found.')
    return result


@app.get('/api/assets/{asset_id}/detail')
def asset_detail(asset_id: str, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    result = repository.get_asset_detail(asset_id)
    if not result:
        raise HTTPException(status_code=404, detail='Asset not found.')
    return result


@app.get('/api/dashboard')
def dashboard(site: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.get_dashboard(site)


@app.get('/api/audit')
def audit(asset_id: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_audit(asset_id=asset_id)


@app.post('/api/review')
def review(payload: ReviewRequest, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.add_audit_entry({**payload.model_dump(), 'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M')})


@app.get('/api/recommendations')
def recommendations(site: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_recommendations(site=site)


@app.get('/api/compliance')
def compliance(_: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.get_compliance()


@app.get('/api/reports/summary')
def report_summary(_: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.get_report_summary()


@app.get('/api/transition')
def transition(_: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_transition()


@app.get('/api/models/benchmark')
def model_benchmark(_: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return train_benchmark_models(force=False)


@app.get('/api/forecasting')
def forecasting(site: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.get_forecasting(site=site)


@app.get('/api/energy-emissions')
def energy_emissions(site: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.get_energy_dashboard(site=site)


@app.post('/api/assistant/chat')
def assistant_chat(payload: AssistantRequest, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.assistant_chat(payload.message, user_name=current_user.get('name', ''))


@app.get('/api/training-compliance')
def training_compliance(site: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_training_records(site=site)


@app.put('/api/training-compliance/{asset_id}')
def update_training(asset_id: str, payload: TrainingRequest, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.upsert_training_record(asset_id, payload.model_dump())


@app.get('/api/maintenance')
def maintenance(asset_id: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_maintenance_history(asset_id=asset_id)


@app.post('/api/maintenance')
def create_maintenance(payload: MaintenanceRequest, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.add_maintenance_entry(payload.model_dump())


@app.get('/api/alerts')
def alerts(status_filter: str | None = 'Open', asset_id: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    status_value = status_filter
    return repository.get_alerts(asset_id=asset_id, status=status_value)


@app.post('/api/alerts/{alert_id}/read')
def read_alert(alert_id: str, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    updated = repository.mark_alert_read(alert_id)
    if not updated:
        raise HTTPException(status_code=404, detail='Alert not found.')
    return updated


@app.get('/api/work-orders')
def work_orders(asset_id: str | None = None, status_filter: str | None = None, _: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.get_work_orders(asset_id=asset_id, status=status_filter)


@app.post('/api/work-orders')
def create_work_order(payload: WorkOrderCreateRequest, current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repository.create_work_order({**payload.model_dump(), 'created_by': current_user['name'], 'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')})


@app.put('/api/work-orders/{work_order_id}')
def edit_work_order(work_order_id: str, payload: WorkOrderUpdateRequest, _: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    updated = repository.update_work_order(work_order_id, {k: v for k, v in payload.model_dump().items() if v is not None})
    if not updated:
        raise HTTPException(status_code=404, detail='Work order not found.')
    return updated


@app.post('/api/admin/users/{user_id}/approve')
def approve_user(user_id: str, payload: RoleRequest, _: dict[str, Any] = Depends(require_roles('Admin'))) -> dict[str, Any]:
    role = payload.role.strip() or 'Engineer / Operator'
    if role == 'Pending Approval':
        role = 'Engineer / Operator'
    updated = repository.update_user(user_id, approved=True, role=role, desiredRole=role)
    if not updated:
        raise HTTPException(status_code=404, detail='User not found.')
    return updated


@app.post('/api/admin/users/{user_id}/role')
def assign_role(user_id: str, payload: RoleRequest, _: dict[str, Any] = Depends(require_roles('Admin'))) -> dict[str, Any]:
    role = payload.role.strip()
    if not role:
        raise HTTPException(status_code=400, detail='Role is required.')
    if user_id == PRIMARY_ADMIN_ID and role != PRIMARY_ADMIN_ROLE:
        raise HTTPException(status_code=400, detail='Primary demo admin role cannot be changed.')
    updated = repository.update_user(user_id, role=role, desiredRole=role)
    if not updated:
        raise HTTPException(status_code=404, detail='User not found.')
    return updated


@app.post('/api/admin/users/{user_id}/toggle-active')
def toggle_active(user_id: str, _: dict[str, Any] = Depends(require_roles('Admin'))) -> dict[str, Any]:
    users = repository.get_users()
    target = next((u for u in users if u['id'] == user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail='User not found.')
    if user_id == PRIMARY_ADMIN_ID:
        raise HTTPException(status_code=400, detail='Primary demo admin account cannot be deactivated.')
    updated = repository.update_user(user_id, isActive=not bool(target.get('isActive')))
    if not updated:
        raise HTTPException(status_code=404, detail='User not found.')
    return updated


@app.post('/api/admin/reset-demo')
def reset_demo(_: dict[str, Any] = Depends(require_roles('Admin'))) -> dict[str, str]:
    try:
        repository.reset_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Unable to reset demo data: {exc}') from exc
    return {'message': 'Demo data reset successfully.'}
