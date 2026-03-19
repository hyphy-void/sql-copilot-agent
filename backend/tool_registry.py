from __future__ import annotations

import secrets
from typing import Any, Dict, List
from uuid import uuid4

from backend.audit_store import AuditStore
from backend.database import DatabaseAdapter
from backend.ddl_guard import validate_ddl_statements
from backend.ddl_planner import DDLPlanner
from backend.schema_manager import SchemaManager


class ToolRegistry:
    def __init__(
        self,
        adapter: DatabaseAdapter,
        schema_manager: SchemaManager,
        planner: DDLPlanner,
        audit_store: AuditStore,
    ) -> None:
        self.adapter = adapter
        self.schema_manager = schema_manager
        self.planner = planner
        self.audit_store = audit_store

    def propose_ddl(
        self,
        prompt: str,
        use_llm: bool = True,
        actor_id: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
    ) -> Dict[str, Any]:
        schema_snapshot = self.schema_manager.get_schema_snapshot()
        planning = self.planner.plan(
            prompt=prompt,
            backend=self.adapter.backend_name,
            dialect=self.adapter.dialect,
            schema_snapshot=schema_snapshot,
            use_llm=use_llm,
        )

        statements = list(planning.get("statements", []))
        guard_result = validate_ddl_statements(
            statements=statements,
            dialect=self.adapter.dialect,
            supports_create_database=self.adapter.supports_create_database,
            schema_snapshot=schema_snapshot,
        )

        proposal_id = uuid4().hex[:12]
        approval_token = _generate_approval_token()
        stored = self.audit_store.create_proposal(
            proposal_id=proposal_id,
            request_text=prompt,
            backend=self.adapter.backend_name,
            dialect=self.adapter.dialect,
            source=source or str(planning.get("source") or "template"),
            approval_token=approval_token,
            has_blocking_risk=bool(guard_result["has_blocking_risk"]),
            risk_summary=str(guard_result["risk_summary"]),
            risk_level=str(guard_result["risk_level"]),
            notes=list(planning.get("notes") or []),
            operations=list(guard_result["operations"]),
            normalized_intent=str(planning.get("normalized_intent") or prompt.strip()),
            impact_summary=str(guard_result.get("impact_summary") or ""),
            preflight_checks=list(guard_result.get("preflight_checks") or []),
            actor_id=actor_id,
            session_id=session_id,
        )

        return _to_api_payload(stored)

    def get_proposal(self, proposal_id: str) -> Dict[str, Any] | None:
        payload = self.audit_store.get_proposal(proposal_id)
        if payload is None:
            return None
        return _to_api_payload(payload)

    def reject_proposal(self, proposal_id: str, reason: str | None = None) -> Dict[str, Any] | None:
        updated = self.audit_store.update_rejected(proposal_id, reason=reason)
        if updated is None:
            return None
        return _to_api_payload(updated)

    def approve_proposal(
        self,
        proposal_id: str,
        approval_token: str,
        approver: str | None = None,
    ) -> Dict[str, Any]:
        current = self.audit_store.get_proposal(proposal_id)
        if current is None:
            raise ValueError("Proposal not found.")

        status = str(current.get("status") or "")
        if status != "PENDING":
            raise ValueError(f"Proposal status is {status}, expected PENDING.")

        expected_token = str(current.get("approval_token") or "")
        if approval_token.strip() != expected_token:
            raise PermissionError("Invalid approval token.")

        if bool(current.get("has_blocking_risk")):
            raise ValueError("Proposal contains blocked operations and cannot be executed.")

        failed_preflight = [
            check
            for check in list(current.get("preflight_checks") or [])
            if str(check.get("status") or "").lower() == "fail"
        ]
        if failed_preflight:
            raise ValueError("Proposal preflight checks failed and cannot be executed.")

        self.audit_store.update_approved(proposal_id, approver=approver)
        statements = [
            str(item.get("statement"))
            for item in list(current.get("operations") or [])
            if bool(item.get("allowed"))
        ]

        execution_results = self.adapter.execute_statements(statements)
        success_count = sum(1 for item in execution_results if item.get("status") == "success")
        error_count = sum(1 for item in execution_results if item.get("status") == "error")
        if success_count and error_count:
            final_status = "PARTIAL"
            error_message = "Some statements succeeded before later failures occurred."
        elif error_count:
            final_status = "FAILED"
            error_message = "One or more statements failed."
        else:
            final_status = "EXECUTED"
            error_message = None

        updated = self.audit_store.update_execution(
            proposal_id=proposal_id,
            status=final_status,
            execution_results=execution_results,
            error_message=error_message,
        )

        self.schema_manager.invalidate_cache()
        if updated is None:
            raise RuntimeError("Execution finished but proposal was not found when updating status.")

        return _to_api_payload(updated)

    def inspect_schema(self) -> Dict[str, Any]:
        tables = self.schema_manager.get_tables()
        columns = {table: self.schema_manager.get_columns(table) for table in tables}
        return {"tables": tables, "columns": columns}


def _generate_approval_token() -> str:
    return secrets.token_urlsafe(16)


def _to_api_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "proposal_id": row.get("proposal_id"),
        "request_text": row.get("request_text"),
        "backend": row.get("backend"),
        "dialect": row.get("dialect"),
        "source": row.get("source"),
        "status": row.get("status"),
        "approval_token": row.get("approval_token"),
        "has_blocking_risk": bool(row.get("has_blocking_risk")),
        "risk_summary": row.get("risk_summary"),
        "risk_level": row.get("risk_level", row.get("risk_summary", "safe")),
        "notes": list(row.get("notes") or []),
        "operations": list(row.get("operations") or []),
        "execution_results": list(row.get("execution_results") or []),
        "normalized_intent": row.get("normalized_intent") or "",
        "impact_summary": row.get("impact_summary") or "",
        "preflight_checks": list(row.get("preflight_checks") or []),
        "actor_id": row.get("actor_id"),
        "session_id": row.get("session_id"),
        "rejection_reason": row.get("rejection_reason"),
        "error_message": row.get("error_message"),
        "approver": row.get("approver"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "approved_at": row.get("approved_at"),
        "rejected_at": row.get("rejected_at"),
        "executed_at": row.get("executed_at"),
    }
