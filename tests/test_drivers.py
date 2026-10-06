DRIVER = {
    "full_name": "Ravi Kumar",
    "licence_number": "TN0120250001",
    "licence_expiry": "2028-06-30",
}


def test_duplicate_licence_returns_409(client):
    client.post("/drivers", json=DRIVER)
    response = client.post("/drivers", json=DRIVER)
    assert response.status_code == 409

def test_invalid_licence_date_returns_422(client):
    bad_driver = {**DRIVER, "licence_expiry": "2028-13-45"}
    response = client.post("/drivers", json=bad_driver)
    assert response.status_code==422