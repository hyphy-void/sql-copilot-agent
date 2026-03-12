from backend.context_analyzer import detect_context


def test_detect_select_context_for_qualified_column():
    sql = "SELECT u. FROM users u"
    context = detect_context(sql, len("SELECT u."))

    assert context.context_type == "select"
    assert context.qualifier == "u"
    assert context.member_prefix == ""


def test_detect_from_context():
    sql = "SELECT * FROM or"
    context = detect_context(sql, len(sql))

    assert context.context_type == "from"
    assert context.token_prefix == "or"


def test_detect_where_context():
    sql = "SELECT * FROM orders WHERE ord"
    context = detect_context(sql, len(sql))

    assert context.context_type == "where"
    assert context.token_prefix == "ord"
