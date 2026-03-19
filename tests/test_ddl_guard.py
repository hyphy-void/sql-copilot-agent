from backend.ddl_guard import validate_ddl_statement


def test_allow_create_table_sqlite():
    result = validate_ddl_statement(
        statement="CREATE TABLE IF NOT EXISTS demo_users (id INTEGER PRIMARY KEY)",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={},
    )
    assert result["allowed"] is True
    assert result["operation_type"] == "create_table"
    assert result["risk_level"] == "safe"
    assert result["preflight_checks"]


def test_block_drop_statement():
    result = validate_ddl_statement(
        statement="DROP TABLE users",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={},
    )
    assert result["allowed"] is False
    assert result["risk_level"] == "blocked"


def test_block_alter_rename():
    result = validate_ddl_statement(
        statement="ALTER TABLE users RENAME TO users_archive",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={"users": ["id"]},
    )
    assert result["allowed"] is False
    assert result["operation_type"] == "blocked_keyword"


def test_block_schema_qualified_table_on_sqlite():
    result = validate_ddl_statement(
        statement="CREATE TABLE crm.users (id INTEGER PRIMARY KEY)",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={},
    )
    assert result["allowed"] is False
    assert result["operation_type"] == "qualified_table_name"


def test_warn_for_existing_table_create():
    result = validate_ddl_statement(
        statement="CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={"users": ["id", "email"]},
    )
    assert result["allowed"] is True
    assert result["risk_level"] == "warning"


def test_warn_for_duplicate_add_column():
    result = validate_ddl_statement(
        statement="ALTER TABLE users ADD COLUMN email TEXT",
        dialect="sqlite",
        supports_create_database=False,
        schema_snapshot={"users": ["id", "email"]},
    )
    assert result["allowed"] is True
    assert result["risk_level"] == "warning"
    assert any(check["name"] == "duplicate_column_check" for check in result["preflight_checks"])
