from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    llm_enabled: bool
    safe_mode: str | None = None


class SchemaTablesResponse(BaseModel):
    tables: List[str]


class SchemaOverviewTable(BaseModel):
    table: str
    column_count: int
    key_columns: List[str]


class SchemaOverviewResponse(BaseModel):
    tables: List[SchemaOverviewTable]


class SchemaColumnsResponse(BaseModel):
    table: str
    columns: List[Dict[str, Any]]


class AutocompleteRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    cursor: int = Field(..., ge=0)
    max_suggestions: int = Field(default=10, ge=1, le=50)
    use_llm: bool = True


AutocompleteMode = Literal["rule_only", "hybrid", "recovery", "join_infer"]


class SuggestionItem(BaseModel):
    text: str
    source: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason_code: str
    reason: str


class AutocompleteResponse(BaseModel):
    suggestions: List[str]
    mode: AutocompleteMode
    strategy: AutocompleteMode
    items: List[SuggestionItem] = Field(default_factory=list)
    debug: Optional[Dict[str, Any]] = None


DDLStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED", "PARTIAL"]
DDLRiskLevel = Literal["safe", "warning", "blocked"]
StatementStatus = Literal["success", "error"]


class ChatPlanRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    use_llm: bool = True
    actor_id: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None


class DDLOperation(BaseModel):
    statement: str
    operation_type: str
    allowed: bool
    risk_level: DDLRiskLevel
    reason: str
    impact_summary: str | None = None
    preflight_checks: List[Dict[str, Any]] = Field(default_factory=list)
    idempotency: str | None = None
    rollback_strategy: str | None = None


class ExecutionResult(BaseModel):
    statement: str
    status: StatementStatus
    duration_ms: float
    error: Optional[str] = None


class DDLProposal(BaseModel):
    proposal_id: str
    request_text: str
    backend: str
    dialect: str
    source: str
    status: DDLStatus
    approval_token: str
    has_blocking_risk: bool
    risk_summary: str
    risk_level: DDLRiskLevel
    notes: List[str]
    operations: List[DDLOperation]
    execution_results: List[ExecutionResult]
    normalized_intent: str = ""
    impact_summary: str = ""
    preflight_checks: List[Dict[str, Any]] = Field(default_factory=list)
    actor_id: Optional[str] = None
    session_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    error_message: Optional[str] = None
    approver: Optional[str] = None
    created_at: str
    updated_at: str
    approved_at: Optional[str] = None
    rejected_at: Optional[str] = None
    executed_at: Optional[str] = None


class ChatPlanSummary(BaseModel):
    allowed_count: int
    blocked_count: int
    next_action_hint: str


class ChatPlanResponse(BaseModel):
    proposal: DDLProposal
    message: str
    summary: ChatPlanSummary


class ApprovalDecision(BaseModel):
    approval_token: str = Field(..., min_length=6)
    approver: Optional[str] = None


class ApproveProposalResponse(BaseModel):
    proposal: DDLProposal
    message: str


class RejectProposalRequest(BaseModel):
    reason: Optional[str] = None


class RejectProposalResponse(BaseModel):
    proposal: DDLProposal
    message: str


class DatabaseCapabilitiesResponse(BaseModel):
    backend: str
    dialect: str
    connected: bool
    supports_create_database: bool
    allowed_ddl: List[str]
