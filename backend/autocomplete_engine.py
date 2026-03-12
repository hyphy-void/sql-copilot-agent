from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.context_analyzer import QueryContext, detect_context
from backend.parser import extract_alias_map, resolve_table
from backend.schema_manager import SchemaManager


SQL_KEYWORDS = [
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "LEFT",
    "RIGHT",
    "INNER",
    "OUTER",
    "ON",
    "GROUP",
    "BY",
    "ORDER",
    "LIMIT",
    "HAVING",
    "AND",
    "OR",
    "AS",
    "DESC",
    "ASC",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
]


@dataclass
class RuleAutocompleteResult:
    suggestions: List[str]
    context: QueryContext
    alias_map: Dict[str, str]
    table_hint: Optional[str] = None


def merge_suggestions(
    rule_suggestions: List[str], llm_suggestions: List[str], max_suggestions: int
) -> List[str]:
    merged: List[str] = []
    seen = set()

    for candidate in [*rule_suggestions, *llm_suggestions]:
        normalized = candidate.strip()
        if not normalized:
            continue

        key = normalized.lower()
        if key in seen:
            continue

        seen.add(key)
        merged.append(normalized)

        if len(merged) >= max_suggestions:
            break

    return merged


class AutocompleteEngine:
    def __init__(self, schema_manager: SchemaManager) -> None:
        self.schema_manager = schema_manager

    def suggest_rules(
        self, sql: str, cursor: int, max_suggestions: int = 10
    ) -> RuleAutocompleteResult:
        alias_map = extract_alias_map(sql)
        context = detect_context(sql, cursor)

        context_suggestions: List[str] = []
        table_hint: Optional[str] = None

        if context.context_type == "from":
            context_suggestions = self._suggest_tables(context.token_prefix)
        elif context.qualifier:
            table_hint = resolve_table(context.qualifier, alias_map)
            if table_hint and self.schema_manager.has_table(table_hint):
                context_suggestions = self._suggest_qualified_columns(
                    reference=context.qualifier,
                    table_name=table_hint,
                    member_prefix=context.member_prefix,
                )
        elif context.context_type in {"select", "where"}:
            context_suggestions = self._suggest_columns_from_query(
                alias_map=alias_map,
                token_prefix=context.token_prefix,
            )

        keyword_prefix = context.token_prefix if not context.qualifier else ""
        keyword_suggestions = self._suggest_keywords(keyword_prefix)

        merged = merge_suggestions(
            context_suggestions,
            keyword_suggestions,
            max_suggestions=max_suggestions,
        )

        return RuleAutocompleteResult(
            suggestions=merged,
            context=context,
            alias_map=alias_map,
            table_hint=table_hint,
        )

    def _suggest_tables(self, prefix: str) -> List[str]:
        tables = self.schema_manager.get_tables()
        if not prefix:
            return tables
        lowered = prefix.lower()
        return [table for table in tables if table.lower().startswith(lowered)]

    def _suggest_qualified_columns(
        self, reference: str, table_name: str, member_prefix: str
    ) -> List[str]:
        columns = self.schema_manager.get_columns(table_name)
        suggestions = [f"{reference}.{column['name']}" for column in columns]
        if not member_prefix:
            return suggestions

        lowered = member_prefix.lower()
        return [
            suggestion
            for suggestion in suggestions
            if suggestion.split(".")[-1].lower().startswith(lowered)
        ]

    def _suggest_columns_from_query(
        self, alias_map: Dict[str, str], token_prefix: str
    ) -> List[str]:
        suggestions: List[str] = []

        alias_pairs = [(alias, table) for alias, table in alias_map.items() if alias != table]

        if alias_pairs:
            references = alias_pairs
        else:
            tables = self.schema_manager.get_tables()
            references = [(table, table) for table in tables]

        for reference, table in references:
            if not self.schema_manager.has_table(table):
                continue
            for column in self.schema_manager.get_columns(table):
                suggestions.append(f"{reference}.{column['name']}")

        if not token_prefix:
            return suggestions

        lowered = token_prefix.lower()
        return [
            suggestion
            for suggestion in suggestions
            if suggestion.lower().startswith(lowered)
            or suggestion.split(".")[-1].lower().startswith(lowered)
        ]

    def _suggest_keywords(self, prefix: str) -> List[str]:
        if not prefix:
            return SQL_KEYWORDS[:]

        lowered = prefix.lower()
        return [keyword for keyword in SQL_KEYWORDS if keyword.lower().startswith(lowered)]
