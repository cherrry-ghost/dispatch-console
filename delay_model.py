"""XGBoost delay model: proposes ETAs, never decides.

Deterministic by construction: fixed seed and a single worker thread, so the
same training data always produces the same model. The synthetic ground truth
planted into history by the data generator lives here too, so the model and
the data share one definition of reality.
"""
import os

AVG_SPEED_KMH = 40.0
MODEL_VERSION = 1
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "delay_model.json")

FEATURES = ["distance_km", "max_risk", "avg_risk", "is_hazardous",
            "weight_kg", "legs"]

MIN_HISTORY_FOR_TRAINING = 20


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


def train(samples):
    import xgboost as xgb  # heavy import, kept lazy
    X, y = training_frame(samples)
    model = xgb.XGBRegressor(
        n_estimators=150, max_depth=4, learning_rate=0.1,
        random_state=0, n_jobs=1, deterministic_histogram=True,
    )
    model.fit(X, y)
    return model


def save(model, path=MODEL_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    model.save_model(path)


def model_exists(path=MODEL_PATH):
    return os.path.exists(path)


def load(path=MODEL_PATH):
    import xgboost as xgb
    model = xgb.XGBRegressor()
    model.load_model(path)
    return model


def predict(model, features):
    X = [[features[f] for f in FEATURES]]
    return max(0.0, float(model.predict(X)[0]))


def mean_absolute_error(model, samples):
    if not samples:
        return None
    errors = [abs(predict(model, s) - s["delay_minutes"]) for s in samples]
    return round(sum(errors) / len(errors), 1)
