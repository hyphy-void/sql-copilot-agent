from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional


class AuditStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_schema()

    def create_proposal(
        self,
        proposal_id: str,
        request_text: str,
        backend: str,
        dialect: str,
        source: str,
        approval_token: str,
        has_blocking_risk: bool,
        risk_summary: str,
        risk_level: str,
        notes: List[str],
        operations: List[Dict[str, Any]],
        normalized_intent: str,
        impact_summary: str,
        preflight_checks: List[Dict[str, Any]],
        actor_id: str | None = None,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ddl_proposals (
                    proposal_id,
                    request_text,
                    backend,
                    dialect,
                    source,
                    status,
                    approval_token,
                    has_blocking_risk,
                    risk_summary,
                    risk_level,
                    notes_json,
                    operations_json,
                    execution_results_json,
                    normalized_intent,
                    impact_summary,
                    preflight_checks_json,
                    actor_id,
                    session_id,
                    rejection_reason,
                    error_message,
                    approver,
                    created_at,
                    updated_at,
                    approved_at,
                    rejected_at,
                    executed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    request_text,
                    backend,
                    dialect,
                    source,
                    "PENDING",
                    approval_token,
                    int(has_blocking_risk),
                    risk_summary,
                    risk_level,
                    json.dumps(notes, ensure_ascii=False),
                    json.dumps(operations, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    normalized_intent,
                    impact_summary,
                    json.dumps(preflight_checks, ensure_ascii=False),
                    actor_id,
                    session_id,
                    None,
                    None,
                    None,
                    now,
                    now,
                    None,
                    None,
                    None,
                ),
            )
            conn.commit()
        return self.get_proposal(proposal_id) or {}

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ddl_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def update_rejected(self, proposal_id: str, reason: str | None = None) -> Optional[Dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE ddl_proposals
                SET status = ?, rejection_reason = ?, rejected_at = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                ("REJECTED", reason, now, now, proposal_id),
            )
            conn.commit()
        return self.get_proposal(proposal_id)

    def update_approved(self, proposal_id: str, approver: str | None = None) -> Optional[Dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE ddl_proposals
                SET status = ?, approver = ?, approved_at = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                ("APPROVED", approver, now, now, proposal_id),
            )
            conn.commit()
        return self.get_proposal(proposal_id)

    def update_execution(
        self,
        proposal_id: str,
        status: str,
        execution_results: List[Dict[str, Any]],
        error_message: str | None = None,
    ) -> Optional[Dict[str, Any]]:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE ddl_proposals
                SET status = ?, execution_results_json = ?, error_message = ?, executed_at = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    status,
                    json.dumps(execution_results, ensure_ascii=False),
                    error_message,
                    now,
                    now,
                    proposal_id,
                ),
            )
            conn.commit()
        return self.get_proposal(proposal_id)

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ddl_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    request_text TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    dialect TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_token TEXT NOT NULL,
                    has_blocking_risk INTEGER NOT NULL,
                    risk_summary TEXT NOT NULL,
                    risk_level TEXT NOT NULL DEFAULT 'safe',
                    notes_json TEXT NOT NULL,
                    operations_json TEXT NOT NULL,
                    execution_results_json TEXT NOT NULL,
                    normalized_intent TEXT NOT NULL DEFAULT '',
                    impact_summary TEXT NOT NULL DEFAULT '',
                    preflight_checks_json TEXT NOT NULL DEFAULT '[]',
                    actor_id TEXT,
                    session_id TEXT,
                    rejection_reason TEXT,
                    error_message TEXT,
                    approver TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    rejected_at TEXT,
                    executed_at TEXT
                )
                """
            )
            self._migrate_schema(conn)
            conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = _existing_columns(conn)
        migrations = {
            "risk_level": "ALTER TABLE ddl_proposals ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'safe'",
            "normalized_intent": "ALTER TABLE ddl_proposals ADD COLUMN normalized_intent TEXT NOT NULL DEFAULT ''",
            "impact_summary": "ALTER TABLE ddl_proposals ADD COLUMN impact_summary TEXT NOT NULL DEFAULT ''",
            "preflight_checks_json": "ALTER TABLE ddl_proposals ADD COLUMN preflight_checks_json TEXT NOT NULL DEFAULT '[]'",
            "actor_id": "ALTER TABLE ddl_proposals ADD COLUMN actor_id TEXT",
            "session_id": "ALTER TABLE ddl_proposals ADD COLUMN session_id TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    payload = dict(row)
    payload["has_blocking_risk"] = bool(payload["has_blocking_risk"])
    payload["notes"] = json.loads(payload.pop("notes_json") or "[]")
    payload["operations"] = json.loads(payload.pop("operations_json") or "[]")
    payload["execution_results"] = json.loads(payload.pop("execution_results_json") or "[]")
    payload["preflight_checks"] = json.loads(payload.pop("preflight_checks_json", "[]") or "[]")
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(ddl_proposals)").fetchall()
    return {str(row[1]) for row in rows}
