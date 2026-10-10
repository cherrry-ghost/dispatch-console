"""Capacity planner: proposes which vehicle carries which pending shipment.

OR-Tools CP-SAT only proposes - it never decides. Nothing is saved here, and
the endpoint that calls this writes every run to the audit ledger. All rules
come from guardrails.py so the planner can never invent its own policy.
"""
from ortools.sat.python import cp_model

from guardrails import (
    check_capacity,
    check_vehicle_type,
    check_insurance,
    check_fitness,
)

NEAR_CAPACITY_RATIO = 0.9  # same threshold as the guardrails warning
SOLVER_TIME_LIMIT_S = 5.0


def _blocks(results):
    return [r for r in results if r is not None and r.severity == "block"]


def _eligible_vehicles(vehicles, today):
    """Drop vehicles that already fail a hard vehicle-level rule."""
    eligible = []
    notes = {}
    for v in vehicles:
        results = [check_insurance(v, None, None, today),
                   check_fitness(v, None, None, today)]
        if _blocks(results):
            continue
        warns = [r.message for r in results if r is not None and r.severity == "warn"]
        if warns:
            notes[v["id"]] = warns
        eligible.append(v)
    return eligible, notes


def _pair_allowed(vehicle, shipment, today):
    return not _blocks([check_capacity(vehicle, None, shipment, today),
                        check_vehicle_type(vehicle, None, shipment, today)])


def _skip_reason(shipment, eligible, all_excluded):
    if all_excluded:
        return ("No eligible vehicle: every vehicle is missing insurance or "
                "fitness dates, or they have expired.")
    reasons = []
    required = shipment.get("required_vehicle_type")
    if required is not None and not any(
        v["vehicle_type"] == required for v in eligible
    ):
        reasons.append(f"no eligible vehicle of type '{required}'")
    biggest = max(v["capacity_kg"] for v in eligible)
    if shipment["weight_kg"] > biggest:
        reasons.append(
            f"it weighs {shipment['weight_kg']} kg and the biggest eligible "
            f"vehicle carries only {biggest} kg"
        )
    if not reasons:
        return ("No eligible vehicle could carry it alongside the other "
                "pending shipments.")
    return "No eligible vehicle: " + " and ".join(reasons) + "."


def plan_shipments(shipments, vehicles, today):
    """Return proposals + explainable skips. Pure function, saves nothing."""
    eligible, vehicle_notes = _eligible_vehicles(vehicles, today)
    all_excluded = not eligible

    allowed = {}  # (shipment index, vehicle index) -> shipment weight
    for si, s in enumerate(shipments):
        for vi, v in enumerate(eligible):
            if _pair_allowed(v, s, today):
                allowed[(si, vi)] = s["weight_kg"]

    model = cp_model.CpModel()
    x = {(si, vi): model.NewBoolVar(f"s{si}_v{vi}") for (si, vi) in allowed}
    used = {vi: model.NewBoolVar(f"used_v{vi}") for vi in range(len(eligible))}

    for si in range(len(shipments)):
        model.Add(sum(var for p, var in x.items() if p[0] == si) <= 1)
    for vi, v in enumerate(eligible):
        vehicle_pairs = [p for p in x if p[1] == vi]
        model.Add(sum(allowed[p] * x[p] for p in vehicle_pairs)
                  <= v["capacity_kg"])
        for p in vehicle_pairs:
            model.AddImplication(x[p], used[vi])  # used <=> at least one x
        model.Add(used[vi] <= sum(x[p] for p in vehicle_pairs))

    # First maximize how many shipments get planned, then use as few vehicles
    # as possible. The big multiplier makes the first goal always win.
    big = len(shipments) + 1
    model.Maximize(big * sum(x.values()) - sum(used.values()))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1  # deterministic runs
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_S
    solver.Solve(model)

    chosen = {si: vi for (si, vi), var in x.items() if solver.Value(var)}
    loads = {}
    for si, vi in chosen.items():
        loads[vi] = loads.get(vi, 0) + shipments[si]["weight_kg"]

    proposals = []
    for si in sorted(chosen):
        vi = chosen[si]
        proposals.append({
            "shipment_id": shipments[si]["id"],
            "vehicle_id": eligible[vi]["id"],
            "weight_kg": shipments[si]["weight_kg"],
            "vehicle_total_load_kg": loads[vi],
        })

    # Vehicle-level notes appear once per vehicle, not per shipment.
    vehicles_out = []
    for vi, v in enumerate(eligible):
        if vi not in loads:
            continue
        warnings = list(vehicle_notes.get(v["id"], []))
        if loads[vi] >= NEAR_CAPACITY_RATIO * v["capacity_kg"]:
            warnings.append(
                f"Plan loads this vehicle to {loads[vi]} of "
                f"{v['capacity_kg']} kg (90% or more of capacity)."
            )
        vehicles_out.append({
            "vehicle_id": v["id"],
            "total_load_kg": loads[vi],
            "warnings": warnings,
        })

    skipped = []
    for si, s in enumerate(shipments):
        if si in chosen:
            continue
        has_any_pair = any(p[0] == si for p in allowed)
        reason = ("No eligible vehicle could carry it alongside the other "
                  "pending shipments." if has_any_pair
                  else _skip_reason(s, eligible, all_excluded))
        skipped.append({"shipment_id": s["id"], "reason": reason})

    return {
        "proposals": proposals,
        "vehicles": vehicles_out,
        "skipped": skipped,
        "summary": {
            "shipments_considered": len(shipments),
            "shipments_planned": len(proposals),
            "eligible_vehicles": len(eligible),
            "vehicles_used": len(loads),
        },
    }
