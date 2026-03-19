from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - handled by runtime fallback
    OpenAI = None


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_completion(
        self, sql_prefix: str, schema_snapshot: Dict[str, List[str]], context: str
    ) -> List[str]:
        raise NotImplementedError

    def generate_ddl_candidates(
        self, intent: str, schema_snapshot: Dict[str, List[str]], dialect: str
    ) -> List[str]:
        return []


class OpenAIProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is not available")

        resolved_api_key = api_key or _first_env(
            "OPENAI_API_KEY",
            "OPENAI_COMPATIBLE_API_KEY",
        )
        resolved_base_url = base_url or _first_env(
            "OPENAI_BASE_URL",
            "BASE_URL",
        )

        client_kwargs = {"api_key": resolved_api_key}
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url

        self.client = OpenAI(**client_kwargs)
        self.model = model or _first_env("OPENAI_MODEL", "MODEL_ID") or "gpt-4o-mini"

    def generate_completion(
        self, sql_prefix: str, schema_snapshot: Dict[str, List[str]], context: str
    ) -> List[str]:
        schema_lines = [
            f"{table}({', '.join(columns)})" for table, columns in schema_snapshot.items()
        ]
        repair_mode = "repair" in context.lower()
        repair_hint = ""
        if repair_mode:
            repair_hint = (
                " The SQL prefix may contain typos, duplicated fragments, or malformed identifiers. "
                "Prefer corrected continuations that repair the current trailing fragment before continuing."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a SQL copilot. Return 3 short SQL continuation suggestions. "
                    "Output a JSON array of strings only."
                    f"{repair_hint}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Context: {context}\n"
                    f"Schema:\n{chr(10).join(schema_lines)}\n\n"
                    "Complete this SQL prefix with practical suggestions. "
                    "Return continuations that can be inserted directly at the cursor without explanation.\n"
                    f"{sql_prefix}"
                ),
            },
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=messages,
        )

        content = ""
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""

        return _parse_suggestions(content)

    def generate_ddl_candidates(
        self, intent: str, schema_snapshot: Dict[str, List[str]], dialect: str
    ) -> List[str]:
        schema_lines = [
            f"{table}({', '.join(columns)})" for table, columns in schema_snapshot.items()
        ]
        capability_hint = ""
        if dialect == "sqlite":
            capability_hint = (
                " For SQLite, do not emit CREATE DATABASE or CREATE SCHEMA, "
                "and do not use schema-qualified table names like crm.users."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a SQL schema migration assistant. "
                    "Generate 1-5 SQL DDL statements only. "
                    "Output JSON array of SQL strings. Do not include markdown."
                    f"{capability_hint}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Dialect: {dialect}\n"
                    f"Schema:\n{chr(10).join(schema_lines)}\n\n"
                    f"Intent:\n{intent}\n\n"
                    "Return only DDL SQL statements."
                ),
            },
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=messages,
        )

        content = ""
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""

        return _parse_suggestions(content)


class QwenProvider(BaseLLMProvider):
    def generate_completion(
        self, sql_prefix: str, schema_snapshot: Dict[str, List[str]], context: str
    ) -> List[str]:
        raise NotImplementedError("Qwen provider is reserved for future implementation")


def build_llm_provider() -> Optional[BaseLLMProvider]:
    provider_raw = _first_env("LLM_PROVIDER", "API_PROVIDER") or "openai"
    provider = _normalize_provider(provider_raw)

    if provider == "qwen":
        logger.warning("LLM_PROVIDER=qwen is not implemented yet. Falling back to rule mode.")
        return None

    if provider not in {"openai", "openai_compatible"}:
        logger.warning("Unknown provider=%s. Falling back to rule mode.", provider_raw)
        return None

    api_key = _first_env("OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY")
    if not api_key:
        logger.info(
            "API key missing (OPENAI_API_KEY or OPENAI_COMPATIBLE_API_KEY). "
            "LLM suggestions disabled."
        )
        return None

    try:
        return OpenAIProvider(api_key=api_key)
    except Exception as exc:  # pragma: no cover - network/dependency runtime behavior
        logger.warning("Failed to initialize OpenAI provider: %s", exc)
        return None


def _first_env(*keys: str) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _normalize_provider(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if normalized in {"openai_compatible", "openai_compat"}:
        return "openai_compatible"
    return normalized


def _parse_suggestions(raw_output: str) -> List[str]:
    text = (raw_output or "").strip()
    if not text:
        return []

    # Preferred format: JSON array.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass

    lines = [
        line.strip().strip("` ")
        for line in text.splitlines()
        if line.strip() and not line.strip().lower().startswith("json")
    ]

    cleaned: List[str] = []
    for line in lines:
        candidate = line.lstrip("-0123456789. ").strip()
        if candidate:
            cleaned.append(candidate)

    return cleaned
