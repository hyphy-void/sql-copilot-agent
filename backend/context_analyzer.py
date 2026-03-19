from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, Optional

import sqlglot
from sqlglot import exp


_FROM_TAIL_PATTERN = re.compile(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)?$", re.IGNORECASE)
_JOIN_TAIL_PATTERN = re.compile(r"\bjoin\s+([A-Za-z_][A-Za-z0-9_]*)?$", re.IGNORECASE)
_QUALIFIED_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)?$")
_WORD_SUFFIX_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)$")
_CTE_PREFIX_PATTERN = re.compile(r"^\s*with\b", re.IGNORECASE)
_SUBQUERY_TAIL_PATTERN = re.compile(r"\(\s*select\b", re.IGNORECASE)


@dataclass
class QueryContext:
    context_type: str
    token_prefix: str = ""
    qualifier: Optional[str] = None
    member_prefix: str = ""
    clause: str = "unknown"
    in_cte: bool = False
    in_subquery: bool = False
    parse_confidence: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _extract_token_prefix(text: str) -> str:
    match = _WORD_SUFFIX_PATTERN.search(text)
    return match.group(1) if match else ""


def _infer_last_clause(prefix_sql: str) -> str:
    lowered = prefix_sql.lower()
    patterns = {
        "select": r"\bselect\b",
        "from": r"\bfrom\b",
        "join": r"\bjoin\b",
        "on": r"\bon\b",
        "where": r"\bwhere\b",
        "group_by": r"\bgroup\s+by\b",
        "order_by": r"\border\s+by\b",
        "having": r"\bhaving\b",
        "with": r"\bwith\b",
    }

    best_clause = "unknown"
    best_pos = -1
    for clause, pattern in patterns.items():
        matches = list(re.finditer(pattern, lowered))
        if not matches:
            continue
        pos = matches[-1].start()
        if pos > best_pos:
            best_clause = clause
            best_pos = pos

    return best_clause


def _is_inside_subquery(prefix_sql: str) -> bool:
    opens = prefix_sql.count("(")
    closes = prefix_sql.count(")")
    return opens > closes and bool(_SUBQUERY_TAIL_PATTERN.search(prefix_sql))


def _detect_clause_via_ast(sql: str, cursor: int) -> tuple[str, float]:
    trimmed = sql[:cursor]
    if not trimmed.strip():
        return "unknown", 0.0

    try:
        tree = sqlglot.parse_one(trimmed)
    except Exception:
        return _infer_last_clause(trimmed), 0.35

    trailing = trimmed.rstrip()
    if not trailing:
        return "unknown", 0.35

    lowered_trailing = trailing.lower()
    if lowered_trailing.endswith("group by") or re.search(
        r"\bgroup\s+by(?:\s+[A-Za-z0-9_,.\s]*)?$", trailing, re.IGNORECASE
    ):
        return "group_by", 0.9
    if lowered_trailing.endswith("order by") or re.search(
        r"\border\s+by(?:\s+[A-Za-z0-9_,.\s]*)?$", trailing, re.IGNORECASE
    ):
        return "order_by", 0.9
    if lowered_trailing.endswith("having") or re.search(
        r"\bhaving(?:\s+[A-Za-z0-9_.\s]*)?$", trailing, re.IGNORECASE
    ):
        return "having", 0.9
    if lowered_trailing.endswith("on") or re.search(
        r"\bon(?:\s+[A-Za-z0-9_.\s=<>!]*)?$", trailing, re.IGNORECASE
    ):
        return "on", 0.9
    if lowered_trailing.endswith("where") or re.search(
        r"\bwhere(?:\s+[A-Za-z0-9_.\s=<>!]*)?$", trailing, re.IGNORECASE
    ):
        return "where", 0.9
    if lowered_trailing.endswith("join") or re.search(
        r"\bjoin(?:\s+[A-Za-z0-9_.\s]*)?$", trailing, re.IGNORECASE
    ):
        return "join", 0.9
    if lowered_trailing.endswith("from") or re.search(
        r"\bfrom(?:\s+[A-Za-z0-9_.\s]*)?$", trailing, re.IGNORECASE
    ):
        return "from", 0.9

    if isinstance(tree, exp.Select):
        return "select", 0.75

    return _infer_last_clause(trimmed), 0.6


def detect_context(sql: str, cursor: int) -> QueryContext:
    clamped_cursor = max(0, min(cursor, len(sql)))
    prefix = sql[:clamped_cursor]
    ends_with_whitespace = bool(prefix) and prefix[-1].isspace()
    stripped = prefix.rstrip()

    qualifier = None
    member_prefix = ""
    qualified_match = _QUALIFIED_PATTERN.search(stripped)
    if qualified_match:
        qualifier = qualified_match.group(1)
        member_prefix = qualified_match.group(2) or ""

    token_prefix = ""
    if not ends_with_whitespace:
        token_prefix = member_prefix if qualifier else _extract_token_prefix(stripped)

    if _JOIN_TAIL_PATTERN.search(stripped):
        return QueryContext(
            context_type="from",
            token_prefix=token_prefix,
            qualifier=qualifier,
            member_prefix=member_prefix,
            clause="join",
            in_cte=bool(_CTE_PREFIX_PATTERN.search(prefix)),
            in_subquery=_is_inside_subquery(prefix),
            parse_confidence=0.85,
        )

    if _FROM_TAIL_PATTERN.search(stripped):
        return QueryContext(
            context_type="from",
            token_prefix=token_prefix,
            qualifier=qualifier,
            member_prefix=member_prefix,
            clause="from",
            in_cte=bool(_CTE_PREFIX_PATTERN.search(prefix)),
            in_subquery=_is_inside_subquery(prefix),
            parse_confidence=0.8,
        )

    clause, confidence = _detect_clause_via_ast(sql, clamped_cursor)

    if clause in {"where", "on", "having"}:
        context_type = "where"
    elif clause in {"from", "join"}:
        context_type = "from"
    elif clause in {"group_by", "order_by"}:
        context_type = "select"
    elif clause in {"select", "with"}:
        context_type = "select"
    elif qualifier:
        context_type = "select"
    else:
        context_type = "unknown"

    return QueryContext(
        context_type=context_type,
        token_prefix=token_prefix,
        qualifier=qualifier,
        member_prefix=member_prefix,
        clause=clause,
        in_cte=bool(_CTE_PREFIX_PATTERN.search(prefix)),
        in_subquery=_is_inside_subquery(prefix),
        parse_confidence=confidence,
    )
