from __future__ import annotations

import re
from typing import Dict, List

import sqlglot
from sqlglot import exp

from backend.llm import BaseLLMProvider


DDL_HEAD_PATTERN = re.compile(r"^\s*(CREATE|ALTER|DROP|TRUNCATE|RENAME)\b", re.IGNORECASE)


class DDLPlanner:
    def __init__(self, llm_provider: BaseLLMProvider | None = None) -> None:
        self.llm_provider = llm_provider

    def plan(
        self,
        prompt: str,
        backend: str,
        dialect: str,
        schema_snapshot: Dict[str, List[str]],
        use_llm: bool = True,
    ) -> Dict[str, object]:
        cleaned_prompt = (prompt or "").strip()
        explicit_sql = _extract_explicit_sql(cleaned_prompt, dialect=dialect)
        notes: List[str] = []

        if explicit_sql:
            return {
                "statements": explicit_sql,
                "source": "explicit_sql",
                "notes": notes,
            }

        template_statements, template_notes = _build_template_statements(
            prompt=cleaned_prompt,
            backend=backend,
            dialect=dialect,
        )
        notes.extend(template_notes)

        llm_statements: List[str] = []
        if use_llm and self.llm_provider is not None:
            try:
                candidates = self.llm_provider.generate_ddl_candidates(
                    intent=cleaned_prompt,
                    schema_snapshot=schema_snapshot,
                    dialect=dialect,
                )
                llm_statements = _filter_ddl_candidates(
                    candidates,
                    backend=backend,
                    dialect=dialect,
                    notes=notes,
                )
            except Exception:
                llm_statements = []

        merged = _merge_statements(llm_statements, template_statements)
        source = "template"
        if llm_statements:
            source = "llm_template"

        if not merged:
            merged = _default_statement(dialect=dialect)
            notes.append("No concrete object name detected; using fallback table name.")

        return {
            "statements": merged[:20],
            "source": source,
            "notes": notes,
        }


def _extract_explicit_sql(text: str, dialect: str) -> List[str]:
    if not text:
        return []

    if not re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|RENAME)\b", text, re.IGNORECASE):
        return []

    try:
        parsed = sqlglot.parse(text, read=dialect)
        statements = [expr.sql(dialect=dialect).strip() for expr in parsed if expr]
        statements = [stmt for stmt in statements if stmt]
        if statements:
            return statements
    except Exception:
        pass

    chunks = [item.strip() for item in re.split(r";\s*", text) if item.strip()]
    return [chunk for chunk in chunks if DDL_HEAD_PATTERN.match(chunk)]


def _build_template_statements(prompt: str, backend: str, dialect: str) -> tuple[List[str], List[str]]:
    notes: List[str] = []
    statements: List[str] = []
    lowered = prompt.lower()

    database_name = _extract_database_name(prompt)
    table_names = _extract_table_names(prompt)
    add_column_requests = _extract_add_column_requests(prompt)
    requested_columns = _extract_column_names(prompt)

    if backend == "mysql" and database_name:
        statements.append(f"CREATE DATABASE IF NOT EXISTS `{database_name}`")
    elif backend == "sqlite" and database_name:
        _append_note_once(notes, "SQLite backend ignores CREATE DATABASE and will use current DB file.")

    if table_names:
        for table_name in table_names:
            qualified_table = table_name
            if backend == "mysql" and database_name:
                qualified_table = f"`{database_name}`.`{table_name}`"
            statements.append(
                _build_create_table_sql(
                    qualified_table,
                    dialect=dialect,
                    column_names=requested_columns,
                )
            )

    if add_column_requests:
        for table_name, column_name in add_column_requests:
            statements.append(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} "
                f"{'VARCHAR(255)' if backend == 'mysql' else 'TEXT'}"
            )

    if not statements and ("表" in prompt or "table" in lowered):
        fallback_table = table_names[0] if table_names else "new_table"
        statements.append(_build_create_table_sql(fallback_table, dialect=dialect))

    return statements, notes


def _extract_database_name(prompt: str) -> str | None:
    patterns = [
        re.compile(r"(?:创建|新建)\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:数据库|库)", re.IGNORECASE),
        re.compile(r"create\s+database\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(prompt)
        if match:
            return match.group(1)
    return None


def _extract_table_names(prompt: str) -> List[str]:
    names: List[str] = []
    patterns = [
        re.compile(r"(?:创建|新建|建)\s*([A-Za-z_][A-Za-z0-9_]*)\s*表", re.IGNORECASE),
        re.compile(r"create\s+table\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE),
    ]
    for pattern in patterns:
        for match in pattern.finditer(prompt):
            table_name = match.group(1)
            if table_name not in names:
                names.append(table_name)
    return names


def _extract_add_column_requests(prompt: str) -> List[tuple[str, str]]:
    requests: List[tuple[str, str]] = []
    patterns = [
        re.compile(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*表.*?(?:新增|添加)\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:字段|列)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"add\s+column\s+([A-Za-z_][A-Za-z0-9_]*)\s+to\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        for match in pattern.finditer(prompt):
            if "add column" in match.group(0).lower():
                column_name = match.group(1)
                table_name = match.group(2)
            else:
                table_name = match.group(1)
                column_name = match.group(2)
            item = (table_name, column_name)
            if item not in requests:
                requests.append(item)
    return requests


def _extract_column_names(prompt: str) -> List[str]:
    patterns = [
        re.compile(r"(?:字段|列)\s*(?:有|为|包括|包含|:|：)\s*([^。；;\n]+)", re.IGNORECASE),
        re.compile(
            r"(?:with\s+)?columns?\s*(?:are|is|as|include|includes|:)?\s*([^.;\n]+)",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.search(prompt)
        if not match:
            continue

        names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1))
        unique_names: List[str] = []
        seen = set()
        for name in names:
            lowered = name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique_names.append(name)
        if unique_names:
            return unique_names

    return []


def _build_create_table_sql(
    table_name: str,
    dialect: str,
    column_names: List[str] | None = None,
) -> str:
    columns = _build_column_definitions(column_names or [], dialect=dialect)
    return f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(columns)})"


def _build_column_definitions(column_names: List[str], dialect: str) -> List[str]:
    if not column_names:
        if dialect == "mysql":
            return [
                "id BIGINT PRIMARY KEY AUTO_INCREMENT",
                "name VARCHAR(255) NOT NULL",
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            ]
        return [
            "id INTEGER PRIMARY KEY",
            "name TEXT NOT NULL",
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP",
        ]

    return [_build_column_definition(name, dialect=dialect) for name in column_names]


def _build_column_definition(column_name: str, dialect: str) -> str:
    normalized = column_name.lower()

    if normalized == "id":
        return "id BIGINT PRIMARY KEY AUTO_INCREMENT" if dialect == "mysql" else "id INTEGER PRIMARY KEY"

    if normalized in {"created_at", "updated_at"}:
        if dialect == "mysql":
            return f"{column_name} TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        return f"{column_name} TEXT DEFAULT CURRENT_TIMESTAMP"

    base_type = "VARCHAR(255)" if dialect == "mysql" else "TEXT"
    return f"{column_name} {base_type}"


def _filter_ddl_candidates(
    candidates: List[str],
    backend: str,
    dialect: str,
    notes: List[str],
) -> List[str]:
    filtered: List[str] = []
    for candidate in candidates:
        sql = (candidate or "").strip().strip("`")
        if not sql:
            continue

        if not DDL_HEAD_PATTERN.match(sql):
            continue

        try:
            parsed = sqlglot.parse_one(sql, read=dialect)
            normalized = _normalize_generated_statement(
                parsed,
                backend=backend,
                dialect=dialect,
                notes=notes,
            )
        except Exception:
            continue

        if not normalized:
            continue

        if normalized not in filtered:
            filtered.append(normalized)
    return filtered


def _normalize_generated_statement(
    expression: exp.Expression,
    backend: str,
    dialect: str,
    notes: List[str],
) -> str | None:
    if isinstance(expression, exp.Create):
        kind = str(expression.args.get("kind") or "").upper()
        if backend == "sqlite" and kind in {"DATABASE", "SCHEMA"}:
            _append_note_once(notes, "SQLite backend ignores CREATE DATABASE and will use current DB file.")
            return None

        if kind in {"DATABASE", "SCHEMA", "TABLE", "INDEX"}:
            expression.set("exists", True)

    if backend == "sqlite":
        table = expression.find(exp.Table)
        if table is not None and (table.args.get("db") is not None or table.args.get("catalog") is not None):
            table.set("db", None)
            table.set("catalog", None)
            _append_note_once(
                notes,
                "SQLite backend uses the current DB file, so schema-qualified table names were rewritten to local tables.",
            )

    return expression.sql(dialect=dialect).strip()


def _merge_statements(primary: List[str], fallback: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for statement in [*primary, *fallback]:
        normalized = statement.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _default_statement(dialect: str) -> List[str]:
    return [_build_create_table_sql("new_table", dialect=dialect)]


def _append_note_once(notes: List[str], note: str) -> None:
    if note not in notes:
        notes.append(note)
