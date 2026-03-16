from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from backend.database import DatabaseAdapter, SQLiteAdapter


class SchemaManager:
    """Schema introspection with lightweight in-memory cache."""

    def __init__(
        self,
        db_path_or_adapter: str | Path | DatabaseAdapter,
        cache_ttl_seconds: int = 30,
    ) -> None:
        if isinstance(db_path_or_adapter, DatabaseAdapter):
            self.adapter = db_path_or_adapter
        else:
            self.adapter = SQLiteAdapter(Path(db_path_or_adapter))
        self.cache_ttl_seconds = cache_ttl_seconds

        self._tables_cache: Optional[tuple[float, List[str]]] = None
        self._columns_cache: Dict[str, tuple[float, List[Dict[str, object]]]] = {}
        self._lock = Lock()

    def initialize(self, init_sql_path: str | Path | None = None) -> None:
        self.adapter.initialize(init_sql_path)
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        with self._lock:
            self._tables_cache = None
            self._columns_cache = {}

    def get_tables(self, refresh: bool = False) -> List[str]:
        with self._lock:
            if not refresh and self._tables_cache and self._is_cache_valid(self._tables_cache[0]):
                return list(self._tables_cache[1])

        tables = self.adapter.get_tables()

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

        columns = self.adapter.get_columns(table)

        with self._lock:
            self._columns_cache[table] = (time.time(), columns)

        return [dict(col) for col in columns]

    def has_table(self, table: str) -> bool:
        return table in self.get_tables()

    def get_schema_snapshot(self) -> Dict[str, List[str]]:
        return self.adapter.get_schema_snapshot()

    def _is_cache_valid(self, cached_at: float) -> bool:
        return (time.time() - cached_at) < self.cache_ttl_seconds
