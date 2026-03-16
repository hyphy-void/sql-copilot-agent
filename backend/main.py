from __future__ import annotations

import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from agent.graph import AutocompleteGraphEngine
from backend.audit_store import AuditStore
from backend.autocomplete_engine import AutocompleteEngine
from backend.database import ConnectionManager, SQLiteAdapter
from backend.ddl_planner import DDLPlanner
from backend.llm import BaseLLMProvider, build_llm_provider
from backend.models import (
    ApprovalDecision,
    ApproveProposalResponse,
    AutocompleteRequest,
    AutocompleteResponse,
    ChatPlanSummary,
    ChatPlanRequest,
    ChatPlanResponse,
    DDLProposal,
    DatabaseCapabilitiesResponse,
    HealthResponse,
    RejectProposalRequest,
    RejectProposalResponse,
    SchemaColumnsResponse,
    SchemaOverviewResponse,
    SchemaOverviewTable,
    SchemaTablesResponse,
)
from backend.schema_manager import SchemaManager
from backend.tool_registry import ToolRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "db" / "demo.db"
DEFAULT_INIT_SQL_PATH = ROOT_DIR / "db" / "init.sql"
DEFAULT_AUDIT_DB_PATH = ROOT_DIR / "db" / "audit.db"
FRONTEND_DIR = ROOT_DIR / "frontend"


def create_app(
    db_path: Optional[str | Path] = None,
    init_sql_path: Optional[str | Path] = None,
    audit_db_path: Optional[str | Path] = None,
    enable_llm: bool = True,
    llm_provider: BaseLLMProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="SQL Copilot Agent", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    configured_db_path = Path(db_path or os.getenv("DB_PATH", DEFAULT_DB_PATH))
    configured_init_sql = Path(init_sql_path or os.getenv("INIT_SQL_PATH", DEFAULT_INIT_SQL_PATH))
    configured_audit_db_path = Path(audit_db_path or os.getenv("AUDIT_DB_PATH", DEFAULT_AUDIT_DB_PATH))

    if db_path is not None:
        connection_manager = ConnectionManager(SQLiteAdapter(configured_db_path))
    else:
        connection_manager = ConnectionManager.from_env(default_db_path=configured_db_path)

    schema_manager = SchemaManager(connection_manager.get_adapter())
    if connection_manager.get_adapter().backend_name == "sqlite":
        schema_manager.initialize(configured_init_sql)

    autocomplete_engine = AutocompleteEngine(schema_manager)
    effective_llm_provider = llm_provider
    if effective_llm_provider is None and enable_llm:
        effective_llm_provider = build_llm_provider()

    graph_engine = AutocompleteGraphEngine(
        autocomplete_engine=autocomplete_engine,
        schema_manager=schema_manager,
        llm_provider=effective_llm_provider,
    )
    ddl_planner = DDLPlanner(llm_provider=effective_llm_provider)
    audit_store = AuditStore(configured_audit_db_path)
    tool_registry = ToolRegistry(
        adapter=connection_manager.get_adapter(),
        schema_manager=schema_manager,
        planner=ddl_planner,
        audit_store=audit_store,
    )

    app.state.schema_manager = schema_manager
    app.state.graph_engine = graph_engine
    app.state.connection_manager = connection_manager
    app.state.tool_registry = tool_registry

    @app.get("/", include_in_schema=False)
    def serve_frontend() -> FileResponse:
        index_file = FRONTEND_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="Frontend file not found")
        return FileResponse(
            index_file,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/monaco.js", include_in_schema=False)
    def serve_monaco_script() -> FileResponse:
        script_file = FRONTEND_DIR / "monaco.js"
        if not script_file.exists():
            raise HTTPException(status_code=404, detail="Monaco script not found")
        return FileResponse(
            script_file,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            llm_enabled=app.state.graph_engine.llm_provider is not None,
        )

    @app.get("/schema/tables", response_model=SchemaTablesResponse)
    def get_tables() -> SchemaTablesResponse:
        tables = app.state.schema_manager.get_tables()
        return SchemaTablesResponse(tables=tables)

    @app.get("/schema/overview", response_model=SchemaOverviewResponse)
    def get_schema_overview() -> SchemaOverviewResponse:
        overview_tables: list[SchemaOverviewTable] = []

        for table in app.state.schema_manager.get_tables():
            columns = app.state.schema_manager.get_columns(table)
            prioritized = sorted(
                columns,
                key=lambda column: (0 if bool(column.get("pk")) else 1, str(column.get("name"))),
            )
            key_columns = [str(column.get("name")) for column in prioritized[:5] if column.get("name")]

            overview_tables.append(
                SchemaOverviewTable(
                    table=table,
                    column_count=len(columns),
                    key_columns=key_columns,
                )
            )

        return SchemaOverviewResponse(tables=overview_tables)

    @app.get("/schema/columns/{table}", response_model=SchemaColumnsResponse)
    def get_columns(table: str) -> SchemaColumnsResponse:
        if table not in app.state.schema_manager.get_tables():
            raise HTTPException(status_code=404, detail=f"Table '{table}' not found")

        columns = app.state.schema_manager.get_columns(table)
        return SchemaColumnsResponse(table=table, columns=columns)

    @app.get("/db/capabilities", response_model=DatabaseCapabilitiesResponse)
    def db_capabilities() -> DatabaseCapabilitiesResponse:
        capabilities = app.state.connection_manager.get_capabilities()
        return DatabaseCapabilitiesResponse(**capabilities)

    @app.post("/chat/plan", response_model=ChatPlanResponse)
    def chat_plan(request: ChatPlanRequest) -> ChatPlanResponse:
        proposal = app.state.tool_registry.propose_ddl(
            prompt=request.prompt,
            use_llm=request.use_llm,
        )
        summary = _build_chat_plan_summary(proposal)
        if proposal.get("has_blocking_risk"):
            message = "Proposal created with blocked operations. Please revise request before approval."
        else:
            message = "Proposal created. Review then approve to execute."
        return ChatPlanResponse(
            proposal=DDLProposal(**proposal),
            message=message,
            summary=ChatPlanSummary(**summary),
        )

    @app.get("/chat/proposals/{proposal_id}", response_model=DDLProposal)
    def get_chat_proposal(proposal_id: str) -> DDLProposal:
        proposal = app.state.tool_registry.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
        return DDLProposal(**proposal)

    @app.post("/chat/proposals/{proposal_id}/approve", response_model=ApproveProposalResponse)
    def approve_chat_proposal(
        proposal_id: str, request: ApprovalDecision
    ) -> ApproveProposalResponse:
        try:
            proposal = app.state.tool_registry.approve_proposal(
                proposal_id=proposal_id,
                approval_token=request.approval_token,
                approver=request.approver,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        message = (
            "DDL executed with failures." if proposal.get("status") == "FAILED" else "DDL executed."
        )
        return ApproveProposalResponse(proposal=DDLProposal(**proposal), message=message)

    @app.post("/chat/proposals/{proposal_id}/reject", response_model=RejectProposalResponse)
    def reject_chat_proposal(
        proposal_id: str, request: RejectProposalRequest
    ) -> RejectProposalResponse:
        proposal = app.state.tool_registry.reject_proposal(
            proposal_id=proposal_id,
            reason=request.reason,
        )
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
        return RejectProposalResponse(
            proposal=DDLProposal(**proposal),
            message="Proposal rejected.",
        )

    @app.post("/autocomplete", response_model=AutocompleteResponse)
    def autocomplete(request: AutocompleteRequest) -> AutocompleteResponse:
        cursor = max(0, min(request.cursor, len(request.sql)))
        start = perf_counter()

        result = app.state.graph_engine.run(
            sql=request.sql,
            cursor=cursor,
            max_suggestions=request.max_suggestions,
            use_llm=request.use_llm,
        )

        elapsed_ms = round((perf_counter() - start) * 1000, 3)
        debug_payload = result.get("debug") or {}

        timings = dict(debug_payload.get("timings_ms", {}))
        timings["total_ms"] = elapsed_ms
        debug_payload["timings_ms"] = timings

        return AutocompleteResponse(
            suggestions=result.get("suggestions", []),
            mode=result.get("mode", "rule_only"),
            debug=debug_payload,
        )

    return app


app = create_app()


def _build_chat_plan_summary(proposal: dict[str, object]) -> dict[str, object]:
    operations = list(proposal.get("operations") or [])
    allowed_count = sum(1 for operation in operations if bool(operation.get("allowed")))
    blocked_count = len(operations) - allowed_count

    if blocked_count > 0:
        hint = (
            "包含高风险语句，请先修改请求再执行 / Blocked operations found. Revise request before execution."
        )
    else:
        hint = (
            "确认无误后输入 APPROVE 再执行 / Review operations, then type APPROVE before execution."
        )

    return {
        "allowed_count": allowed_count,
        "blocked_count": blocked_count,
        "next_action_hint": hint,
    }
