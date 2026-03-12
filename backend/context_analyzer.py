from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, Optional


_FROM_TAIL_PATTERN = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)?$", re.IGNORECASE
)
_QUALIFIED_PATTERN = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)?$"
)
_WORD_SUFFIX_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass
class QueryContext:
    context_type: str
    token_prefix: str = ""
    qualifier: Optional[str] = None
    member_prefix: str = ""
    clause: str = "unknown"

    def to_dict(self) -> Dict[str, str]:
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
        "where": r"\bwhere\b",
        "on": r"\bon\b",
        "having": r"\bhaving\b",
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


def detect_context(sql: str, cursor: int) -> QueryContext:
    """Best-effort context inference around cursor position."""
    clamped_cursor = max(0, min(cursor, len(sql)))
    prefix = sql[:clamped_cursor]
    stripped = prefix.rstrip()

    qualifier = None
    member_prefix = ""

    qualified_match = _QUALIFIED_PATTERN.search(stripped)
    if qualified_match:
        qualifier = qualified_match.group(1)
        member_prefix = qualified_match.group(2) or ""

    token_prefix = member_prefix if qualifier else _extract_token_prefix(stripped)

    if _FROM_TAIL_PATTERN.search(stripped):
        return QueryContext(
            context_type="from",
            token_prefix=token_prefix,
            qualifier=qualifier,
            member_prefix=member_prefix,
            clause="from",
        )

    clause = _infer_last_clause(prefix)
    if clause in {"where", "on", "having"}:
        context_type = "where"
    elif clause in {"from", "join"}:
        context_type = "from"
    elif clause == "select":
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
    )
