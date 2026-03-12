from pathlib import Path

from agent.graph import AutocompleteGraphEngine
from backend.autocomplete_engine import AutocompleteEngine
from backend.llm import BaseLLMProvider
from backend.schema_manager import SchemaManager

ROOT_DIR = Path(__file__).resolve().parent.parent


class FakeLLMProvider(BaseLLMProvider):
    def generate_completion(self, sql_prefix, schema_snapshot, context):
        return ["order_date >= date('now', '-7 day')", "price > 100"]


def make_components(tmp_path: Path, provider):
    db_path = tmp_path / "graph.db"
    manager = SchemaManager(db_path)
    manager.initialize(ROOT_DIR / "db" / "init.sql")

    engine = AutocompleteEngine(manager)
    graph = AutocompleteGraphEngine(engine, manager, provider)
    return graph


def test_graph_returns_hybrid_mode_with_llm(tmp_path: Path):
    graph = make_components(tmp_path, FakeLLMProvider())

    sql = "SELECT * FROM orders WHERE "
    result = graph.run(sql=sql, cursor=len(sql), use_llm=True, max_suggestions=100)

    assert result["mode"] == "hybrid"
    assert result["suggestions"][0] in {
        "order_date >= date('now', '-7 day')",
        "price > 100",
    }
    assert any("order_date" in item for item in result["suggestions"])
    assert "timings_ms" in result["debug"]
    assert result["debug"]["suggestion_sources"]["order_date >= date('now', '-7 day')"] == "llm"


def test_graph_degrades_to_rule_mode_without_llm(tmp_path: Path):
    graph = make_components(tmp_path, provider=None)

    sql = "SELECT * FROM orders WHERE "
    result = graph.run(sql=sql, cursor=len(sql), use_llm=True)

    assert result["mode"] == "rule_only"
    assert result["suggestions"]
