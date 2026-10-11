"""Phase 9: guardrailed AI copilot - intents, proposals, human decisions."""
import pytest


def build_world(client):
    """Datagen world + trained model so informational intents have data."""
    assert client.post("/datagen", json={
        "scale": "small", "seed": 7}).status_code == 200
    client.post("/model/train", json={})


def ask(client, question):
    response = client.post("/copilot/ask", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def test_summary_intents_answer_without_proposals(client):
    build_world(client)
    fleet = ask(client, "how many vehicles are idle?")
    assert fleet["intent"] == "fleet_summary"
    assert "Fleet:" in fleet["answer"]
    assert fleet["requires_human"] is False
    assert fleet["proposal_id"] is None

    ships = ask(client, "what is the pending shipment backlog?")
    assert ships["intent"] == "shipments_summary"
    assert "pending" in ships["answer"].lower()


def test_plan_intent_makes_an_executable_proposal(client):
    build_world(client)
    result = ask(client, "propose a plan for today")
    assert result["intent"] == "propose_plan"
    if result["proposal"]:  # world has clean combos
        assert result["requires_human"] is True
        assert result["proposal"]["action"] == "assign"
        assert {"shipment_id", "vehicle_id", "driver_id"} <= \
            set(result["proposal"])
        assert result["proposal_id"] is not None


def test_model_health_intent_reflects_training(client):
    result = ask(client, "what is the model health?")
    assert result["intent"] == "model_health"
    assert "not been trained" in result["answer"]
    build_world(client)
    result = ask(client, "what is the model health?")
    assert f"v{result['answer'].split('v')[1].split(':')[0]}" in result["answer"]
    assert "trust" in result["answer"].lower()


def test_unknown_question_falls_back_to_help(client):
    result = ask(client, "what is the weather on mars?")
    assert result["intent"] == "help"
    assert result["confidence"] < 0.5
    assert "never execute" in result["answer"]


def test_assign_intent_blocked_by_guardrails_has_no_proposal(client):
    build_world(client)
    ships = client.get("/shipments").json()
    pending = next(s for s in ships if s["status"] == "pending"
                   and not s["is_hazardous"])
    vehicles = client.get("/vehicles").json()
    # a tiny vehicle that cannot carry the weight -> hard block
    small = client.post("/vehicles", json={
        "plate": "TN 99 SM 0001", "vehicle_type": "truck",
        "capacity_kg": 1, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    result = ask(client, f"assign shipment {pending['id']} to vehicle {small}")
    assert result["intent"] == "assign_shipment"
    assert "BLOCKED" in result["answer"]
    assert result["proposal"] is None
    assert any(g["severity"] == "block" for g in result["guardrail_results"])


def test_zone_proposal_accept_adds_real_zone(client):
    client.post("/locations", json={
        "name": "Z town", "location_type": "depot",
        "latitude": 16.0, "longitude": 80.0, "is_depot": True})
    result = ask(client, "add forbidden zone at lat 16.05 lng 80.05 radius 3")
    assert result["proposal"]["action"] == "zone"
    assert result["requires_human"] is True
    pid = result["proposal_id"]

    decided = client.post(f"/copilot/proposals/{pid}/decision",
                          json={"decision": "accepted",
                                "note": "conflict reported on news"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["outcome"] == "allowed"
    assert decided.json()["result"]["executed"] is True

    zones = client.get("/zones").json()
    assert any(z["name"].startswith("Copilot zone") for z in zones)

    ledger = client.get("/copilot/proposals").json()[0]
    assert ledger["status"] == "accepted"
    assert ledger["decided_by"] == "dispatcher"

    # deciding again is refused
    again = client.post(f"/copilot/proposals/{pid}/decision",
                        json={"decision": "rejected"})
    assert again.status_code == 409


def test_rejected_proposal_executes_nothing(client):
    result = ask(client, "add forbidden zone at lat 17.02 lng 81.02 radius 2")
    pid = result["proposal_id"]
    decided = client.post(f"/copilot/proposals/{pid}/decision",
                          json={"decision": "rejected",
                                "note": "bad intelligence"})
    assert decided.status_code == 200
    assert decided.json()["result"]["executed"] is False
    assert all(not z["name"].startswith("Copilot zone")
               for z in client.get("/zones").json())
    ledger = client.get("/copilot/proposals").json()[0]
    assert ledger["status"] == "rejected"


def test_accepted_assign_runs_through_guardrails(client):
    build_world(client)
    ships = client.get("/shipments").json()
    pending = next(s for s in ships if s["status"] == "pending"
                   and not s["is_hazardous"])
    drivers = client.get("/drivers").json()
    idle = [v for v in client.get("/vehicles").json()]
    result = None
    for v in idle:
        candidate = ask(client, f"assign shipment {pending['id']} "
                                f"to vehicle {v['id']} and driver "
                                f"{drivers[0]['id']}")
        if candidate["proposal"]:
            result = candidate
            break
    assert result is not None, "expected at least one clean combo"
    pid = result["proposal_id"]
    decided = client.post(f"/copilot/proposals/{pid}/decision",
                          json={"decision": "accepted"})
    assert decided.status_code == 200, decided.text
    body = decided.json()
    if body["outcome"] == "allowed":
        shipments = {s["id"]: s for s in client.get("/shipments").json()}
        assert shipments[pending["id"]]["status"] == "assigned"

    # the copilot itself left its own audit trail
    audits = client.get("/audit?limit=200").json()
    assert any(a["action"] == "copilot.suggested"
               and a["actor_type"] == "ai_copilot" for a in audits)
    mine = [a for a in audits if a["action"] == "copilot.accepted"]
    assert len(mine) == 1 and mine[0]["actor_type"] == "dispatcher"
    assert mine[0]["details"]["intent"] == result["intent"]
    assert client.get("/audit/verify").json()["ok"] is True


def test_decision_on_missing_proposal_404(client):
    assert client.post("/copilot/proposals/987654/decision",
                       json={"decision": "accepted"}).status_code == 404
