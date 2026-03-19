from backend.ddl_guard import validate_ddl_statement


def test_ddl_risk_classification_metrics():
    cases = [
        {
            "statement": "CREATE TABLE IF NOT EXISTS demo_users (id INTEGER PRIMARY KEY)",
            "expected": "safe",
            "schema_snapshot": {},
        },
        {
            "statement": "ALTER TABLE users ADD COLUMN email TEXT",
            "expected": "warning",
            "schema_snapshot": {"users": ["id", "email"]},
        },
        {
            "statement": "DROP TABLE users",
            "expected": "blocked",
            "schema_snapshot": {"users": ["id"]},
        },
    ]

    correct = 0
    blocked_misses = 0
    safe_false_blocks = 0

    for case in cases:
        result = validate_ddl_statement(
            statement=case["statement"],
            dialect="sqlite",
            supports_create_database=False,
            schema_snapshot=case["schema_snapshot"],
        )
        if result["risk_level"] == case["expected"]:
            correct += 1
        if case["expected"] == "blocked" and result["risk_level"] != "blocked":
            blocked_misses += 1
        if case["expected"] == "safe" and result["allowed"] is False:
            safe_false_blocks += 1

    assert correct == len(cases)
    assert blocked_misses == 0
    assert safe_false_blocks == 0
