"""Phase 12: polish - landing endpoint and docs surface."""


def test_root_lists_the_surfaces(client, anon_client):
    root = anon_client.get("/").json()
    assert "Dispatch Console is running" in root["message"]
    for key in ("driver_app", "interactive_docs", "health", "metrics",
                "audit_verify", "end_to_end_demo"):
        assert key in root, key
    # the interactive docs actually render
    docs = anon_client.get("/docs")
    assert docs.status_code == 200
    assert "FastAPI" in docs.text or "Swagger" in docs.text
