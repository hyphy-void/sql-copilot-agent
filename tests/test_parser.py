from backend.parser import extract_alias_map, parse_sql


def test_parse_valid_sql():
    ast = parse_sql("SELECT u.id FROM users u")
    assert ast is not None


def test_parse_invalid_sql_returns_none():
    ast = parse_sql("SELECT * FROM")
    assert ast is None


def test_extract_alias_map_from_join():
    sql = "SELECT u.id, o.price FROM users u JOIN orders o ON u.id = o.user_id"
    alias_map = extract_alias_map(sql)

    assert alias_map["u"] == "users"
    assert alias_map["o"] == "orders"


def test_extract_alias_map_from_partial_sql():
    sql = "SELECT u. FROM users u"
    alias_map = extract_alias_map(sql)

    assert alias_map["u"] == "users"
