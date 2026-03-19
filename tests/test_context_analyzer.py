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


def test_detect_where_context_after_space_has_empty_prefix():
    sql = "SELECT * FROM orders WHERE "
    context = detect_context(sql, len(sql))

    assert context.context_type == "where"
    assert context.token_prefix == ""


def test_detect_join_context():
    sql = "SELECT * FROM orders JOIN us"
    context = detect_context(sql, len(sql))

    assert context.context_type == "from"
    assert context.clause == "join"
    assert context.token_prefix == "us"


def test_detect_group_by_context():
    sql = "SELECT user_id, COUNT(*) FROM orders GROUP BY us"
    context = detect_context(sql, len(sql))

    assert context.context_type == "select"
    assert context.clause == "group_by"


def test_detect_order_by_context():
    sql = "SELECT * FROM orders ORDER BY ord"
    context = detect_context(sql, len(sql))

    assert context.context_type == "select"
    assert context.clause == "order_by"


def test_detect_cte_and_subquery_flags():
    sql = "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent WHERE "
    context = detect_context(sql, len(sql))

    assert context.in_cte is True
    assert context.context_type == "where"
