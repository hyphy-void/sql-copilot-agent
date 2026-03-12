from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Dict, List, TypedDict

from backend.autocomplete_engine import AutocompleteEngine, merge_suggestions
from backend.context_analyzer import detect_context
from backend.llm import BaseLLMProvider
from backend.parser import extract_alias_map
from backend.schema_manager import SchemaManager

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - runtime fallback
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
    rule_suggestions: List[str]
    llm_suggestions: List[str]
    suggestions: List[str]
    mode: str
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

        if self.graph is not None:
            final_state = self.graph.invoke(state)
        else:
            final_state = self._run_sequential(state)

        return {
            "suggestions": final_state.get("suggestions", []),
            "mode": final_state.get("mode", "rule_only"),
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

        return {
            "alias_map": alias_map,
            "context": context,
            "timings": timings,
        }

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
            "rule_suggestions": result.suggestions,
            "alias_map": result.alias_map,
            "context": result.context.to_dict(),
            "table": result.table_hint,
            "timings": timings,
        }

    def _llm_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()
        errors = list(state.get("errors", []))

        llm_suggestions: List[str] = []
        if state.get("use_llm", True) and self.llm_provider is not None:
            try:
                llm_suggestions = self.llm_provider.generate_completion(
                    sql_prefix=state["sql"][: state["cursor"]],
                    schema_snapshot=self.schema_manager.get_schema_snapshot(),
                    context=state.get("context", {}).get("context_type", "unknown"),
                )
            except Exception as exc:  # pragma: no cover - runtime/network behavior
                logger.warning("LLM generation failed: %s", exc)
                errors.append("model_error")

        timings = dict(state.get("timings", {}))
        timings["llm_ms"] = round((perf_counter() - start) * 1000, 3)

        return {
            "llm_suggestions": llm_suggestions,
            "errors": errors,
            "timings": timings,
        }

    def _rank_node(self, state: AutocompleteState) -> AutocompleteState:
        start = perf_counter()

        rule_suggestions = state.get("rule_suggestions", [])
        llm_suggestions = state.get("llm_suggestions", [])
        max_suggestions = state.get("max_suggestions", 10)
        llm_first = bool(state.get("use_llm", True) and llm_suggestions)

        if llm_first:
            merged = merge_suggestions(llm_suggestions, rule_suggestions, max_suggestions)
            source_order = [("llm", llm_suggestions), ("rule", rule_suggestions)]
        else:
            merged = merge_suggestions(rule_suggestions, llm_suggestions, max_suggestions)
            source_order = [("rule", rule_suggestions), ("llm", llm_suggestions)]

        source_by_key: Dict[str, str] = {}

        for source, source_suggestions in source_order:
            for suggestion in source_suggestions:
                key = _normalize_suggestion_key(suggestion)
                if key and key not in source_by_key:
                    source_by_key[key] = source

        suggestion_sources: Dict[str, str] = {}
        for suggestion in merged:
            key = _normalize_suggestion_key(suggestion)
            suggestion_sources[suggestion] = source_by_key.get(key, "rule")

        timings = dict(state.get("timings", {}))
        timings["rank_ms"] = round((perf_counter() - start) * 1000, 3)

        mode = "hybrid" if state.get("use_llm", True) and llm_suggestions else "rule_only"

        debug = {
            "context": state.get("context", {}).get("context_type", "unknown"),
            "table": state.get("table"),
            "alias_map": state.get("alias_map", {}),
            "rule_suggestions": rule_suggestions,
            "llm_suggestions": llm_suggestions,
            "suggestion_sources": suggestion_sources,
            "timings_ms": timings,
            "errors": state.get("errors", []),
        }

        return {
            "suggestions": merged,
            "mode": mode,
            "debug": debug,
            "timings": timings,
        }


def _normalize_suggestion_key(value: str) -> str:
    return value.strip().lower()
