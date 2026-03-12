from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.main import create_app


@pytest.fixture()
def app(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_sql_path = ROOT_DIR / "db" / "init.sql"

    return create_app(
        db_path=db_path,
        init_sql_path=init_sql_path,
        enable_llm=False,
    )


@pytest.fixture()
def client(app):
    return TestClient(app)
