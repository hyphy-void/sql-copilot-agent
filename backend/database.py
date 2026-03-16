from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import sqlglot
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


DEFAULT_ALLOWED_DDL = [
    "CREATE DATABASE",
    "CREATE TABLE",
    "CREATE INDEX",
    "ALTER TABLE ADD COLUMN",
]


def split_sql_statements(sql_text: str, dialect: str) -> List[str]:
    content = (sql_text or "").strip()
    if not content:
        return []

    try:
        expressions = sqlglot.parse(content, read=dialect)
        statements = [expr.sql(dialect=dialect).strip() for expr in expressions if expr]
        statements = [stmt for stmt in statements if stmt]
        if statements:
            return statements
    except Exception:
        pass

    return [item.strip() for item in re.split(r";\s*", content) if item.strip()]


class DatabaseAdapter(ABC):
    backend_name: str
    dialect: str
    supports_create_database: bool

    @abstractmethod
    def initialize(self, init_sql_path: str | Path | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_tables(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_columns(self, table: str) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def execute_statements(self, statements: List[str]) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:
        raise NotImplementedError

    def get_schema_snapshot(self) -> Dict[str, List[str]]:
        snapshot: Dict[str, List[str]] = {}
        for table in self.get_tables():
            columns = self.get_columns(table)
            snapshot[table] = [str(column["name"]) for column in columns]
        return snapshot

    def get_capabilities(self) -> Dict[str, object]:
        return {
            "backend": self.backend_name,
            "dialect": self.dialect,
            "connected": self.ping(),
            "supports_create_database": self.supports_create_database,
            "allowed_ddl": DEFAULT_ALLOWED_DDL,
        }


class SQLAlchemyDatabaseAdapter(DatabaseAdapter):
    backend_name = "unknown"
    dialect = "sqlite"
    supports_create_database = False

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def initialize(self, init_sql_path: str | Path | None = None) -> None:
        if init_sql_path is None:
            return

        path = Path(init_sql_path)
        if not path.exists():
            return

        script = path.read_text(encoding="utf-8")
        statements = split_sql_statements(script, self.dialect)
        if not statements:
            return
        self.execute_statements(statements)

    def get_tables(self) -> List[str]:
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        if self.backend_name == "sqlite":
            tables = [name for name in tables if not name.startswith("sqlite_")]
        return sorted(tables)

    def get_columns(self, table: str) -> List[Dict[str, object]]:
        schema_name, table_name = _split_schema_table(table)
        inspector = inspect(self.engine)
        columns = inspector.get_columns(table_name, schema=schema_name)
        result: List[Dict[str, object]] = []
        for item in columns:
            result.append(
                {
                    "name": item.get("name"),
                    "type": str(item.get("type") or ""),
                    "notnull": not bool(item.get("nullable", True)),
                    "default": item.get("default"),
                    "pk": bool(item.get("primary_key", False)),
                }
            )
        return result

    def execute_statements(self, statements: List[str]) -> List[Dict[str, object]]:
        results: List[Dict[str, object]] = []
        for statement in statements:
            sql = statement.strip()
            if not sql:
                continue

            start = perf_counter()
            try:
                with self.engine.connect() as connection:
                    connection.execute(text(sql))
                    connection.commit()
                duration_ms = round((perf_counter() - start) * 1000, 3)
                results.append(
                    {
                        "statement": sql,
                        "status": "success",
                        "duration_ms": duration_ms,
                        "error": None,
                    }
                )
            except Exception as exc:
                duration_ms = round((perf_counter() - start) * 1000, 3)
                results.append(
                    {
                        "statement": sql,
                        "status": "error",
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    }
                )

        return results

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class SQLiteAdapter(SQLAlchemyDatabaseAdapter):
    backend_name = "sqlite"
    dialect = "sqlite"
    supports_create_database = False

    def __init__(self, db_path: str | Path) -> None:
        resolved_path = Path(db_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{resolved_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        super().__init__(engine)
        self.db_path = resolved_path


class MySQLAdapter(SQLAlchemyDatabaseAdapter):
    backend_name = "mysql"
    dialect = "mysql"
    supports_create_database = True

    def __init__(
        self,
        dsn: str | None = None,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        url = dsn or _build_mysql_url(
            host=host or "127.0.0.1",
            port=port or 3306,
            user=user or "root",
            password=password or "",
            database=database or "",
        )
        engine = create_engine(url, future=True, pool_pre_ping=True)
        super().__init__(engine)
        self.url = url


class ConnectionManager:
    def __init__(self, adapter: DatabaseAdapter) -> None:
        self.adapter = adapter

    @classmethod
    def from_env(
        cls,
        default_db_path: str | Path,
    ) -> "ConnectionManager":
        backend = (os.getenv("DB_BACKEND") or "sqlite").strip().lower()

        if backend == "mysql":
            adapter: DatabaseAdapter = MySQLAdapter(
                dsn=_read_env("MYSQL_DSN"),
                host=_read_env("MYSQL_HOST"),
                port=_read_int_env("MYSQL_PORT") or 3306,
                user=_read_env("MYSQL_USER") or "root",
                password=_read_env("MYSQL_PASSWORD") or "",
                database=_read_env("MYSQL_DATABASE") or "",
            )
        else:
            db_path = Path(_read_env("DB_PATH") or default_db_path)
            adapter = SQLiteAdapter(db_path=db_path)

        return cls(adapter)

    def get_adapter(self) -> DatabaseAdapter:
        return self.adapter

    def get_capabilities(self) -> Dict[str, object]:
        return self.adapter.get_capabilities()


def _split_schema_table(table: str) -> tuple[Optional[str], str]:
    value = (table or "").strip()
    if "." not in value:
        return None, value

    schema, table_name = value.split(".", 1)
    return schema.strip() or None, table_name.strip()


def _build_mysql_url(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> str:
    encoded_user = quote_plus(user)
    encoded_password = quote_plus(password)
    auth = encoded_user
    if encoded_password:
        auth = f"{encoded_user}:{encoded_password}"
    return f"mysql+pymysql://{auth}@{host}:{port}/{database}"


def _read_env(key: str) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read_int_env(key: str) -> Optional[int]:
    value = _read_env(key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None
