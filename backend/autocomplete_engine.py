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
    "LEFT JOIN",
    "RIGHT JOIN",
    "INNER JOIN",
    "OUTER JOIN",
    "ON",
    "GROUP BY",
    "ORDER BY",
    "LIMIT",
    "HAVING",
    "AND",
    "OR",
    "AS",
    "DESC",
    "ASC",
    "COUNT(",
    "SUM(",
    "AVG(",
    "MIN(",
    "MAX(",
]


@dataclass
class RankedSuggestion:
    text: str
    source: str
    confidence: float
    reason_code: str
    reason: str


@dataclass
class RuleAutocompleteResult:
    suggestions: List[str]
    items: List[RankedSuggestion]
    context: QueryContext
    alias_map: Dict[str, str]
    table_hint: Optional[str] = None
    strategy: str = "rule_only"


def merge_suggestions(
    primary: List[RankedSuggestion],
    secondary: List[RankedSuggestion],
    max_suggestions: int,
) -> List[RankedSuggestion]:
    merged: List[RankedSuggestion] = []
    seen = set()

    for candidate in [*primary, *secondary]:
        normalized = candidate.text.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
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

        suggestion_items: List[RankedSuggestion] = []
        table_hint: Optional[str] = None
        strategy = "rule_only"

        if self._needs_recovery(sql, cursor, context):
            suggestion_items.extend(self._suggest_recovery(sql, cursor))
            strategy = "recovery"

        if context.context_type == "from":
            if context.clause == "join":
                join_items = self._suggest_join_paths(sql, alias_map, context)
                if join_items:
                    suggestion_items.extend(join_items)
                    strategy = "join_infer"
            suggestion_items.extend(self._suggest_tables(context.token_prefix))
        elif context.qualifier:
            table_hint = resolve_table(context.qualifier, alias_map)
            if table_hint and self.schema_manager.has_table(table_hint):
                suggestion_items.extend(
                    self._suggest_qualified_columns(
                        reference=context.qualifier,
                        table_name=table_hint,
                        member_prefix=context.member_prefix,
                        context=context,
                    )
                )
        elif context.context_type in {"select", "where", "unknown"}:
            suggestion_items.extend(
                self._suggest_columns_from_query(
                    sql=sql,
                    alias_map=alias_map,
                    token_prefix=context.token_prefix,
                    context=context,
                )
            )

        keyword_prefix = context.token_prefix if not context.qualifier else ""
        suggestion_items.extend(self._suggest_keywords(keyword_prefix, context))

        ranked = self._rank_suggestions(suggestion_items, context)
        ranked = ranked[:max_suggestions]
        return RuleAutocompleteResult(
            suggestions=[item.text for item in ranked],
            items=ranked,
            context=context,
            alias_map=alias_map,
            table_hint=table_hint,
            strategy=strategy,
        )

    def _needs_recovery(self, sql: str, cursor: int, context: QueryContext) -> bool:
        prefix = sql[:cursor]
        if prefix.count("(") > prefix.count(")"):
            return True
        return (
            context.context_type == "unknown"
            and context.parse_confidence < 0.4
            and any(token in prefix.upper() for token in ("SELECT", "FROM"))
        )

    def _suggest_recovery(self, sql: str, cursor: int) -> List[RankedSuggestion]:
        prefix = sql[:cursor].rstrip()
        suggestions: List[RankedSuggestion] = []

        if prefix.upper().endswith("WHERE"):
            suggestions.append(
                RankedSuggestion(
                    text="WHERE 1 = 1",
                    source="recovery",
                    confidence=0.78,
                    reason_code="syntax_recovery_where",
                    reason="Recover an incomplete WHERE clause with a valid predicate scaffold.",
                )
            )

        if prefix.count("(") > prefix.count(")"):
            suggestions.append(
                RankedSuggestion(
                    text=f"{prefix})",
                    source="recovery",
                    confidence=0.72,
                    reason_code="syntax_recovery_parenthesis",
                    reason="Recover unmatched parentheses before continuing the query.",
                )
            )

        if not suggestions:
            suggestions.append(
                RankedSuggestion(
                    text="SELECT * FROM orders",
                    source="recovery",
                    confidence=0.55,
                    reason_code="syntax_recovery_generic",
                    reason="Fallback recovery suggestion for incomplete SQL input.",
                )
            )

        return suggestions

    def _suggest_tables(self, prefix: str) -> List[RankedSuggestion]:
        tables = self.schema_manager.get_tables()
        if prefix:
            lowered = prefix.lower()
            tables = [table for table in tables if table.lower().startswith(lowered)]

        return [
            RankedSuggestion(
                text=table,
                source="rule",
                confidence=0.78 if prefix else 0.68,
                reason_code="table_match",
                reason="Matched known schema table for the current FROM/JOIN context.",
            )
            for table in tables
        ]

    def _suggest_qualified_columns(
        self,
        reference: str,
        table_name: str,
        member_prefix: str,
        context: QueryContext,
    ) -> List[RankedSuggestion]:
        columns = self.schema_manager.get_columns(table_name)
        suggestions: List[RankedSuggestion] = []
        lowered = member_prefix.lower()

        for column in columns:
            name = str(column["name"])
            text = f"{reference}.{name}"
            if lowered and not name.lower().startswith(lowered):
                continue
            suggestions.append(
                RankedSuggestion(
                    text=text,
                    source="rule",
                    confidence=self._base_column_confidence(
                        column_name=name,
                        full_reference=text,
                        sql=reference,
                        context=context,
                    ),
                    reason_code="qualified_column",
                    reason="Column available through the current table alias.",
                )
            )

        return suggestions

    def _suggest_columns_from_query(
        self,
        sql: str,
        alias_map: Dict[str, str],
        token_prefix: str,
        context: QueryContext,
    ) -> List[RankedSuggestion]:
        suggestions: List[RankedSuggestion] = []
        alias_pairs = [(alias, table) for alias, table in alias_map.items() if alias != table]

        if alias_pairs:
            references = alias_pairs
        else:
            referenced_tables = list(dict.fromkeys(alias_map.values()))
            if referenced_tables:
                references = [(table, table) for table in referenced_tables]
            else:
                references = [(table, table) for table in self.schema_manager.get_tables()]

        lowered = token_prefix.lower()
        for reference, table in references:
            if not self.schema_manager.has_table(table):
                continue
            for column in self.schema_manager.get_columns(table):
                name = str(column["name"])
                text = f"{reference}.{name}"
                if lowered and not (
                    text.lower().startswith(lowered) or name.lower().startswith(lowered)
                ):
                    continue
                suggestions.append(
                    RankedSuggestion(
                        text=text,
                        source="rule",
                        confidence=self._base_column_confidence(
                            column_name=name,
                            full_reference=text,
                            sql=sql,
                            context=context,
                        ),
                        reason_code=f"{context.clause or context.context_type}_column",
                        reason="Column matches the current query scope and clause.",
                    )
                )

        if context.clause in {"group_by", "order_by"}:
            for item in suggestions:
                item.reason_code = context.clause
                item.reason = "Column is suitable for aggregation or sorting clauses."
                item.confidence = max(item.confidence, 0.76)

        return suggestions

    def _suggest_join_paths(
        self,
        sql: str,
        alias_map: Dict[str, str],
        context: QueryContext,
    ) -> List[RankedSuggestion]:
        items: List[RankedSuggestion] = []
        known_tables = {table for table in alias_map.values() if self.schema_manager.has_table(table)}
        if not known_tables:
            return items

        joined_text = sql.lower()
        for source_table in sorted(known_tables):
            for target_table in self.schema_manager.get_tables():
                if target_table in known_tables:
                    continue
                candidate = self._build_join_suggestion(source_table, target_table)
                if candidate is None:
                    continue
                if context.token_prefix and not target_table.lower().startswith(context.token_prefix.lower()):
                    continue
                if target_table.lower() in joined_text:
                    continue
                items.append(candidate)

        return items

    def _build_join_suggestion(self, source_table: str, target_table: str) -> RankedSuggestion | None:
        source_columns = {str(column["name"]).lower() for column in self.schema_manager.get_columns(source_table)}
        target_columns = {str(column["name"]).lower() for column in self.schema_manager.get_columns(target_table)}

        singular_source = source_table[:-1] if source_table.endswith("s") else source_table
        singular_target = target_table[:-1] if target_table.endswith("s") else target_table

        if f"{singular_target}_id" in source_columns and "id" in target_columns:
            text = f"JOIN {target_table} ON {source_table}.{singular_target}_id = {target_table}.id"
            reason = f"Inferred join via {source_table}.{singular_target}_id -> {target_table}.id."
        elif f"{singular_source}_id" in target_columns and "id" in source_columns:
            text = f"JOIN {target_table} ON {target_table}.{singular_source}_id = {source_table}.id"
            reason = f"Inferred join via {target_table}.{singular_source}_id -> {source_table}.id."
        else:
            return None

        return RankedSuggestion(
            text=text,
            source="join_infer",
            confidence=0.86,
            reason_code="join_path",
            reason=reason,
        )

    def _suggest_keywords(self, prefix: str, context: QueryContext) -> List[RankedSuggestion]:
        candidates = SQL_KEYWORDS[:]
        if prefix:
            lowered = prefix.lower()
            candidates = [keyword for keyword in candidates if keyword.lower().startswith(lowered)]

        clause_reason_code = {
            "select": "keyword_select",
            "from": "keyword_from",
            "join": "keyword_join",
            "where": "keyword_where",
            "having": "keyword_having",
            "group_by": "keyword_group_by",
            "order_by": "keyword_order_by",
        }

        return [
            RankedSuggestion(
                text=keyword,
                source="rule",
                confidence=0.42 if prefix else 0.3,
                reason_code=clause_reason_code.get(context.clause, "keyword"),
                reason="SQL keyword matched against the current editing clause.",
            )
            for keyword in candidates
        ]

    def _rank_suggestions(
        self, suggestions: List[RankedSuggestion], context: QueryContext
    ) -> List[RankedSuggestion]:
        deduped: Dict[str, RankedSuggestion] = {}
        for item in suggestions:
            key = item.text.strip().lower()
            if not key:
                continue
            existing = deduped.get(key)
            if existing is None or item.confidence > existing.confidence:
                deduped[key] = item

        ranked = sorted(
            deduped.values(),
            key=lambda item: (
                1 if item.source == "join_infer" else 0,
                1 if item.source == "recovery" else 0,
                item.confidence,
                self._context_priority(item, context),
                -len(item.text),
            ),
            reverse=True,
        )
        return ranked

    def _context_priority(self, item: RankedSuggestion, context: QueryContext) -> int:
        if context.clause == "where" and "." in item.text:
            return 3
        if context.clause in {"group_by", "order_by"} and "." in item.text:
            return 2
        if context.clause == "join" and item.source == "join_infer":
            return 4
        return 1

    def _base_column_confidence(
        self,
        column_name: str,
        full_reference: str,
        sql: str,
        context: QueryContext,
    ) -> float:
        score = 0.62
        lowered = column_name.lower()
        lowered_sql = sql.lower()
        if lowered == "id":
            score += 0.03
        if lowered.endswith("_id") and context.clause in {"join", "on", "where"}:
            score += 0.06
        if lowered.endswith("_at") and context.clause in {"where", "order_by"}:
            score += 0.09
        if lowered.endswith("_date") and context.clause in {"where", "order_by"}:
            score += 0.11
        if lowered in {"status", "created_at", "updated_at"} and context.clause in {"where", "order_by"}:
            score += 0.08
        if context.clause == "group_by" and (lowered.endswith("_id") or full_reference.lower() in lowered_sql):
            score += 0.14
        if context.clause == "order_by" and full_reference.lower() in lowered_sql:
            score += 0.06
        if context.clause in {"group_by", "order_by"}:
            score += 0.05
        return min(score, 0.95)
