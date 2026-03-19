from pathlib import Path

from backend.autocomplete_engine import AutocompleteEngine
from backend.schema_manager import SchemaManager

ROOT_DIR = Path(__file__).resolve().parent.parent


def make_engine(tmp_path: Path) -> AutocompleteEngine:
    db_path = tmp_path / "unit.db"
    manager = SchemaManager(db_path)
    manager.initialize(ROOT_DIR / "db" / "init.sql")
    return AutocompleteEngine(manager)


def test_suggest_tables(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT * FROM or"

    result = engine.suggest_rules(sql, len(sql))
    assert "orders" in result.suggestions


def test_suggest_alias_columns(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT u. FROM users u"

    result = engine.suggest_rules(sql, len("SELECT u."))
    assert "u.id" in result.suggestions
    assert "u.name" in result.suggestions


def test_suggest_keywords(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SEL"

    result = engine.suggest_rules(sql, len(sql))
    assert "SELECT" in result.suggestions


def test_where_with_trailing_space_prefers_columns(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT * FROM orders WHERE "

    result = engine.suggest_rules(sql, len(sql))
    assert "orders.id" in result.suggestions
    assert "orders.order_date" in result.suggestions


def test_join_inference_suggests_related_table(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT * FROM orders JOIN us"

    result = engine.suggest_rules(sql, len(sql), max_suggestions=20)

    assert any(item.source == "join_infer" for item in result.items)
    assert any("JOIN users ON orders.user_id = users.id" == item.text for item in result.items)
    assert result.strategy == "join_infer"


def test_recovery_mode_returns_fixup_suggestion(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT ("

    result = engine.suggest_rules(sql, len(sql), max_suggestions=20)

    assert result.strategy == "recovery"
    assert any(item.source == "recovery" for item in result.items)


def test_group_by_ranks_columns(tmp_path: Path):
    engine = make_engine(tmp_path)
    sql = "SELECT orders.user_id, COUNT(*) FROM orders GROUP BY "

    result = engine.suggest_rules(sql, len(sql))

    assert result.context.clause == "group_by"
    assert result.items[0].reason_code in {"group_by", "select_column"}
