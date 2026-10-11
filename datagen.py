"""Synthetic data generator for demos and learning.

Seeded and deterministic: the same seed always builds the same world, so
tests and demos are repeatable. The generator only produces Python dicts -
the endpoint decides how to insert them.
"""
from random import Random

from zones import haversine_km

CITIES = [
    ("Chennai", 13.0827, 80.2707), ("Coimbatore", 11.0168, 76.9558),
    ("Madurai", 9.9252, 78.1198), ("Tiruchirappalli", 10.7905, 78.7047),
    ("Salem", 11.6643, 78.1460), ("Tirunelveli", 8.7139, 77.7567),
    ("Erode", 11.3410, 77.7172), ("Vellore", 12.9165, 79.1325),
    ("Thanjavur", 10.7870, 79.1378), ("Dindigul", 10.3673, 77.9803),
    ("Cuddalore", 11.7480, 79.7714), ("Kanchipuram", 12.8342, 79.7036),
    ("Karur", 10.9601, 78.0766), ("Rajapalayam", 9.4533, 77.5527),
    ("Pudukkottai", 10.3833, 78.8001), ("Neyveli", 11.5330, 79.4830),
]

FIRST_NAMES = ["Ravi", "Suresh", "Kumar", "Arun", "Vijay", "Sekar",
               "Mani", "Prakash", "Ganesan", "Murugan", "Bala", "Karthik",
               "Sathish", "Ramesh", "Anand", "Dinesh"]
LAST_NAMES = ["Kumar", "Raj", "Selvam", "Krishnan", "Subramani", "Palani",
              "Murthy", "Chettiar", "Pillai", "Nadar"]

VEHICLE_TYPES = ["truck", "lorry", "van", "container"]
CARGO = ["Rice bags", "Steel rods", "Cotton bales", "Electronics",
         "Spare parts", "Textiles", "Sugar sacks", "Cement bags",
         "Furniture", "Packaged food", "Auto components", "Fertiliser"]
HAZMAT_CARGO = ["Gas cylinders", "Paint drums", "Chemical drums"]

PLATE_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"


def _plate(rng, used):
    while True:
        plate = "TN{:02d}{}{}{:04d}".format(
            rng.randint(1, 12),
            rng.choice(PLATE_LETTERS), rng.choice(PLATE_LETTERS),
            rng.randint(1, 9999),
        )
        if plate not in used:
            used.add(plate)
            return plate


def generate_world(seed, n_locations, n_vehicles, n_drivers, n_shipments):
    """Return dicts for locations, roads, vehicles, drivers, shipments."""
    rng = Random(seed)

    cities = rng.sample(CITIES, min(n_locations, len(CITIES)))
    locations = []
    extra = n_locations - len(cities)
    branch_towns = ["Branch {:02d}".format(i + 1) for i in range(extra)]
    for i, entry in enumerate(cities + branch_towns):
        if i < len(cities):
            city_name, lat, lng = entry
        else:
            city_name = entry
            # spread branch depots around the state
            lat = rng.uniform(9.0, 13.0)
            lng = rng.uniform(77.5, 80.2)
        locations.append({
            "name": city_name,
            "location_type": ("city" if i < len(cities) else "branch_depot"),
            "latitude": lat, "longitude": lng,
        })

    # Road network: minimum spanning tree by distance keeps it connected,
    # then a few extra roads add alternative routes.
    n = len(locations)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = locations[i], locations[j]
            pairs.append((haversine_km(a["latitude"], a["longitude"],
                                       b["latitude"], b["longitude"]), i, j))
    pairs.sort()
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    roads = []
    added = set()

    def add_road(i, j, trunk=False):
        i, j = min(i, j), max(i, j)  # schema requires from < to
        key = (i, j)
        if key in added:
            return
        added.add(key)
        a, b = locations[i], locations[j]
        straight = haversine_km(a["latitude"], a["longitude"],
                                b["latitude"], b["longitude"])
        distance = round(straight * rng.uniform(1.15, 1.45), 1)
        roads.append({
            "from": i, "to": j, "distance_km": distance,
            # trunk roads always stay open so the network stays connected
            "status": "closed" if (not trunk and rng.random() < 0.05)
            else "open",
            "risk_score": (rng.randint(70, 95) if rng.random() < 0.08
                           else rng.randint(0, 30)),
        })

    for d, i, j in pairs:
        if find(i) != find(j):
            parent[find(i)] = find(j)
            add_road(i, j, trunk=True)
        if len(added) >= n - 1:
            break
    extra_roads = int(n * 0.6)
    tries = 0
    while len(added) < n - 1 + extra_roads and tries < 200:
        tries += 1
        i, j = rng.sample(range(n), 2)
        add_road(i, j)

    used_plates = set()
    vehicles = []
    for _ in range(n_vehicles):
        expiry_year = rng.randint(2024, 2038)
        vehicles.append({
            "plate": _plate(rng, used_plates),
            "vehicle_type": rng.choice(VEHICLE_TYPES),
            "capacity_kg": rng.choice([1500, 3500, 5000, 7500, 9000, 16000]),
            "insurance_expiry": "{}-{:02d}-{:02d}".format(
                expiry_year, rng.randint(1, 12), rng.randint(1, 28)),
            "fitness_expiry": "{}-{:02d}-{:02d}".format(
                rng.randint(2024, 2038), rng.randint(1, 12), rng.randint(1, 28)),
        })

    drivers = []
    for i in range(n_drivers):
        name = "{} {}".format(rng.choice(FIRST_NAMES),
                              rng.choice(LAST_NAMES))
        drivers.append({
            "full_name": name,
            "licence_number": "TN{:02d}20{:02d}{:05d}".format(
                rng.randint(1, 12), rng.randint(10, 25), i + 1),
            "licence_expiry": "{}-{:02d}-{:02d}".format(
                rng.randint(2024, 2036), rng.randint(1, 12), rng.randint(1, 28)),
            "hazmat_certified": rng.random() < 0.3,
        })

    shipments = []
    for i in range(n_shipments):
        origin, dest = rng.sample(range(n), 2)
        if rng.random() < 0.1:
            cargo = rng.choice(HAZMAT_CARGO)
            hazardous = True
        else:
            cargo = rng.choice(CARGO)
            hazardous = False
        shipments.append({
            "description": cargo,
            "weight_kg": rng.randint(100, 9000),
            "is_hazardous": hazardous,
            "origin_id": origin,   # index into locations, fixed up on insert
            "destination_id": dest,
            "required_vehicle_type": (rng.choice(VEHICLE_TYPES)
                                      if rng.random() < 0.3 else None),
        })

    return {
        "locations": locations, "roads": roads, "vehicles": vehicles,
        "drivers": drivers, "shipments": shipments,
    }
