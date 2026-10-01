from fastapi.testclient import TestClient

from app.main import app
from app.transcript import load_sample

client = TestClient(app)


def test_health_reports_demo_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.get("/api/health").json()
    assert body["engine"] == "rules"
    assert body["connectors"]["calendar"] == "live"


def test_api_analyze_returns_items_and_routing():
    resp = client.post("/api/analyze", json={"transcript": load_sample("leadership-sync"), "meeting_date": "2026-10-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] == "rules"
    assert len(data["analysis"]["action_items"]) == 7
    assert {r["destination"] for r in data["routing"]} == {"linear", "attio", "notion", "calendar"}
    assert all(
        r["request"]["headers"].get("Authorization", "<redacted>") == "<redacted>"
        for r in data["routing"]
        if r["destination"] != "calendar"
    )


def test_api_analyze_rejects_garbage():
    assert client.post("/api/analyze", json={"transcript": "no speakers in here at all"}).status_code == 422


def test_grain_webhook_with_auto_dispatch_runs_dry():
    payload = {
        "title": "Partner sync",
        "date": "2026-10-01",
        "participants": [{"name": "Sam Rivera", "role": "CEO"}, {"name": "Ana Ruiz", "role": "COO", "organization": "Acme Clinics"}],
        "transcript": [
            {"start": "00:00:05", "speaker": "Ana Ruiz", "text": "Can you send the contract?"},
            {"start": "00:00:09", "speaker": "Sam Rivera", "text": "Yes, I'll send the contract by Friday."},
        ],
        "auto_dispatch": True,
    }
    data = client.post("/api/webhooks/grain", json=payload).json()
    assert data["analysis"]["action_items"][0]["title"] == "Send the contract"
    assert data["dispatch"][0]["status"] == "dry_run"


def test_html_flow_analyze_then_dispatch():
    page = client.post("/analyze", data={"transcript": load_sample("summit-team-plan")})
    assert page.status_code == 200 and "Send approved items" in page.text
    import html
    import re

    items_json = html.unescape(re.search(r'name="items_json" value="([^"]*)"', page.text).group(1))
    result = client.post("/dispatch", data={"items_json": items_json, "meeting_title": "Summit", "approved": ["0", "2"]})
    assert result.status_code == 200
    assert result.text.count('class="result ') == 2


def test_samples_endpoint():
    assert "Title:" in client.get("/api/samples/investor-checkin").text
    assert client.get("/api/samples/nope").status_code == 404
