"""XGBoost delay model: proposes ETAs, never decides.

Deterministic by construction: fixed seed and a single worker thread, so the
same training data always produces the same model. The synthetic ground truth
planted into history by the data generator lives here too, so the model and
the data share one definition of reality.

Since Phase 7C the model is a trio of quantile models: one predicts a
pessimistic delay (10th percentile), one the typical delay (median), one an
optimistic delay (90th percentile). Together they give every ETA an honest
"likely between X and Y" range. Explanations come from XGBoost's built-in
contribution values - no extra libraries - and are turned into plain
sentences by explain().
"""
import os

AVG_SPEED_KMH = 40.0
MODEL_VERSION = 2
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "delay_model.json")
MODEL_PATH_LOW = os.path.join(MODEL_DIR, "delay_model_p10.json")
MODEL_PATH_HIGH = os.path.join(MODEL_DIR, "delay_model_p90.json")
MODEL_PATHS = (MODEL_PATH_LOW, MODEL_PATH, MODEL_PATH_HIGH)

FEATURES = ["distance_km", "max_risk", "avg_risk", "is_hazardous",
            "weight_kg", "legs"]

MIN_HISTORY_FOR_TRAINING = 20

FACTOR_TEXT = {
    "distance_km": lambda v: f"the trip is {v:.0f} km long",
    "max_risk": lambda v: f"the route crosses a risk-{v:.0f} road",
    "avg_risk": lambda v: f"average road risk on the route is {v:.0f} of 100",
    "is_hazardous": lambda v: ("the load is hazardous" if v >= 0.5
                               else "the load is not hazardous"),
    "weight_kg": lambda v: f"the load weighs {v:.0f} kg",
    "legs": lambda v: f"the route has {v:.0f} road legs",
}


def route_features(route, shipment):
    """Feature dict from a shortest_route() result plus a shipment row."""
    return {
        "distance_km": route["distance_km"],
        "max_risk": route["max_risk"],
        "avg_risk": route.get("avg_risk", 0.0),
        "is_hazardous": 1 if shipment["is_hazardous"] else 0,
        "weight_kg": shipment["weight_kg"],
        "legs": len(route["path"]) - 1,
    }


def synthetic_truth(features, rng):
    """Ground-truth delay in minutes that the generator plants in history."""
    delay = 15.0                                # loading, paperwork
    delay += features["distance_km"] * 0.15     # longer hauls slip more
    delay += features["max_risk"] * 1.2         # risky roads delay
    delay += 45.0 if features["is_hazardous"] else 0.0
    delay += features["weight_kg"] / 200.0
    delay += rng.gauss(0, 20)                   # real-world noise
    return max(0, round(delay))


def travel_minutes(features):
    return round(features["distance_km"] / AVG_SPEED_KMH * 60)


def training_frame(samples):
    return ([[s[f] for f in FEATURES] for s in samples],
            [s["delay_minutes"] for s in samples])


def _one_quantile_model(alpha):
    import xgboost as xgb  # heavy import, kept lazy
    return xgb.XGBRegressor(
        objective="reg:quantileerror", quantile_alpha=alpha,
        n_estimators=150, max_depth=4, learning_rate=0.1,
        tree_method="hist", random_state=0, n_jobs=1,
        deterministic_histogram=True,
    )


def train(samples):
    """Train the pessimistic / typical / optimistic trio."""
    X, y = training_frame(samples)
    models = {}
    for name, alpha in (("low", 0.1), ("mid", 0.5), ("high", 0.9)):
        model = _one_quantile_model(alpha)
        model.fit(X, y)
        models[name] = model
    return models


def save(models):
    for path, key in zip(MODEL_PATHS, ("low", "mid", "high")):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        models[key].save_model(path)


def model_exists():
    return all(os.path.exists(p) for p in MODEL_PATHS)


def load():
    import xgboost as xgb
    models = {}
    for path, key in zip(MODEL_PATHS, ("low", "mid", "high")):
        model = xgb.XGBRegressor()
        model.load_model(path)
        models[key] = model
    return models


def predict(models, features):
    """Return (low, mid, high) delay minutes; sorted for safety."""
    X = [[features[f] for f in FEATURES]]
    raw = [float(models[key].predict(X)[0]) for key in ("low", "mid", "high")]
    return tuple(max(0.0, v) for v in sorted(raw))


def predict_median(models, features):
    return predict(models, features)[1]


def mean_absolute_error(models, samples):
    if not samples:
        return None
    errors = [abs(predict_median(models, s) - s["delay_minutes"])
              for s in samples]
    return round(sum(errors) / len(errors), 1)


def interval_coverage(models, samples):
    """Share of samples whose true delay falls inside the low..high range."""
    if not samples:
        return None
    hits = 0
    for s in samples:
        low, _, high = predict(models, s)
        if low <= s["delay_minutes"] <= high:
            hits += 1
    return round(hits / len(samples), 3)


def explain(models, features, top_n=3, min_impact_minutes=0.5):
    """Plain-language 'why' from the median model's contribution values."""
    import xgboost as xgb
    X = [[features[f] for f in FEATURES]]
    booster = models["mid"].get_booster()
    contribs = booster.predict(xgb.DMatrix(X), pred_contribs=True)
    values = contribs[0].tolist()[:len(FEATURES)]
    ranked = sorted(zip(FEATURES, values), key=lambda p: -abs(p[1]))
    factors = []
    for name, impact in ranked:
        if len(factors) >= top_n or abs(impact) < min_impact_minutes:
            continue
        factors.append({
            "feature": name,
            "value": features[name],
            "impact_minutes": round(impact, 1),
            "text": FACTOR_TEXT[name](features[name]),
        })
    if factors:
        parts = "; ".join(
            f"{f['text']} ({'+' if f['impact_minutes'] >= 0 else ''}"
            f"{f['impact_minutes']:.0f} min)" for f in factors)
        summary = "Mostly because " + parts + "."
    else:
        summary = "No single factor stands out; the expected delay is small."
    return {"top_factors": factors, "summary": summary}
