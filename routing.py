import networkx as nx


def build_graph(edges, risk_weight=0.0):
    """Turn road_edges rows into an undirected graph. Closed roads are left out.

    risk_weight=0 gives pure shortest-distance routing; risk_weight=1 makes a
    maximum-risk road (100) cost double its distance.
    """
    g = nx.Graph()
    for e in edges:
        if e["status"] != "open":
            continue
        cost = float(e["distance_km"]) * (1 + e["risk_score"] / 100 * risk_weight)
        g.add_edge(
            e["from_location_id"],
            e["to_location_id"],
            distance_km=float(e["distance_km"]),
            risk_score=e["risk_score"],
            weight=cost,
        )
    return g


def _path_summary(g, path):
    legs = [g[a][b] for a, b in zip(path, path[1:])]
    risks = [leg["risk_score"] for leg in legs]
    return {
        "path": path,
        "distance_km": round(sum(leg["distance_km"] for leg in legs), 2),
        "max_risk": max(risks, default=0),
        "avg_risk": round(sum(risks) / len(risks), 1) if risks else 0.0,
        "weighted_cost": round(sum(leg["weight"] for leg in legs), 2),
    }


def shortest_route(edges, start, end, risk_weight=0.0):
    """Shortest path (by chosen weights). Returns None if no open route exists."""
    g = build_graph(edges, risk_weight)
    if start not in g or end not in g:
        return None
    try:
        path = nx.shortest_path(g, start, end, weight="weight")
    except nx.NetworkXNoPath:
        return None
    return _path_summary(g, path)


def ranked_routes(edges, start, end, k=3, risk_weight=1.0):
    """Up to k distinct open routes, safest-weighted best first.

    Simplified Yen: repeatedly knock one edge out of the best route found so
    far and recompute, keeping any genuinely new path. Returns [] when the
    locations are unreachable.
    """
    g = build_graph(edges, risk_weight)
    if start not in g or end not in g:
        return []
    try:
        best = nx.shortest_path(g, start, end, weight="weight")
    except nx.NetworkXNoPath:
        return []
    found = [best]
    seen = {tuple(best)}
    for _ in range(k - 1):
        candidate = None
        for base in found:
            for a, b in zip(base, base[1:]):
                g2 = g.copy()
                g2.remove_edge(a, b)
                try:
                    alt = nx.shortest_path(g2, start, end, weight="weight")
                except nx.NetworkXNoPath:
                    continue
                if tuple(alt) not in seen:
                    seen.add(tuple(alt))
                    if candidate is None or _path_summary(g2, alt)["weighted_cost"] < candidate[0]:
                        candidate = (_path_summary(g2, alt)["weighted_cost"], alt)
        if candidate is None:
            break
        found.append(candidate[1])
    summaries = [_path_summary(g, p) for p in found]
    summaries.sort(key=lambda r: r["weighted_cost"])
    return summaries
