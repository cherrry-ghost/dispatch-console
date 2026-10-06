VEHICLE = {"plate": "TN01AB1234", "vehicle_type": "truck", "capacity_kg": 5000}


def test_list_vehicles_starts_empty(client):
    response = client.get("/vehicles")
    assert response.status_code == 200
    assert response.json() == []


def test_create_vehicle(client):
    response = client.post("/vehicles", json=VEHICLE)
    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["plate"] == "TN01AB1234"


def test_duplicate_plate_returns_409(client):
    client.post("/vehicles", json=VEHICLE)
    response = client.post("/vehicles", json=VEHICLE)
    assert response.status_code == 409