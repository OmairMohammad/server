from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

SECRET_KEY = os.environ.get('OBRIEN_IDI_SECRET', 'obrien-idi-demo-secret-key')
TOKEN_TTL_SECONDS = int(os.environ.get('OBRIEN_IDI_TOKEN_TTL_SECONDS', str(60 * 60 * 12)))
PBKDF2_ITERATIONS = 120_000


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def _b64url_decode(data: str) -> bytes:
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(f'{data}{padding}'.encode('utf-8'))


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS)
    return f'{salt}${_b64url_encode(digest)}'


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash or '$' not in password_hash:
        return False
    salt, expected = password_hash.split('$', 1)
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, f'{salt}${expected}')


def create_token(payload: dict[str, Any], ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    body = dict(payload)
    body['exp'] = int(time.time()) + ttl_seconds
    encoded_payload = _b64url_encode(json.dumps(body, separators=(',', ':')).encode('utf-8'))
    signature = hmac.new(SECRET_KEY.encode('utf-8'), encoded_payload.encode('utf-8'), hashlib.sha256).digest()
    return f'{encoded_payload}.{_b64url_encode(signature)}'


def decode_token(token: str) -> dict[str, Any]:
    try:
        encoded_payload, encoded_signature = token.split('.', 1)
    except ValueError as exc:
        raise ValueError('Malformed token.') from exc

    expected = hmac.new(SECRET_KEY.encode('utf-8'), encoded_payload.encode('utf-8'), hashlib.sha256).digest()
    actual = _b64url_decode(encoded_signature)
    if not hmac.compare_digest(expected, actual):
        raise ValueError('Invalid token signature.')

    payload = json.loads(_b64url_decode(encoded_payload).decode('utf-8'))
    if int(payload.get('exp', 0)) < int(time.time()):
        raise ValueError('Token has expired.')
    return payload
