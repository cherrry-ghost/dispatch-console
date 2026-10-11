"""Phase 11: media authenticity checks on evidence and delivery proofs."""
import io
import os

from PIL import Image

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "evidence")


def phone_photo(with_exif=True, size=(800, 600)) -> bytes:
    """A plausible phone photo: real dimensions, optional camera EXIF."""
    img = Image.new("RGB", size, (110, 130, 160))
    for x in range(0, size[0], 40):  # some texture, not a flat fill
        for y in range(0, size[1], 40):
            img.putpixel((x, y), (200, 200, 210))
    buf = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[271] = "Google"            # Make
        exif[272] = "Pixel 7"           # Model
        exif[306] = "2026:10:11 09:41:00"  # DateTime
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def make_incident(client) -> int:
    a = client.post("/locations", json={
        "name": "Media depot", "location_type": "depot",
        "latitude": 15.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Media stop", "location_type": "warehouse",
        "latitude": 15.1, "longitude": 78.1}).json()["id"]
    road = client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b,
        "distance_km": 30}).json()["id"]
    return client.post("/incidents", json={
        "road_edge_id": road,
        "description": "Rockslide on the ghat section",
        "incident_type": "road_blocked"}).json()["id"]


def upload_evidence(client, incident_id: int, blob: bytes, name="p.jpg"):
    result = client.post(f"/incidents/{incident_id}/evidence",
                         files={"file": (name, blob, "image/jpeg")})
    assert result.status_code == 200, result.text
    return result.json()


def test_clean_phone_photo_passes(client):
    incident_id = make_incident(client)
    uploaded = upload_evidence(client, incident_id, phone_photo(True))
    report = uploaded["media_check"]
    assert report["verdict"] == "clean", report["reasons"]
    assert report["score"] >= 75
    flags = [r["flag"] for r in report["reasons"]]
    assert "camera_exif" in flags
    # stored and queryable
    latest = client.get(f"/media/checks/evidence/{uploaded['id']}").json()
    assert latest["verdict"] == "clean"
    listing = client.get("/media/checks").json()
    assert listing[0]["media_kind"] == "evidence"


def test_screenshot_style_photo_is_flagged(client):
    incident_id = make_incident(client)
    uploaded = upload_evidence(client, incident_id, phone_photo(False),
                               name="shot.png")
    report = uploaded["media_check"]
    flags = [r["flag"] for r in report["reasons"]]
    assert "no_exif" in flags
    assert report["verdict"] in ("suspicious", "likely_fake")


def test_duplicate_photo_reuse_is_caught(client):
    incident_a = make_incident(client)
    incident_b = make_incident(client)
    blob = phone_photo(True)
    first = upload_evidence(client, incident_a, blob)
    second = upload_evidence(client, incident_b, blob)
    assert first["media_check"]["verdict"] == "clean"
    second_report = second["media_check"]
    flags = [r["flag"] for r in second_report["reasons"]]
    assert "duplicate" in flags
    assert second_report["verdict"] == "likely_fake"


def test_tampered_file_on_disk_is_detected_on_recheck(client):
    incident_id = make_incident(client)
    uploaded = upload_evidence(client, incident_id, phone_photo(True))
    stored = next(f for f in os.listdir(EVIDENCE_DIR)
                  if f.startswith(uploaded["sha256"][:32])
                  or uploaded["sha256"] in f)
    path = os.path.join(EVIDENCE_DIR, stored)
    with open(path, "ab") as f:
        f.write(b"tampered bytes")
    try:
        report = client.post(
            f"/media/check/evidence/{uploaded['id']}").json()
        assert report["verdict"] == "likely_fake"
        assert any(r["flag"] == "hash_mismatch" for r in report["reasons"])
    finally:
        os.remove(path)  # leave no test litter


def test_recheck_needs_special_role(client, manager_client, anon_client):
    incident_id = make_incident(client)
    uploaded = upload_evidence(client, incident_id, phone_photo(True))
    assert anon_client.post(
        f"/media/check/evidence/{uploaded['id']}").status_code == 401
    # dispatcher (the default client) and manager may recheck
    assert client.post(
        f"/media/check/evidence/{uploaded['id']}").status_code == 200
    assert manager_client.post(
        f"/media/check/evidence/{uploaded['id']}").status_code == 200
    assert client.post("/media/check/nope/1").status_code == 404


def test_video_container_is_unverified_not_fake(client):
    incident_id = make_incident(client)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048
    uploaded = upload_evidence(client, incident_id, mp4, name="clip.mp4")
    report = uploaded["media_check"]
    assert report["verdict"] == "unverified"
    assert any(r["flag"] == "video_container_only"
               for r in report["reasons"])
    # garbage pretending to be mp4: flagged, not trusted
    junk = b"GARBAGE" * 300
    junk_up = upload_evidence(client, make_incident(client), junk,
                              name="junk.mp4")
    assert junk_up["media_check"]["verdict"] == "likely_fake"


def test_delivery_proof_gets_checked_and_copilot_reports(client,
                                                         driver_login):
    a = client.post("/locations", json={
        "name": "Proof depot", "location_type": "depot",
        "latitude": 16.0, "longitude": 79.0, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Proof stop", "location_type": "warehouse",
        "latitude": 16.1, "longitude": 79.1}).json()["id"]
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b, "distance_km": 18})
    vehicle_id = client.post("/vehicles", json={
        "plate": "TN 11 MM 1111", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    driver_id = client.post("/drivers", json={
        "full_name": "Proof Driver", "licence_number": "TN1120260111",
        "licence_expiry": "2035-12-31"}).json()["id"]
    shipment_id = client.post("/shipments", json={
        "description": "Cement bags", "weight_kg": 800,
        "origin_id": a, "destination_id": b}).json()["id"]
    assigned = client.post("/assignments", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "reason": "media test"}).json()
    assignment_id = assigned.get("id") or assigned.get("assignment_id")

    driver = driver_login(driver_id)
    done = driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver",
        files={"photo": ("proof.jpg", phone_photo(True), "image/jpeg")})
    assert done.status_code == 200, done.text
    report = done.json()["proof_check"]
    assert report["media_kind"] == "delivery"
    assert report["verdict"] == "clean", report["reasons"]

    latest = client.get(f"/media/checks/delivery/{assignment_id}").json()
    assert latest["verdict"] == "clean"

    # the copilot can quote the verdict
    answer = client.post("/copilot/ask", json={
        "question": "is the proof for delivery "
                    f"{assignment_id} authentic?"}).json()
    assert answer["intent"] == "media_check"
    assert "CLEAN" in answer["answer"]
    # and for an unknown item it says so honestly
    answer2 = client.post("/copilot/ask", json={
        "question": "is the proof for incident 999 authentic?"}).json()
    assert "nothing to verify" in answer2["answer"]


def test_media_analysis_is_audited(client):
    incident_id = make_incident(client)
    upload_evidence(client, incident_id, phone_photo(True))
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "media.analyzed"]
    assert len(audits) == 1
    assert audits[0]["actor_type"] == "ai_media_checker"
    assert client.get("/audit/verify").json()["ok"] is True
