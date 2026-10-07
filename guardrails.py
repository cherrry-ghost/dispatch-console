from dataclasses import dataclass
from datetime import timedelta

@dataclass(frozen=True)
class RuleResult:
    rule: str       # which rule fired, e.g. "capacity"
    severity: str   # "block" (refuse) or "warn" (allow, but flag)
    message: str    # human-readable reason, later stored in the audit trail


def check_capacity(vehicle, driver, shipment, today):
    weight = shipment["weight_kg"]
    capacity = vehicle["capacity_kg"]
    if weight > capacity:
        return RuleResult(
            "capacity", "block",
            f"Shipment weighs {weight} kg but the vehicle can carry only {capacity} kg.",
        )
    return None  # rule passed


WARN_DAYS = 30


def _expiry_result(rule, label, expiry, today):
    if expiry is None:
        return RuleResult(rule, "block", f"{label} expiry date is not recorded, so validity cannot be confirmed.")
    if expiry < today:
        return RuleResult(rule, "block", f"{label} expired on {expiry}.")
    if expiry - today <= timedelta(days=WARN_DAYS):
        return RuleResult(rule, "warn", f"{label} expires on {expiry}, within {WARN_DAYS} days.")
    return None


def check_licence(vehicle, driver, shipment, today):
    return _expiry_result("licence", "Driver licence", driver["licence_expiry"], today)


def check_insurance(vehicle, driver, shipment, today):
    return _expiry_result("insurance", "Vehicle insurance", vehicle["insurance_expiry"], today)


def check_fitness(vehicle, driver, shipment, today):
    return _expiry_result("fitness", "Vehicle fitness certificate", vehicle["fitness_expiry"], today)


def check_vehicle_type(vehicle, driver, shipment, today):
    required = shipment.get("required_vehicle_type")
    if required is not None and required != vehicle["vehicle_type"]:
        return RuleResult(
            "vehicle_type", "block",
            f"Shipment needs a '{required}' but the vehicle is a '{vehicle['vehicle_type']}'.",
        )
    return None


def check_hazmat(vehicle, driver, shipment, today):
    if shipment["is_hazardous"] and not driver["hazmat_certified"]:
        return RuleResult("hazmat", "block", "Hazardous shipment but the driver is not hazmat certified.")
    return None


RULES = [
    check_capacity, check_licence, check_insurance,
    check_fitness, check_vehicle_type, check_hazmat,
]


def evaluate(vehicle, driver, shipment, today):
    results = []
    for rule in RULES:
        result = rule(vehicle, driver, shipment, today)
        if result is not None:
            results.append(result)
    return results


def is_allowed(results):
    return not any(r.severity == "block" for r in results)