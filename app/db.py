from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / 'data'
SQLITE_PATH = DATA_DIR / 'app.db'


def _normalize_database_url(url: str) -> str:
    value = (url or '').strip()
    if not value:
        return f"sqlite:///{SQLITE_PATH}"
    if value.startswith('postgres://'):
        return 'postgresql+psycopg://' + value[len('postgres://'):]
    if value.startswith('postgresql://') and not value.startswith('postgresql+psycopg://'):
        return 'postgresql+psycopg://' + value[len('postgresql://'):]
    return value


DATABASE_URL = _normalize_database_url(os.environ.get('DATABASE_URL', ''))
ENGINE = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
IS_POSTGRES = ENGINE.dialect.name.startswith('postgresql')
IS_SQLITE = ENGINE.dialect.name == 'sqlite'


def identifier(name: str) -> str:
    return f'"{name}"' if IS_POSTGRES else name


def table_exists(table: str) -> bool:
    return inspect(ENGINE).has_table(table)


def get_columns(table: str) -> list[str]:
    if not table_exists(table):
        return []
    return [col['name'] for col in inspect(ENGINE).get_columns(table)]


def drop_table(table: str) -> None:
    with ENGINE.begin() as conn:
        cascade = ' CASCADE' if IS_POSTGRES else ''
        conn.execute(text(f'DROP TABLE IF EXISTS {identifier(table)}{cascade}'))


def write_df(df: pd.DataFrame, table: str, if_exists: str = 'append') -> None:
    with ENGINE.begin() as conn:
        df.to_sql(table, conn, if_exists=if_exists, index=False)


def read_sql_frame(sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(text(sql), ENGINE, params=params or {})


class ResultWrapper:
    def __init__(self, result: Any) -> None:
        self._result = result

    def fetchone(self) -> dict[str, Any] | None:
        row = self._result.fetchone()
        if row is None:
            return None
        mapping = getattr(row, '_mapping', row)
        return dict(mapping)

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._result.fetchall()
        wrapped: list[dict[str, Any]] = []
        for row in rows:
            mapping = getattr(row, '_mapping', row)
            wrapped.append(dict(mapping))
        return wrapped


class CompatConnection:
    def __init__(self) -> None:
        self._ctx = ENGINE.begin()
        self._conn = self._ctx.__enter__()
        self._closed = False

    def __enter__(self) -> 'CompatConnection':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._closed:
            self._closed = True
            self._ctx.__exit__(exc_type, exc, tb)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._ctx.__exit__(None, None, None)

    def commit(self) -> None:
        return None

    @staticmethod
    def _convert_query(query: str, params: Any) -> tuple[str, dict[str, Any]]:
        if params is None:
            return query, {}
        if isinstance(params, dict):
            return query, params
        if not isinstance(params, (list, tuple)):
            params = [params]
        out_parts: list[str] = []
        bindings: dict[str, Any] = {}
        idx = 0
        for char in query:
            if char == '?':
                key = f'p{idx}'
                out_parts.append(f':{key}')
                bindings[key] = params[idx]
                idx += 1
            else:
                out_parts.append(char)
        return ''.join(out_parts), bindings

    def execute(self, query: str, params: Any = None) -> ResultWrapper:
        sql, bound = self._convert_query(query, params)
        result = self._conn.execute(text(sql), bound)
        return ResultWrapper(result)
