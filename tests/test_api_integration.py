def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["llm_enabled"] is False
    assert payload["safe_mode"] == "strict"
    assert response.headers["X-Request-ID"]


def test_db_capabilities(client):
    response = client.get("/db/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "sqlite"
    assert payload["connected"] is True


def test_schema_endpoints(client):
    tables_response = client.get("/schema/tables")
    assert tables_response.status_code == 200
    assert "users" in tables_response.json()["tables"]

    overview_response = client.get("/schema/overview")
    assert overview_response.status_code == 200
    overview = overview_response.json()["tables"]
    assert any(item["table"] == "users" for item in overview)
    assert all("column_count" in item and "key_columns" in item for item in overview)

    columns_response = client.get("/schema/columns/users")
    assert columns_response.status_code == 200
    columns = columns_response.json()["columns"]
    assert any(column["name"] == "email" for column in columns)


def test_autocomplete_rule_mode(client):
    payload = {
        "sql": "SELECT u. FROM users u",
        "cursor": len("SELECT u."),
        "use_llm": False,
        "max_suggestions": 10,
    }
    response = client.post("/autocomplete", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "rule_only"
    assert body["strategy"] == "rule_only"
    assert "u.id" in body["suggestions"]
    assert "ui_context_label" in body["debug"]
    assert "suggestion_reasons" in body["debug"]
    assert body["debug"]["suggestion_reasons"]["u.id"]
    assert body["items"]
    assert {"text", "source", "confidence", "reason_code", "reason"} <= set(body["items"][0].keys())


def test_autocomplete_degrades_without_llm_provider(client):
    payload = {
        "sql": "SELECT * FROM orders WHERE ",
        "cursor": len("SELECT * FROM orders WHERE "),
        "use_llm": True,
    }
    response = client.post("/autocomplete", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "rule_only"
    assert body["suggestions"]
    assert "fallback_reason" in body["debug"]
    assert "LLM" in body["debug"]["fallback_reason"]


def test_autocomplete_join_infer_contract(client):
    payload = {
        "sql": "SELECT * FROM orders JOIN us",
        "cursor": len("SELECT * FROM orders JOIN us"),
        "use_llm": False,
        "max_suggestions": 20,
    }
    response = client.post("/autocomplete", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == "join_infer"
    assert any(item["source"] == "join_infer" for item in body["items"])
    assert "chosen_strategy" in body["debug"]
