from pathlib import Path

from fastapi.testclient import TestClient

from backend.llm import BaseLLMProvider
from backend.main import create_app


class StubDDLProvider(BaseLLMProvider):
    def generate_completion(self, sql_prefix, schema_snapshot, context):
        return []

    def generate_ddl_candidates(self, intent, schema_snapshot, dialect):
        return [
            "CREATE DATABASE crm",
            "CREATE TABLE crm.crm_users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
        ]


def test_chat_plan_creates_pending_proposal(client):
    response = client.post(
        "/chat/plan",
        json={
            "prompt": "创建 demo_users 表",
            "use_llm": False,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    proposal = payload["proposal"]
    assert proposal["status"] == "PENDING"
    assert proposal["proposal_id"]
    assert proposal["operations"]


def test_chat_approve_executes_create_table(client):
    plan_response = client.post(
        "/chat/plan",
        json={
            "prompt": "创建 demo_orders 表",
            "use_llm": False,
        },
    )
    proposal = plan_response.json()["proposal"]

    approve_response = client.post(
        f"/chat/proposals/{proposal['proposal_id']}/approve",
        json={
            "approval_token": proposal["approval_token"],
            "approver": "test-suite",
        },
    )
    assert approve_response.status_code == 200

    body = approve_response.json()
    assert body["proposal"]["status"] == "EXECUTED"
    assert any(
        result["status"] == "success" for result in body["proposal"]["execution_results"]
    )

    tables_response = client.get("/schema/tables")
    assert tables_response.status_code == 200
    assert "demo_orders" in tables_response.json()["tables"]


def test_blocked_proposal_cannot_be_approved(client):
    plan_response = client.post(
        "/chat/plan",
        json={
            "prompt": "DROP TABLE users;",
            "use_llm": False,
        },
    )
    proposal = plan_response.json()["proposal"]
    assert proposal["has_blocking_risk"] is True

    approve_response = client.post(
        f"/chat/proposals/{proposal['proposal_id']}/approve",
        json={
            "approval_token": proposal["approval_token"],
        },
    )
    assert approve_response.status_code == 400
    assert "blocked operations" in approve_response.json()["detail"].lower()


def test_chat_plan_allows_sqlite_database_request_after_normalization(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "test.db",
        init_sql_path=Path(__file__).resolve().parent.parent / "db" / "init.sql",
        audit_db_path=tmp_path / "audit.db",
        enable_llm=True,
        llm_provider=StubDDLProvider(),
    )
    client = TestClient(app)

    plan_response = client.post(
        "/chat/plan",
        json={
            "prompt": "创建 crm 库并建 crm_users 表，字段有 id、name、email",
            "use_llm": True,
        },
    )
    assert plan_response.status_code == 200

    proposal = plan_response.json()["proposal"]
    assert proposal["has_blocking_risk"] is False
    assert proposal["operations"] == [
        {
            "statement": "CREATE TABLE IF NOT EXISTS crm_users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
            "operation_type": "create_table",
            "allowed": True,
            "risk_level": "safe",
            "reason": "Allowed by safe DDL policy.",
        }
    ]

    approve_response = client.post(
        f"/chat/proposals/{proposal['proposal_id']}/approve",
        json={
            "approval_token": proposal["approval_token"],
            "approver": "test-suite",
        },
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["proposal"]["status"] == "EXECUTED"

    tables_response = client.get("/schema/tables")
    assert tables_response.status_code == 200
    assert "crm_users" in tables_response.json()["tables"]
