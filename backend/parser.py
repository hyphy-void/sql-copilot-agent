from __future__ import annotations

import re
from typing import Dict, Optional

import sqlglot
from sqlglot import exp


_ALIAS_PATTERN = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)

_SQL_KEYWORDS = {
    "select",
    "from",
    "where",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "on",
    "group",
    "order",
    "having",
    "limit",
    "union",
    "as",
    "and",
    "or",
    "by",
}


def parse_sql(sql: str) -> Optional[exp.Expression]:
    """Parse SQL into AST. Return None on parse failure."""
    try:
        return sqlglot.parse_one(sql)
    except Exception:
        return None


def extract_alias_map(sql: str) -> Dict[str, str]:
    """Extract alias -> table mapping, with best-effort fallback for incomplete SQL."""
    alias_map: Dict[str, str] = {}

    ast = parse_sql(sql)
    if ast is not None:
        for table in ast.find_all(exp.Table):
            table_name = table.name
            if not table_name:
                continue
            alias_map.setdefault(table_name, table_name)

            alias_name = table.alias_or_name
            if alias_name:
                alias_map.setdefault(alias_name, table_name)

    # Fallback regex parser for partial SQL where AST might fail.
    for match in _ALIAS_PATTERN.finditer(sql):
        table_name = match.group(1)
        alias_name = match.group(2)

        if _is_sql_keyword(table_name):
            continue

        alias_map.setdefault(table_name, table_name)
        if alias_name and not _is_sql_keyword(alias_name):
            alias_map[alias_name] = table_name

    return alias_map


def resolve_table(reference: str, alias_map: Dict[str, str]) -> Optional[str]:
    """Resolve alias/table reference to physical table name."""
    if not reference:
        return None
    return alias_map.get(reference)


def _is_sql_keyword(token: str) -> bool:
    return token.lower() in _SQL_KEYWORDS
