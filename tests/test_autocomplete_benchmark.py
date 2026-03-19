from __future__ import annotations

import json
from pathlib import Path


def test_autocomplete_eval_cases(client):
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "autocomplete_eval_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    top1_hits = 0
    top3_hits = 0
    fallback_count = 0
    total_ms = 0.0

    for case in cases:
        response = client.post(
            "/autocomplete",
            json={
                "sql": case["sql"],
                "cursor": case["cursor"],
                "use_llm": False,
                "max_suggestions": 10,
            },
        )
        assert response.status_code == 200, case["name"]
        payload = response.json()

        strategy = case.get("expected_strategy")
        if strategy:
            assert payload["strategy"] == strategy, case["name"]

        expected_any = case.get("expected_any", [])
        if expected_any:
            suggestions = payload["suggestions"]
            if suggestions and suggestions[0] in expected_any:
                top1_hits += 1
            if any(item in expected_any for item in suggestions[:3]):
                top3_hits += 1
            assert any(item in expected_any for item in suggestions), case["name"]

        if payload["debug"].get("fallback_reason"):
            fallback_count += 1
        total_ms += float(payload["debug"]["timings_ms"]["total_ms"])

    assert top1_hits >= 1
    assert top3_hits >= 3
    assert fallback_count >= 1
    assert total_ms > 0
