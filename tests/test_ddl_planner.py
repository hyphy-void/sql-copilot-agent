from backend.ddl_planner import DDLPlanner
from backend.llm import BaseLLMProvider


class StubDDLProvider(BaseLLMProvider):
    def __init__(self, candidates):
        self._candidates = candidates

    def generate_completion(self, sql_prefix, schema_snapshot, context):
        return []

    def generate_ddl_candidates(self, intent, schema_snapshot, dialect):
        return list(self._candidates)


def test_template_extracts_requested_columns_without_llm():
    planner = DDLPlanner()

    result = planner.plan(
        prompt="创建 demo_contacts 表，字段有 id、name、email",
        backend="sqlite",
        dialect="sqlite",
        schema_snapshot={},
        use_llm=False,
    )

    assert result["source"] == "template"
    assert result["statements"] == [
        "CREATE TABLE IF NOT EXISTS demo_contacts (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
    ]


def test_sqlite_llm_candidates_are_normalized_to_local_tables():
    planner = DDLPlanner(
        llm_provider=StubDDLProvider(
            [
                "CREATE DATABASE crm",
                "CREATE TABLE crm.crm_users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)",
            ]
        )
    )

    result = planner.plan(
        prompt="创建 crm 库并建 crm_users 表，字段有 id、name、email",
        backend="sqlite",
        dialect="sqlite",
        schema_snapshot={},
        use_llm=True,
    )

    assert result["source"] == "llm_template"
    assert result["statements"] == [
        "CREATE TABLE IF NOT EXISTS crm_users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
    ]
    assert "SQLite backend ignores CREATE DATABASE and will use current DB file." in result["notes"]
    assert (
        "SQLite backend uses the current DB file, so schema-qualified table names were rewritten to local tables."
        in result["notes"]
    )
