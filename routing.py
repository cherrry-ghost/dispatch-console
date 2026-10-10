import networkx as nx


def build_graph(edges):
    """Turn road_edges rows into an undirected graph. Closed roads are left out."""
    g = nx.Graph()
    for e in edges:
        if e["status"] != "open":
            continue
        g.add_edge(
            e["from_location_id"],
            e["to_location_id"],
            distance_km=float(e["distance_km"]),
            risk_score=e["risk_score"],
        )
    return g


def shortest_route(edges, start, end):
    """Dijkstra shortest path by distance. Returns None if no open route exists."""
    g = build_graph(edges)
    if start not in g or end not in g:
        return None
    try:
        path = nx.shortest_path(g, start, end, weight="distance_km")
    except nx.NetworkXNoPath:
        return None
    legs = [g[a][b] for a, b in zip(path, path[1:])]
    return {
        "path": path,
        "distance_km": round(sum(leg["distance_km"] for leg in legs), 2),
        "max_risk": max((leg["risk_score"] for leg in legs), default=0),
    }
