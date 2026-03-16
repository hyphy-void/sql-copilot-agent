from __future__ import annotations

import re
from typing import Dict, List

import sqlglot
from sqlglot import exp


BLOCKED_TOKENS = (
    " DROP ",
    " TRUNCATE ",
    " RENAME ",
)


def validate_ddl_statements(
    statements: List[str],
    dialect: str,
    supports_create_database: bool,
) -> Dict[str, object]:
    operations: List[Dict[str, object]] = []

    for statement in statements:
        operations.append(
            validate_ddl_statement(
                statement=statement,
                dialect=dialect,
                supports_create_database=supports_create_database,
            )
        )

    has_blocking_risk = any(not bool(item["allowed"]) for item in operations)
    risk_summary = "blocked" if has_blocking_risk else "safe"
    return {
        "operations": operations,
        "has_blocking_risk": has_blocking_risk,
        "risk_summary": risk_summary,
    }


def validate_ddl_statement(
    statement: str,
    dialect: str,
    supports_create_database: bool,
) -> Dict[str, object]:
    normalized_sql = (statement or "").strip()
    if not normalized_sql:
        return _blocked_operation(
            statement=statement,
            operation_type="empty_statement",
            reason="Empty SQL statement.",
        )

    guarded_sql = f" {normalized_sql.upper()} "
    if any(token in guarded_sql for token in BLOCKED_TOKENS):
        return _blocked_operation(
            statement=normalized_sql,
            operation_type="blocked_keyword",
            reason="DROP/TRUNCATE/RENAME are blocked in v1 safe DDL mode.",
        )

    try:
        expression = sqlglot.parse_one(normalized_sql, read=dialect)
    except Exception as exc:
        return _blocked_operation(
            statement=normalized_sql,
            operation_type="parse_error",
            reason=f"SQL parse failed: {exc}",
        )

    if isinstance(expression, exp.Create):
        return _validate_create(
            statement=normalized_sql,
            expression=expression,
            supports_create_database=supports_create_database,
        )

    if isinstance(expression, exp.Alter):
        return _validate_alter(statement=normalized_sql, expression=expression)

    return _blocked_operation(
        statement=normalized_sql,
        operation_type=type(expression).__name__.lower(),
        reason="Only CREATE and ALTER TABLE ADD COLUMN are allowed.",
    )


def _validate_create(
    statement: str,
    expression: exp.Create,
    supports_create_database: bool,
) -> Dict[str, object]:
    kind = str(expression.args.get("kind") or "").upper()

    if kind in {"DATABASE", "SCHEMA"}:
        if not supports_create_database:
            return _blocked_operation(
                statement=statement,
                operation_type="create_database",
                reason="Current backend does not support CREATE DATABASE.",
            )
        return _allowed_operation(statement, "create_database")

    if kind == "TABLE":
        return _allowed_operation(statement, "create_table")

    if kind == "INDEX":
        return _allowed_operation(statement, "create_index")

    return _blocked_operation(
        statement=statement,
        operation_type="create_other",
        reason=f"CREATE {kind or 'UNKNOWN'} is not in the v1 safe DDL allowlist.",
    )


def _validate_alter(statement: str, expression: exp.Alter) -> Dict[str, object]:
    upper_sql = statement.upper()
    if not re.search(r"\bALTER\s+TABLE\b", upper_sql):
        return _blocked_operation(
            statement=statement,
            operation_type="alter_non_table",
            reason="Only ALTER TABLE ADD COLUMN is allowed.",
        )

    if not re.search(r"\bADD(\s+COLUMN)?\b", upper_sql):
        return _blocked_operation(
            statement=statement,
            operation_type="alter_table_non_add_column",
            reason="Only ALTER TABLE ADD COLUMN is allowed.",
        )

    actions = expression.args.get("actions") or []
    if not actions or not all(isinstance(action, exp.ColumnDef) for action in actions):
        return _blocked_operation(
            statement=statement,
            operation_type="alter_table_non_add_column",
            reason="ALTER TABLE actions are restricted to ADD COLUMN definitions.",
        )

    return _allowed_operation(statement, "alter_table_add_column")


def _allowed_operation(statement: str, operation_type: str) -> Dict[str, object]:
    return {
        "statement": statement,
        "operation_type": operation_type,
        "allowed": True,
        "risk_level": "safe",
        "reason": "Allowed by safe DDL policy.",
    }


def _blocked_operation(statement: str, operation_type: str, reason: str) -> Dict[str, object]:
    return {
        "statement": statement,
        "operation_type": operation_type,
        "allowed": False,
        "risk_level": "blocked",
        "reason": reason,
    }
