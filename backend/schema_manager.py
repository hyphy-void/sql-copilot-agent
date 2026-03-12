from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional


class SchemaManager:
    """SQLite schema introspection with lightweight in-memory cache."""

    def __init__(self, db_path: str | Path, cache_ttl_seconds: int = 30) -> None:
        self.db_path = Path(db_path)
        self.cache_ttl_seconds = cache_ttl_seconds

        self._tables_cache: Optional[tuple[float, List[str]]] = None
        self._columns_cache: Dict[str, tuple[float, List[Dict[str, object]]]] = {}
        self._lock = Lock()

    def initialize(self, init_sql_path: str | Path | None = None) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if init_sql_path is None:
            return

        script_path = Path(init_sql_path)
        if not script_path.exists():
            return

        script = script_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(script)
            conn.commit()

        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        with self._lock:
            self._tables_cache = None
            self._columns_cache = {}

    def get_tables(self, refresh: bool = False) -> List[str]:
        with self._lock:
            if not refresh and self._tables_cache and self._is_cache_valid(self._tables_cache[0]):
                return list(self._tables_cache[1])

        query = (
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        tables = [row[0] for row in rows]

        with self._lock:
            self._tables_cache = (time.time(), tables)

        return list(tables)

    def get_columns(self, table: str, refresh: bool = False) -> List[Dict[str, object]]:
        table = table.strip()
        if not table:
            return []

        with self._lock:
            cached = self._columns_cache.get(table)
            if not refresh and cached and self._is_cache_valid(cached[0]):
                return [dict(col) for col in cached[1]]

        if table not in self.get_tables(refresh=refresh):
            return []

        safe_table = table.replace('"', '""')
        with self._connect() as conn:
            rows = conn.execute(f'PRAGMA table_info("{safe_table}")').fetchall()

        columns = [
            {
                "name": row[1],
                "type": row[2],
                "notnull": bool(row[3]),
                "default": row[4],
                "pk": bool(row[5]),
            }
            for row in rows
        ]

        with self._lock:
            self._columns_cache[table] = (time.time(), columns)

        return [dict(col) for col in columns]

    def has_table(self, table: str) -> bool:
        return table in self.get_tables()

    def get_schema_snapshot(self) -> Dict[str, List[str]]:
        snapshot: Dict[str, List[str]] = {}
        for table in self.get_tables():
            columns = self.get_columns(table)
            snapshot[table] = [column["name"] for column in columns]
        return snapshot

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _is_cache_valid(self, cached_at: float) -> bool:
        return (time.time() - cached_at) < self.cache_ttl_seconds
