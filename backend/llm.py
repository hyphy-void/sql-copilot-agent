from __future__ import annotations

import json
import logging
import os
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


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is not available")

        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def generate_completion(
        self, sql_prefix: str, schema_snapshot: Dict[str, List[str]], context: str
    ) -> List[str]:
        schema_lines = [
            f"{table}({', '.join(columns)})" for table, columns in schema_snapshot.items()
        ]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a SQL copilot. Return 3 short SQL continuation suggestions. "
                    "Output a JSON array of strings only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Context: {context}\n"
                    f"Schema:\n{chr(10).join(schema_lines)}\n\n"
                    "Complete this SQL prefix with practical suggestions:\n"
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


class QwenProvider(BaseLLMProvider):
    def generate_completion(
        self, sql_prefix: str, schema_snapshot: Dict[str, List[str]], context: str
    ) -> List[str]:
        raise NotImplementedError("Qwen provider is reserved for future implementation")


def build_llm_provider() -> Optional[BaseLLMProvider]:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "qwen":
        logger.warning("LLM_PROVIDER=qwen is not implemented yet. Falling back to rule mode.")
        return None

    if provider != "openai":
        logger.warning("Unknown LLM_PROVIDER=%s. Falling back to rule mode.", provider)
        return None

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.info("OPENAI_API_KEY missing. LLM suggestions disabled.")
        return None

    try:
        return OpenAIProvider(api_key=api_key)
    except Exception as exc:  # pragma: no cover - network/dependency runtime behavior
        logger.warning("Failed to initialize OpenAI provider: %s", exc)
        return None


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
