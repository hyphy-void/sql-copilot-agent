def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["llm_enabled"] is False


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
    assert "u.id" in body["suggestions"]


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
