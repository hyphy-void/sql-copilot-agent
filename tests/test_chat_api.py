def test_chat_plan_creates_pending_proposal(client):
    response = client.post(
        "/chat/plan",
        json={
            "prompt": "创建 demo_users 表",
            "use_llm": False,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    proposal = payload["proposal"]
    assert proposal["status"] == "PENDING"
    assert proposal["proposal_id"]
    assert proposal["operations"]


def test_chat_approve_executes_create_table(client):
    plan_response = client.post(
        "/chat/plan",
        json={
            "prompt": "创建 demo_orders 表",
            "use_llm": False,
        },
    )
    proposal = plan_response.json()["proposal"]

    approve_response = client.post(
        f"/chat/proposals/{proposal['proposal_id']}/approve",
        json={
            "approval_token": proposal["approval_token"],
            "approver": "test-suite",
        },
    )
    assert approve_response.status_code == 200

    body = approve_response.json()
    assert body["proposal"]["status"] == "EXECUTED"
    assert any(
        result["status"] == "success" for result in body["proposal"]["execution_results"]
    )

    tables_response = client.get("/schema/tables")
    assert tables_response.status_code == 200
    assert "demo_orders" in tables_response.json()["tables"]


def test_blocked_proposal_cannot_be_approved(client):
    plan_response = client.post(
        "/chat/plan",
        json={
            "prompt": "DROP TABLE users;",
            "use_llm": False,
        },
    )
    proposal = plan_response.json()["proposal"]
    assert proposal["has_blocking_risk"] is True

    approve_response = client.post(
        f"/chat/proposals/{proposal['proposal_id']}/approve",
        json={
            "approval_token": proposal["approval_token"],
        },
    )
    assert approve_response.status_code == 400
    assert "blocked operations" in approve_response.json()["detail"].lower()
