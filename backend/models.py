from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    llm_enabled: bool


class SchemaTablesResponse(BaseModel):
    tables: List[str]


class SchemaColumnsResponse(BaseModel):
    table: str
    columns: List[Dict[str, Any]]


class AutocompleteRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    cursor: int = Field(..., ge=0)
    max_suggestions: int = Field(default=10, ge=1, le=50)
    use_llm: bool = True


class AutocompleteResponse(BaseModel):
    suggestions: List[str]
    mode: Literal["rule_only", "hybrid"]
    debug: Optional[Dict[str, Any]] = None
