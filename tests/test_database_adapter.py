from pathlib import Path

from backend.database import SQLiteAdapter

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_sqlite_adapter_introspection(tmp_path: Path):
    adapter = SQLiteAdapter(tmp_path / "adapter.db")
    adapter.initialize(ROOT_DIR / "db" / "init.sql")

    tables = adapter.get_tables()
    assert "users" in tables
    columns = adapter.get_columns("users")
    assert any(column["name"] == "email" for column in columns)


def test_sqlite_adapter_execute_ddl(tmp_path: Path):
    adapter = SQLiteAdapter(tmp_path / "ddl.db")
    result = adapter.execute_statements(
        ["CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, name TEXT)"]
    )

    assert result
    assert result[0]["status"] == "success"
    assert "test_table" in adapter.get_tables()
