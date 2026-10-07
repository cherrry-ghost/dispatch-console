from datetime import date

from guardrails import evaluate, is_allowed

TODAY = date(2026, 10, 7)


def make_vehicle(**changes):
    v = {"capacity_kg": 5000, "vehicle_type": "box_truck",
         "insurance_expiry": date(2027, 6, 1), "fitness_expiry": date(2027, 6, 1)}
    v.update(changes)
    return v


def make_driver(**changes):
    d = {"licence_expiry": date(2028, 1, 1), "hazmat_certified": False}
    d.update(changes)
    return d


def make_shipment(**changes):
    s = {"weight_kg": 4000, "is_hazardous": False, "required_vehicle_type": None}
    s.update(changes)
    return s


def run(vehicle=None, driver=None, shipment=None):
    return evaluate(vehicle or make_vehicle(), driver or make_driver(),
                    shipment or make_shipment(), TODAY)


def fired(results):
    return {r.rule for r in results}


def test_baseline_is_allowed():
    results = run()
    assert results == []
    assert is_allowed(results)


def test_overweight_is_blocked():
    results = run(shipment=make_shipment(weight_kg=9000))
    assert not is_allowed(results)
    assert fired(results) == {"capacity"}


def test_expired_licence_is_blocked():
    results = run(driver=make_driver(licence_expiry=date(2026, 10, 6)))
    assert not is_allowed(results)
    assert fired(results) == {"licence"}


def test_licence_expiring_soon_only_warns():
    results = run(driver=make_driver(licence_expiry=date(2026, 10, 17)))
    assert is_allowed(results)
    assert results[0].severity == "warn"


def test_missing_insurance_date_is_blocked():
    results = run(vehicle=make_vehicle(insurance_expiry=None))
    assert not is_allowed(results)
    assert fired(results) == {"insurance"}


def test_expired_fitness_is_blocked():
    results = run(vehicle=make_vehicle(fitness_expiry=date(2026, 1, 1)))
    assert fired(results) == {"fitness"}


def test_wrong_vehicle_type_is_blocked():
    results = run(shipment=make_shipment(required_vehicle_type="tanker"))
    assert fired(results) == {"vehicle_type"}


def test_hazmat_needs_certified_driver():
    results = run(shipment=make_shipment(is_hazardous=True))
    assert fired(results) == {"hazmat"}
    ok = run(shipment=make_shipment(is_hazardous=True),
             driver=make_driver(hazmat_certified=True))
    assert ok == []


def test_all_problems_are_reported_together():
    results = run(shipment=make_shipment(weight_kg=9000),
                  driver=make_driver(licence_expiry=date(2026, 1, 1)))
    assert fired(results) == {"capacity", "licence"}

def test_near_capacity_warns_but_allows():
    results = run(shipment=make_shipment(weight_kg=4600))
    assert fired(results) == {"near_capacity"}
    assert is_allowed(results)


def test_comfortable_load_has_no_warning():
    assert run(shipment=make_shipment(weight_kg=4000)) == []