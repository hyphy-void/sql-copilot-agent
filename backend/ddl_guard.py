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
    schema_snapshot: Dict[str, List[str]] | None = None,
) -> Dict[str, object]:
    operations: List[Dict[str, object]] = []
    highest_risk = "safe"
    for statement in statements:
        operation = validate_ddl_statement(
            statement=statement,
            dialect=dialect,
            supports_create_database=supports_create_database,
            schema_snapshot=schema_snapshot,
        )
        operations.append(operation)
        highest_risk = _max_risk(highest_risk, str(operation["risk_level"]))

    has_blocking_risk = any(not bool(item["allowed"]) for item in operations)
    return {
        "operations": operations,
        "has_blocking_risk": has_blocking_risk,
        "risk_summary": highest_risk,
        "risk_level": highest_risk,
        "impact_summary": _summarize_impact(operations),
        "preflight_checks": _collect_preflight_checks(operations),
    }


def validate_ddl_statement(
    statement: str,
    dialect: str,
    supports_create_database: bool,
    schema_snapshot: Dict[str, List[str]] | None = None,
) -> Dict[str, object]:
    normalized_sql = (statement or "").strip()
    schema_snapshot = schema_snapshot or {}
    if not normalized_sql:
        return _operation(
            statement=statement,
            operation_type="empty_statement",
            allowed=False,
            risk_level="blocked",
            reason="Empty SQL statement.",
        )

    guarded_sql = f" {normalized_sql.upper()} "
    if any(token in guarded_sql for token in BLOCKED_TOKENS):
        return _operation(
            statement=normalized_sql,
            operation_type="blocked_keyword",
            allowed=False,
            risk_level="blocked",
            reason="DROP/TRUNCATE/RENAME are blocked in safe DDL mode.",
            impact_summary="Destructive statement blocked before execution.",
            rollback_strategy="Not applicable because execution is blocked.",
        )

    try:
        expression = sqlglot.parse_one(normalized_sql, read=dialect)
    except Exception as exc:
        return _operation(
            statement=normalized_sql,
            operation_type="parse_error",
            allowed=False,
            risk_level="blocked",
            reason=f"SQL parse failed: {exc}",
            impact_summary="Statement could not be normalized safely.",
            rollback_strategy="Fix SQL before retrying.",
        )

    if not supports_create_database and _has_qualified_table_reference(expression):
        return _operation(
            statement=normalized_sql,
            operation_type="qualified_table_name",
            allowed=False,
            risk_level="blocked",
            reason="Current backend does not support schema-qualified table names.",
            impact_summary="Cross-database write blocked on current backend.",
            rollback_strategy="Remove schema qualifier and retry.",
        )

    if isinstance(expression, exp.Create):
        return _validate_create(
            statement=normalized_sql,
            expression=expression,
            supports_create_database=supports_create_database,
            schema_snapshot=schema_snapshot,
        )

    if isinstance(expression, exp.Alter):
        return _validate_alter(
            statement=normalized_sql,
            expression=expression,
            schema_snapshot=schema_snapshot,
        )

    return _operation(
        statement=normalized_sql,
        operation_type=type(expression).__name__.lower(),
        allowed=False,
        risk_level="blocked",
        reason="Only CREATE and ALTER TABLE ADD COLUMN are allowed.",
        impact_summary="Unsupported DDL category blocked by policy.",
        rollback_strategy="Rewrite request into supported safe DDL operations.",
    )


def _validate_create(
    statement: str,
    expression: exp.Create,
    supports_create_database: bool,
    schema_snapshot: Dict[str, List[str]],
) -> Dict[str, object]:
    kind = str(expression.args.get("kind") or "").upper()
    table = expression.find(exp.Table)
    object_name = table.name if table is not None else None

    if kind in {"DATABASE", "SCHEMA"}:
        if not supports_create_database:
            return _operation(
                statement=statement,
                operation_type="create_database",
                allowed=False,
                risk_level="blocked",
                reason="Current backend does not support CREATE DATABASE.",
                impact_summary="Database creation blocked on current backend.",
                rollback_strategy="Use an existing database connection instead.",
            )
        return _operation(
            statement=statement,
            operation_type="create_database",
            allowed=True,
            risk_level="warning",
            reason="Database creation is allowed but should be reviewed carefully.",
            impact_summary="Creates a new logical database/schema namespace.",
            preflight_checks=[
                {
                    "name": "database_exists_check",
                    "status": "review",
                    "detail": "Confirm database naming and privileges before execution.",
                }
            ],
            idempotency="Mostly idempotent when IF NOT EXISTS is present.",
            rollback_strategy="Manual DROP DATABASE if this creation was unintended.",
        )

    if kind == "TABLE":
        exists = bool(object_name and object_name in schema_snapshot)
        preflight_checks = [
            {
                "name": "table_exists_check",
                "status": "pass" if not exists else "warning",
                "detail": (
                    f"Table '{object_name}' already exists; statement should remain idempotent."
                    if exists
                    else f"Table '{object_name}' does not exist yet."
                ),
            }
        ]
        return _operation(
            statement=statement,
            operation_type="create_table",
            allowed=True,
            risk_level="warning" if exists else "safe",
            reason="Allowed by safe DDL policy.",
            impact_summary=(
                f"Adds a new table '{object_name}' to the schema."
                if not exists
                else f"Targets existing table '{object_name}' and relies on idempotent create semantics."
            ),
            preflight_checks=preflight_checks,
            idempotency="Idempotent when IF NOT EXISTS is present.",
            rollback_strategy="Manual DROP TABLE if rollback is required after creation.",
        )

    if kind == "INDEX":
        return _operation(
            statement=statement,
            operation_type="create_index",
            allowed=True,
            risk_level="warning",
            reason="Index creation is allowed but may affect write performance during build.",
            impact_summary="Adds an index and may increase lock time or storage usage.",
            preflight_checks=[
                {
                    "name": "index_build_review",
                    "status": "review",
                    "detail": "Review index naming and expected write amplification.",
                }
            ],
            idempotency="Idempotent when IF NOT EXISTS is present.",
            rollback_strategy="Manual DROP INDEX if rollback is needed.",
        )

    return _operation(
        statement=statement,
        operation_type="create_other",
        allowed=False,
        risk_level="blocked",
        reason=f"CREATE {kind or 'UNKNOWN'} is not in the safe DDL allowlist.",
        impact_summary="Unsupported create operation blocked by policy.",
        rollback_strategy="Rewrite the request using CREATE TABLE/INDEX only.",
    )


def _validate_alter(
    statement: str,
    expression: exp.Alter,
    schema_snapshot: Dict[str, List[str]],
) -> Dict[str, object]:
    upper_sql = statement.upper()
    if not re.search(r"\bALTER\s+TABLE\b", upper_sql):
        return _operation(
            statement=statement,
            operation_type="alter_non_table",
            allowed=False,
            risk_level="blocked",
            reason="Only ALTER TABLE ADD COLUMN is allowed.",
            impact_summary="Non-table ALTER statement blocked.",
            rollback_strategy="Rewrite request into ALTER TABLE ADD COLUMN.",
        )

    if not re.search(r"\bADD(\s+COLUMN)?\b", upper_sql):
        return _operation(
            statement=statement,
            operation_type="alter_table_non_add_column",
            allowed=False,
            risk_level="blocked",
            reason="Only ALTER TABLE ADD COLUMN is allowed.",
            impact_summary="Unsafe ALTER TABLE operation blocked.",
            rollback_strategy="Rewrite request into ADD COLUMN only.",
        )

    actions = expression.args.get("actions") or []
    if not actions or not all(isinstance(action, exp.ColumnDef) for action in actions):
        return _operation(
            statement=statement,
            operation_type="alter_table_non_add_column",
            allowed=False,
            risk_level="blocked",
            reason="ALTER TABLE actions are restricted to ADD COLUMN definitions.",
            impact_summary="Unsafe column alteration blocked.",
            rollback_strategy="Use additive-only schema changes.",
        )

    table = expression.find(exp.Table)
    table_name = table.name if table is not None else ""
    existing_columns = {column.lower() for column in schema_snapshot.get(table_name, [])}
    added_columns = [str(action.this.name) for action in actions if action.this is not None]
    duplicates = [column for column in added_columns if column.lower() in existing_columns]
    preflight_checks = [
        {
            "name": "table_exists_check",
            "status": "pass" if table_name in schema_snapshot else "warning",
            "detail": (
                f"Table '{table_name}' exists and can be altered."
                if table_name in schema_snapshot
                else f"Table '{table_name}' was not found in current schema snapshot."
            ),
        }
    ]
    if duplicates:
        preflight_checks.append(
            {
                "name": "duplicate_column_check",
                "status": "warning",
                "detail": f"Columns already present: {', '.join(sorted(duplicates))}.",
            }
        )

    return _operation(
        statement=statement,
        operation_type="alter_table_add_column",
        allowed=True,
        risk_level="warning",
        reason="Additive ALTER TABLE is allowed but should be reviewed for compatibility.",
        impact_summary=(
            f"Adds columns {', '.join(added_columns)} to '{table_name}'. Existing rows may require backfill."
        ),
        preflight_checks=preflight_checks,
        idempotency="Not fully idempotent when target columns already exist.",
        rollback_strategy="Manual table rebuild or column ignore strategy depending on backend.",
    )


def _operation(
    statement: str,
    operation_type: str,
    allowed: bool,
    risk_level: str,
    reason: str,
    impact_summary: str | None = None,
    preflight_checks: List[Dict[str, object]] | None = None,
    idempotency: str | None = None,
    rollback_strategy: str | None = None,
) -> Dict[str, object]:
    return {
        "statement": statement,
        "operation_type": operation_type,
        "allowed": allowed,
        "risk_level": risk_level,
        "reason": reason,
        "impact_summary": impact_summary or "",
        "preflight_checks": preflight_checks or [],
        "idempotency": idempotency or "",
        "rollback_strategy": rollback_strategy or "",
    }


def _has_qualified_table_reference(expression: exp.Expression) -> bool:
    table = expression.find(exp.Table)
    if table is None:
        return False
    return table.args.get("db") is not None or table.args.get("catalog") is not None


def _max_risk(left: str, right: str) -> str:
    order = {"safe": 0, "warning": 1, "blocked": 2}
    return left if order[left] >= order[right] else right


def _collect_preflight_checks(operations: List[Dict[str, object]]) -> List[Dict[str, object]]:
    checks: List[Dict[str, object]] = []
    for operation in operations:
        for check in operation.get("preflight_checks", []):
            checks.append(dict(check))
    return checks


def _summarize_impact(operations: List[Dict[str, object]]) -> str:
    if not operations:
        return "No DDL operations were generated."
    parts = [str(operation.get("impact_summary") or "").strip() for operation in operations]
    parts = [part for part in parts if part]
    return " ".join(parts)
