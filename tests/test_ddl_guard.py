from backend.ddl_guard import validate_ddl_statement


def test_allow_create_table_sqlite():
    result = validate_ddl_statement(
        statement="CREATE TABLE IF NOT EXISTS demo_users (id INTEGER PRIMARY KEY)",
        dialect="sqlite",
        supports_create_database=False,
    )
    assert result["allowed"] is True
    assert result["operation_type"] == "create_table"


def test_block_drop_statement():
    result = validate_ddl_statement(
        statement="DROP TABLE users",
        dialect="sqlite",
        supports_create_database=False,
    )
    assert result["allowed"] is False
    assert result["risk_level"] == "blocked"


def test_block_alter_rename():
    result = validate_ddl_statement(
        statement="ALTER TABLE users RENAME TO users_archive",
        dialect="sqlite",
        supports_create_database=False,
    )
    assert result["allowed"] is False
    assert result["operation_type"] == "blocked_keyword"


def test_block_schema_qualified_table_on_sqlite():
    result = validate_ddl_statement(
        statement="CREATE TABLE crm.users (id INTEGER PRIMARY KEY)",
        dialect="sqlite",
        supports_create_database=False,
    )
    assert result["allowed"] is False
    assert result["operation_type"] == "qualified_table_name"
