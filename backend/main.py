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
from backend.autocomplete_engine import AutocompleteEngine
from backend.llm import BaseLLMProvider, build_llm_provider
from backend.models import (
    AutocompleteRequest,
    AutocompleteResponse,
    HealthResponse,
    SchemaColumnsResponse,
    SchemaTablesResponse,
)
from backend.schema_manager import SchemaManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "db" / "demo.db"
DEFAULT_INIT_SQL_PATH = ROOT_DIR / "db" / "init.sql"
FRONTEND_DIR = ROOT_DIR / "frontend"


def create_app(
    db_path: Optional[str | Path] = None,
    init_sql_path: Optional[str | Path] = None,
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

    schema_manager = SchemaManager(configured_db_path)
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

    app.state.schema_manager = schema_manager
    app.state.graph_engine = graph_engine

    @app.get("/", include_in_schema=False)
    def serve_frontend() -> FileResponse:
        index_file = FRONTEND_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="Frontend file not found")
        return FileResponse(index_file)

    @app.get("/monaco.js", include_in_schema=False)
    def serve_monaco_script() -> FileResponse:
        script_file = FRONTEND_DIR / "monaco.js"
        if not script_file.exists():
            raise HTTPException(status_code=404, detail="Monaco script not found")
        return FileResponse(script_file)

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

    @app.get("/schema/columns/{table}", response_model=SchemaColumnsResponse)
    def get_columns(table: str) -> SchemaColumnsResponse:
        if table not in app.state.schema_manager.get_tables():
            raise HTTPException(status_code=404, detail=f"Table '{table}' not found")

        columns = app.state.schema_manager.get_columns(table)
        return SchemaColumnsResponse(table=table, columns=columns)

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
