from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any, Dict, List, TypedDict

from backend.autocomplete_engine import (
    AutocompleteEngine,
    RankedSuggestion,
    merge_suggestions,
)
from backend.context_analyzer import detect_context
from backend.llm import BaseLLMProvider
from backend.parser import extract_alias_map
from backend.schema_manager import SchemaManager

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:  # pragma: no cover
    END = None
    StateGraph = None
    HAS_LANGGRAPH = False


class AutocompleteState(TypedDict, total=False):
    sql: str
    cursor: int
    max_suggestions: int
    use_llm: bool
    alias_map: Dict[str, str]
    context: Dict[str, Any]
    table: str | None
    rule_items: List[RankedSuggestion]
    llm_items: List[RankedSuggestion]
    items: List[RankedSuggestion]
    suggestions: List[str]
    strategy: str
    debug: Dict[str, Any]
    timings: Dict[str, float]
    errors: List[str]


class AutocompleteGraphEngine:
    def __init__(
        self,
        autocomplete_engine: AutocompleteEngine,
        schema_manager: SchemaManager,
        llm_provider: BaseLLMProvider | None,
    ) -> None:
        self.autocomplete_engine = autocomplete_engine
        self.schema_manager = schema_manager
        self.llm_provider = llm_provider
        self.graph = self._build_graph() if HAS_LANGGRAPH else None
        if self.graph is None:
            logger.warning("LangGraph unavailable, using sequential fallback pipeline.")

    def run(
        self,
        sql: str,
        cursor: int,
        max_suggestions: int = 10,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        state: AutocompleteState = {
            "sql": sql,
            "cursor": cursor,
            "max_suggestions": max_suggestions,
            "use_llm": use_llm,
            "errors": [],
            "timings": {},
        }
        final_state = self.graph.invoke(state) if self.graph is not None else self._run_sequential(state)
        return {
            "suggestions": final_state.get("suggestions", []),
            "mode": final_state.get("strategy", "rule_only"),
            "strategy": final_state.get("strategy", "rule_only"),
            "items": [self._serialize_item(item) for item in final_state.get("items", [])],
            "debug": final_state.get("debug", {}),
        }

    def _build_graph(self):
        workflow = StateGraph(AutocompleteState)
        workflow.add_node("parse", self._parse_node)
        workflow.add_node("schema", self._schema_node)
        workflow.add_node("llm", self._llm_node)
        workflow.add_node("rank", self._rank_node)
        workflow.set_entry_point("parse")
        workflow.add_edge("parse", "schema")
        workflow.add_edge("schema", "llm")
        workflow.add_edge("llm", "rank")
        workflow.add_edge("rank", END)
        return workflow.compile()

    def _run_sequential(self, initial_state: AutocompleteState) -> AutocompleteState:
        state = dict(initial_state)
        state.update(self._parse_node(state))
        state.update(self._schema_node(state))
        state.update(self._llm_node(state))
        state.update(self._rank_node(state))
        return state

    def _parse_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()
        alias_map = extract_alias_map(state["sql"])
        context = detect_context(state["sql"], state["cursor"]).to_dict()
        timings = dict(state.get("timings", {}))
        timings["parse_ms"] = round((perf_counter() - start) * 1000, 3)
        return {"alias_map": alias_map, "context": context, "timings": timings}

    def _schema_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()
        result = self.autocomplete_engine.suggest_rules(
            sql=state["sql"],
            cursor=state["cursor"],
            max_suggestions=state["max_suggestions"],
        )
        timings = dict(state.get("timings", {}))
        timings["schema_ms"] = round((perf_counter() - start) * 1000, 3)
        return {
            "rule_items": result.items,
            "alias_map": result.alias_map,
            "context": result.context.to_dict(),
            "table": result.table_hint,
            "strategy": result.strategy,
            "timings": timings,
        }

    def _llm_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()
        errors = list(state.get("errors", []))
        llm_items: List[RankedSuggestion] = []
        if state.get("use_llm", True) and self.llm_provider is not None:
            try:
                repair_mode = _should_use_llm_repair_mode(
                    sql=state["sql"],
                    cursor=state["cursor"],
                    context=state.get("context", {}),
                )
                llm_context = str(state.get("context", {}).get("clause") or "unknown")
                if repair_mode:
                    llm_context = f"{llm_context}|repair"
                suggestions = self.llm_provider.generate_completion(
                    sql_prefix=state["sql"][: state["cursor"]],
                    schema_snapshot=self.schema_manager.get_schema_snapshot(),
                    context=llm_context,
                )
                filtered_suggestions = _filter_llm_suggestions(
                    suggestions=suggestions,
                    sql_prefix=state["sql"][: state["cursor"]],
                    context=state.get("context", {}),
                    alias_map=state.get("alias_map", {}),
                    schema_snapshot=self.schema_manager.get_schema_snapshot(),
                    repair_mode=repair_mode,
                )
                llm_items = [
                    RankedSuggestion(
                        text=suggestion,
                        source="llm",
                        confidence=0.82 if repair_mode else 0.7,
                        reason_code="semantic_repair" if repair_mode else "semantic_prediction",
                        reason=(
                            "The language model repaired the malformed SQL tail before continuing."
                            if repair_mode
                            else "Semantic continuation predicted by the language model."
                        ),
                    )
                    for suggestion in filtered_suggestions
                ]
            except Exception as exc:  # pragma: no cover
                logger.warning("LLM generation failed: %s", exc)
                errors.append("model_error")

        timings = dict(state.get("timings", {}))
        timings["llm_ms"] = round((perf_counter() - start) * 1000, 3)
        return {"llm_items": llm_items, "errors": errors, "timings": timings}

    def _rank_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()
        rule_items = state.get("rule_items", [])
        llm_items = state.get("llm_items", [])
        max_suggestions = state.get("max_suggestions", 10)

        llm_first = bool(state.get("use_llm", True) and llm_items)
        merged = (
            merge_suggestions(llm_items, rule_items, max_suggestions)
            if llm_first
            else merge_suggestions(rule_items, llm_items, max_suggestions)
        )

        context = state.get("context", {})
        strategy = str(state.get("strategy") or "rule_only")
        if llm_first and strategy == "rule_only":
            strategy = "hybrid"

        timings = dict(state.get("timings", {}))
        timings["rank_ms"] = round((perf_counter() - start) * 1000, 3)
        debug = {
            "context": context.get("context_type", "unknown"),
            "clause": context.get("clause", "unknown"),
            "table": state.get("table"),
            "alias_map": state.get("alias_map", {}),
            "chosen_strategy": strategy,
            "repair_mode": any(item.reason_code == "semantic_repair" for item in llm_items),
            "ui_context_label": _build_context_label(str(context.get("clause") or context.get("context_type") or "unknown")),
            "suggestion_reasons": {item.text: item.reason for item in merged},
            "suggestion_sources": {item.text: item.source for item in merged},
            "rule_suggestions": [item.text for item in rule_items],
            "llm_suggestions": [item.text for item in llm_items],
            "final_rank_sources": [item.source for item in merged],
            "fallback_reason": _build_fallback_reason(
                use_llm=bool(state.get("use_llm", True)),
                has_provider=self.llm_provider is not None,
                strategy=strategy,
                errors=state.get("errors", []),
            ),
            "timings_ms": timings,
            "errors": state.get("errors", []),
            "observability": {
                "context": context.get("context_type", "unknown"),
                "chosen_strategy": strategy,
                "repair_mode": any(item.reason_code == "semantic_repair" for item in llm_items),
                "fallback_reason": _build_fallback_reason(
                    use_llm=bool(state.get("use_llm", True)),
                    has_provider=self.llm_provider is not None,
                    strategy=strategy,
                    errors=state.get("errors", []),
                ),
                "llm_latency_ms": timings.get("llm_ms", 0.0),
                "schema_latency_ms": timings.get("schema_ms", 0.0),
                "final_rank_sources": [item.source for item in merged],
            },
        }
        return {
            "items": merged,
            "suggestions": [item.text for item in merged],
            "strategy": strategy,
            "debug": debug,
            "timings": timings,
        }

    def _serialize_item(self, item: RankedSuggestion) -> Dict[str, Any]:
        return {
            "text": item.text,
            "source": item.source,
            "confidence": round(item.confidence, 3),
            "reason_code": item.reason_code,
            "reason": item.reason,
        }


def _build_fallback_reason(
    use_llm: bool,
    has_provider: bool,
    strategy: str,
    errors: List[str],
) -> str | None:
    if strategy == "hybrid":
        return None
    if not use_llm:
        return "LLM disabled, returning deterministic rule-based suggestions only."
    if not has_provider:
        return "LLM unavailable, falling back to deterministic suggestions."
    if "model_error" in errors:
        return "LLM request failed, falling back to deterministic suggestions."
    if strategy == "recovery":
        return "Input looked incomplete, so recovery suggestions were prioritized."
    if strategy == "join_infer":
        return "Join inference matched schema relationships more strongly than LLM output."
    return "No valid LLM results, returning deterministic suggestions."


def _build_context_label(clause: str) -> str:
    labels = {
        "select": "SELECT column suggestions",
        "from": "FROM table suggestions",
        "join": "JOIN path suggestions",
        "on": "JOIN condition suggestions",
        "where": "WHERE condition suggestions",
        "having": "HAVING condition suggestions",
        "group_by": "GROUP BY suggestions",
        "order_by": "ORDER BY suggestions",
        "unknown": "General SQL suggestions",
    }
    return labels.get(clause, labels["unknown"])


def _should_use_llm_repair_mode(
    sql: str,
    cursor: int,
    context: Dict[str, Any],
) -> bool:
    prefix = sql[:cursor]
    parse_confidence = float(context.get("parse_confidence") or 0.0)
    if parse_confidence < 0.45:
        return True

    suspicious_patterns = [
        r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*[A-Za-z_]{2,}\.[A-Za-z_]",
        r"\b(from|where|join|on|having)\b\s+[A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*\s*,",
        r"[A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_]",
    ]
    return any(re.search(pattern, prefix, re.IGNORECASE) for pattern in suspicious_patterns)


_QUALIFIED_REF_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_COMPARISON_PATTERN = re.compile(
    r"(=|!=|<>|>=|<=|>|<|\blike\b|\bin\b|\bbetween\b|\bis\b|\bexists\b)",
    re.IGNORECASE,
)
_BOOLEAN_CONNECTOR_PATTERN = re.compile(r"\b(and|or)\b", re.IGNORECASE)
_AGGREGATE_PATTERN = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)


def _filter_llm_suggestions(
    suggestions: List[str],
    sql_prefix: str,
    context: Dict[str, Any],
    alias_map: Dict[str, str],
    schema_snapshot: Dict[str, List[str]],
    repair_mode: bool = False,
) -> List[str]:
    clause = str(context.get("clause") or context.get("context_type") or "unknown")
    filtered: List[str] = []

    for suggestion in suggestions:
        candidate = (suggestion or "").strip()
        if not candidate:
            continue
        if not _passes_clause_guard(candidate, clause):
            continue
        if not _passes_reference_guard(
            candidate,
            alias_map,
            schema_snapshot,
            sql_prefix=sql_prefix,
            allow_soft_alias=repair_mode,
        ):
            continue
        filtered.append(candidate)

    return filtered


def _passes_clause_guard(candidate: str, clause: str) -> bool:
    normalized = candidate.strip()
    upper = normalized.upper()

    if clause in {"where", "on", "having"}:
        if _AGGREGATE_PATTERN.search(normalized) and clause != "having":
            return False
        if not (
            _COMPARISON_PATTERN.search(normalized)
            or _BOOLEAN_CONNECTOR_PATTERN.search(normalized)
        ):
            return False

    if clause == "select" and upper.startswith(("WHERE ", "JOIN ", "ON ", "GROUP BY ", "ORDER BY ")):
        return False

    if clause in {"from", "join"} and upper.startswith(("WHERE ", "HAVING ")):
        return False

    return True


def _passes_reference_guard(
    candidate: str,
    alias_map: Dict[str, str],
    schema_snapshot: Dict[str, List[str]],
    sql_prefix: str,
    allow_soft_alias: bool = False,
) -> bool:
    column_lookup = {
        table: {column.lower() for column in columns}
        for table, columns in schema_snapshot.items()
    }
    valid_refs = set(alias_map.keys()) | set(schema_snapshot.keys())
    seen_qualifiers = {qualifier for qualifier, _ in _QUALIFIED_REF_PATTERN.findall(sql_prefix)}

    for qualifier, column in _QUALIFIED_REF_PATTERN.findall(candidate):
        if qualifier not in valid_refs:
            if allow_soft_alias and qualifier in seen_qualifiers:
                continue
            return False
        table = alias_map.get(qualifier, qualifier)
        if table not in column_lookup:
            if allow_soft_alias and qualifier in seen_qualifiers:
                continue
            return False
        if column.lower() not in column_lookup[table]:
            return False

    return True
