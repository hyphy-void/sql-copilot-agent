from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str | None
    enabled: bool


@dataclass(frozen=True)
class AppConfig:
    db_backend: str
    db_path: Path
    init_sql_path: Path
    audit_db_path: Path
    default_max_suggestions: int
    safe_mode: str
    llm: LLMConfig

    @classmethod
    def from_env(
        cls,
        root_dir: Path,
        db_path: str | Path | None = None,
        init_sql_path: str | Path | None = None,
        audit_db_path: str | Path | None = None,
        enable_llm: bool = True,
    ) -> "AppConfig":
        default_db_path = root_dir / "db" / "demo.db"
        default_init_sql_path = root_dir / "db" / "init.sql"
        default_audit_db_path = root_dir / "db" / "audit.db"

        provider = _first_env("LLM_PROVIDER", "API_PROVIDER") or "openai"
        model = _first_env("OPENAI_MODEL", "MODEL_ID") or "gpt-4o-mini"
        base_url = _first_env("OPENAI_BASE_URL", "BASE_URL")
        api_key = _first_env("OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY")

        return cls(
            db_backend=(_first_env("DB_BACKEND") or "sqlite").lower(),
            db_path=Path(db_path or _first_env("DB_PATH") or default_db_path),
            init_sql_path=Path(init_sql_path or _first_env("INIT_SQL_PATH") or default_init_sql_path),
            audit_db_path=Path(audit_db_path or _first_env("AUDIT_DB_PATH") or default_audit_db_path),
            default_max_suggestions=int(_first_env("DEFAULT_MAX_SUGGESTIONS") or "10"),
            safe_mode=_first_env("SAFE_DDL_MODE") or "strict",
            llm=LLMConfig(
                provider=provider,
                model=model,
                base_url=base_url,
                enabled=bool(enable_llm and api_key),
            ),
        )


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None
