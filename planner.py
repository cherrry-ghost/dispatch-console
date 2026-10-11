"""Capacity planner with time windows, depots and mixed speeds.

OR-Tools CP-SAT only proposes - it never decides. Nothing is saved here, and
the endpoint that calls this writes every run to the audit ledger. All rules
come from guardrails.py so the planner can never invent its own policy.

Each vehicle gets a little round trip: start at its home depot, pick up and
drop its assigned shipments one by one, respecting each shipment's ready time
(waiting is allowed) and deadline (being late is not). Travel times come from
the open road network and each vehicle's own speed.
"""
from math import ceil

import networkx as nx
from ortools.sat.python import cp_model

from zones import haversine_km
from guardrails import (
    check_capacity,
    check_vehicle_type,
    check_insurance,
    check_fitness,
)

NEAR_CAPACITY_RATIO = 0.9  # same threshold as the guardrails warning
SOLVER_TIME_LIMIT_S = 5.0
LOAD_MINUTES = 30          # loading at the origin
UNLOAD_MINUTES = 20        # unloading at the destination
DAY_MINUTES = 1440         # everything happens inside one day
BIG = 10 ** 7              # objective weight: plan as many shipments as possible
MED = 10 ** 5              # objective weight: then use as few vehicles as possible


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


def _distance_lookup(edges, locations):
    """Kilometres between location ids over open roads.

    With no roads at all (fresh setups), fall back to a straight-line
    estimate so the planner still works offline.
    """
    g = nx.Graph()
    for e in edges:
        if e["status"] == "open":
            g.add_edge(e["from_location_id"], e["to_location_id"],
                       distance_km=float(e["distance_km"]))
    offline = g.number_of_edges() == 0
    latlng = {l["id"]: (l["latitude"], l["longitude"]) for l in locations}
    ids = sorted(latlng)
    if offline:
        def est(a, b):
            if a == b:
                return 0.0
            (la, lo), (lb, lob) = latlng[a], latlng[b]
            return haversine_km(la, lo, lb, lob) * 1.25
        return {a: {b: est(a, b) for b in ids} for a in ids}, offline
    dist = {}
    for a in ids:
        if a in g:
            dist[a] = nx.single_source_dijkstra_path_length(
                g, a, weight="distance_km")
    return dist, offline


def plan_shipments(shipments, vehicles, today, edges=(), locations=()):
    """Return proposals + explainable skips. Pure function, saves nothing."""
    eligible, vehicle_notes = _eligible_vehicles(vehicles, today)
    all_excluded = not eligible
    dist, offline = _distance_lookup(edges, locations)

    depots = [l["id"] for l in locations if l.get("is_depot")]
    fallback_depot = (depots[0] if depots
                      else min((l["id"] for l in locations), default=None))

    def depot_of(v):
        return v.get("home_depot_id") or fallback_depot

    def km(a, b):
        if a is None or b is None or a not in dist or b not in dist.get(a, {}):
            return None
        return dist[a][b]

    def travel_min(km_value, speed):
        return ceil(km_value * 60.0 / speed)

    allowed = {}      # (shipment index, vehicle index) -> weight kg
    reachable = {si: False for si in range(len(shipments))}
    guard_ok = {si: False for si in range(len(shipments))}
    for si, s in enumerate(shipments):
        for vi, v in enumerate(eligible):
            if not _pair_allowed(v, s, today):
                continue
            guard_ok[si] = True
            legs = [km(depot_of(v), s["origin_id"]),
                    km(s["origin_id"], s["destination_id"]),
                    km(s["destination_id"], depot_of(v))]
            if any(leg is None for leg in legs):
                continue
            reachable[si] = True
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
            model.AddImplication(x[p], used[vi])
        model.Add(used[vi] <= sum(x[p] for p in vehicle_pairs))

    travel_terms = []  # (literal, minutes) for the objective
    routes = {}        # vehicle index -> bookkeeping to read the order back
    for vi, v in enumerate(eligible):
        idxs = sorted(si for (si, vj) in x if vj == vi)
        if not idxs:
            continue
        arcs = []
        # when this vehicle carries nothing, the depot loops on itself
        arcs.append([0, 0, used[vi].Not()])
        order_lits = []   # depot -> origin arc per shipment
        home_lits = []    # destination -> depot arc per shipment
        chain_lits = {}   # (si, sj) -> destination of si to origin of sj
        for k, si in enumerate(idxs):
            node_o, node_d = 1 + 2 * k, 2 + 2 * k
            arcs.append([node_o, node_o, x[(si, vi)].Not()])
            arcs.append([node_d, node_d, x[(si, vi)].Not()])
            start = model.NewBoolVar(f"start_v{vi}_s{si}")
            arcs.append([0, node_o, start])
            order_lits.append((si, start))
            back = model.NewBoolVar(f"home_v{vi}_s{si}")
            arcs.append([node_d, 0, back])
            home_lits.append((si, back))
            arcs.append([node_o, node_d, x[(si, vi)]])
            for kj, sj in enumerate(idxs):
                if sj == si:
                    continue
                lit = model.NewBoolVar(f"chain_v{vi}_s{si}_s{sj}")
                arcs.append([node_d, 1 + 2 * kj, lit])
                chain_lits[(si, sj)] = lit
        model.AddCircuit(arcs)

        # Schedule: minutes of the day for every pickup and drop.
        ready = {si: shipments[si].get("ready_minute") or 0 for si in idxs}
        deadline = {si: shipments[si].get("deadline_minute") for si in idxs}
        pickup = {}
        drop = {}
        for si in idxs:
            pickup[si] = model.NewIntVar(ready[si], DAY_MINUTES,
                                         f"pick_v{vi}_s{si}")
            drop[si] = model.NewIntVar(
                ready[si], deadline[si] or DAY_MINUTES, f"drop_v{vi}_s{si}")

        depot = depot_of(v)
        speed = v["speed_kmh"]
        for si in idxs:
            s = shipments[si]
            leg_km = km(depot, s["origin_id"])
            minutes = travel_min(leg_km, speed)
            model.Add(pickup[si] >= minutes).OnlyEnforceIf(
                dict(order_lits)[si])
            model.Add(drop[si] >= pickup[si] + LOAD_MINUTES
                      + travel_min(km(s["origin_id"], s["destination_id"]),
                                   speed)).OnlyEnforceIf(x[(si, vi)])
            leg_km = km(s["destination_id"], depot)
            model.Add(drop[si] + UNLOAD_MINUTES
                      + travel_min(leg_km, speed)
                      <= DAY_MINUTES).OnlyEnforceIf(dict(home_lits)[si])
        for (si, sj), lit in chain_lits.items():
            a, b = shipments[si], shipments[sj]
            model.Add(pickup[sj] >= drop[si] + UNLOAD_MINUTES
                      + travel_min(km(a["destination_id"], b["origin_id"]),
                                   speed)).OnlyEnforceIf(lit)

        for si, sj_lit in [(si, dict(order_lits)[si]) for si in idxs]:
            travel_terms.append((sj_lit, travel_min(
                km(depot, shipments[si]["origin_id"]), speed)))
        for si in idxs:
            s = shipments[si]
            travel_terms.append((x[(si, vi)], travel_min(
                km(s["origin_id"], s["destination_id"]), speed)))
        routes[vi] = {"order": order_lits, "home": home_lits,
                      "chain": chain_lits, "pickup": pickup, "drop": drop,
                      "depot": depot}

    model.Maximize(
        BIG * sum(x.values()) - MED * sum(used.values())
        - sum(lit * minutes for lit, minutes in travel_terms))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1  # deterministic runs
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_S
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        skipped = []
        for si, s in enumerate(shipments):
            if guard_ok[si]:
                reason = ("No eligible vehicle could carry it alongside the "
                          "other pending shipments.")
            else:
                reason = _skip_reason(s, eligible, all_excluded)
            skipped.append({"shipment_id": s["id"], "reason": reason})
        return {"proposals": [], "vehicles": [], "skipped": skipped,
                "travel_model": "offline-estimate" if offline else "road-network",
                "summary": {"shipments_considered": len(shipments),
                            "shipments_planned": 0,
                            "eligible_vehicles": len(eligible),
                            "vehicles_used": 0}}

    chosen = {si: vi for (si, vi), var in x.items() if solver.Value(var)}
    loads = {}
    for si, vi in chosen.items():
        loads[vi] = loads.get(vi, 0) + shipments[si]["weight_kg"]

    # Read each used vehicle's stop order back from the circuit arcs.
    schedules = {}
    for vi in loads:
        r = routes[vi]
        succ = {}
        for si, lit in r["order"]:
            if solver.Value(lit):
                succ["depot"] = si
        for (si, sj), lit in r["chain"].items():
            if solver.Value(lit):
                succ[si] = sj
        order, node = [], succ.get("depot")
        while node is not None:
            order.append(node)
            node = succ.get(node)
        stops = []
        for si in order:
            stops.append({
                "shipment_id": shipments[si]["id"],
                "pickup_minute": solver.Value(r["pickup"][si]),
                "drop_minute": solver.Value(r["drop"][si]),
                "deadline_minute": shipments[si].get("deadline_minute"),
            })
        schedules[vi] = {"depot_id": r["depot"], "stops": stops}

    proposals = []
    for si in sorted(chosen):
        vi = chosen[si]
        stop = next(st for st in schedules[vi]["stops"]
                    if st["shipment_id"] == shipments[si]["id"])
        proposals.append({
            "shipment_id": shipments[si]["id"],
            "vehicle_id": eligible[vi]["id"],
            "weight_kg": shipments[si]["weight_kg"],
            "vehicle_total_load_kg": loads[vi],
            "pickup_minute": stop["pickup_minute"],
            "drop_minute": stop["drop_minute"],
        })

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
        stops = schedules[vi]["stops"]
        vehicles_out.append({
            "vehicle_id": v["id"],
            "total_load_kg": loads[vi],
            "home_depot_id": schedules[vi]["depot_id"],
            "stops": stops,
            "warnings": warnings,
        })

    skipped = []
    for si, s in enumerate(shipments):
        if si in chosen:
            continue
        if not guard_ok[si]:
            reason = _skip_reason(s, eligible, all_excluded)
        elif not reachable[si]:
            reason = ("No open road connects its origin and destination to "
                      "any depot, so no vehicle can reach it.")
        else:
            reason = ("No eligible vehicle could carry it alongside the "
                      "other pending shipments within the time windows.")
        skipped.append({"shipment_id": s["id"], "reason": reason})

    return {
        "proposals": proposals,
        "vehicles": vehicles_out,
        "skipped": skipped,
        "travel_model": "offline-estimate" if offline else "road-network",
        "summary": {
            "shipments_considered": len(shipments),
            "shipments_planned": len(proposals),
            "eligible_vehicles": len(eligible),
            "vehicles_used": len(loads),
        },
    }
