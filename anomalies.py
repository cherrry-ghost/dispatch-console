"""Anomaly detection: flags trips where reality does not match expectations.

Pure functions - nothing here reads the database or writes anything. The
endpoint in main.py gathers the data, runs scan() and records the result.

Three checks:
- delay_outlier: the trip took far longer than the delay model (or, without
  a model, the fleet's statistical band) says it should;
- impossible_speed: the recorded wall-clock time is shorter than half the
  physical minimum at legal speed, which smells like falsified tracking;
- contradictory_record: delivered before it was created.
"""
from datetime import timedelta

from delay_model import travel_minutes

DELAY_MEDIUM_MIN = 90   # minutes above expectation -> medium severity
DELAY_HIGH_MIN = 180    # minutes above expectation -> high severity
IMPOSSIBLE_RATIO = 0.5  # recorded time must reach this share of the minimum
IMPOSSIBLE_SLACK_MIN = 10
BAND_SIGMAS = 2.5       # statistical fallback: mean + this many sigmas
MIN_SAMPLES_FOR_BAND = 8

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _minutes(delta):
    return delta.total_seconds() / 60.0


def _baseline_band(samples):
    delays = [s["delay_minutes"] for s in samples]
    n = len(delays)
    mean = sum(delays) / n
    variance = sum((d - mean) ** 2 for d in delays) / n
    return mean, variance ** 0.5


def scan(shipments, features_by_id, predict_fn=None):
    """Run every check over completed shipments.

    shipments: completed shipment rows (created_at, delivered_at,
    delay_minutes, ...).
    features_by_id: {shipment_id: feature dict or None}; None means the road
    network changed and no route exists any more - travel checks are skipped.
    predict_fn: optional feature dict -> model-expected delay minutes.
    Returns a list of findings sorted by severity.
    """
    findings = []
    use_band = predict_fn is None and len(shipments) >= MIN_SAMPLES_FOR_BAND
    band = _baseline_band(shipments) if use_band else None

    for s in shipments:
        sid = s["id"]
        features = features_by_id.get(sid)
        if s["delivered_at"] is not None and s["created_at"] is not None \
                and s["delivered_at"] <= s["created_at"]:
            findings.append({
                "shipment_id": sid, "kind": "contradictory_record",
                "severity": "high",
                "detail": "recorded as delivered before it was created",
                "expected_minutes": None, "actual_minutes": None,
                "detected_by": "rule",
            })

        if features is None or s["delivered_at"] is None \
                or s["created_at"] is None:
            continue

        physical_min = travel_minutes(features)
        actual = _minutes(s["delivered_at"] - s["created_at"])
        if actual + IMPOSSIBLE_SLACK_MIN < physical_min * IMPOSSIBLE_RATIO:
            findings.append({
                "shipment_id": sid, "kind": "impossible_speed",
                "severity": "high",
                "detail": (f"finished {actual:.0f} min after creation, but "
                           f"the route needs at least {physical_min} min "
                           f"at legal speed"),
                "expected_minutes": physical_min,
                "actual_minutes": round(actual, 1),
                "detected_by": "rule",
            })

        if s["delay_minutes"] is None:
            continue
        if predict_fn is not None:
            expected = predict_fn(features)
            residual = s["delay_minutes"] - expected
            detected_by = "model"
            severity = ("high" if residual >= DELAY_HIGH_MIN
                        else "medium" if residual >= DELAY_MEDIUM_MIN
                        else None)
        elif band is not None:
            mean, sigma = band
            expected = round(mean, 1)
            residual = s["delay_minutes"] - mean
            detected_by = "statistics"
            if sigma <= 0:
                severity = None
            else:
                severity = ("high" if residual >= 4 * sigma
                            else "medium" if residual >= BAND_SIGMAS * sigma
                            else None)
        else:
            continue
        if severity:
            findings.append({
                "shipment_id": sid, "kind": "delay_outlier",
                "severity": severity,
                "detail": (f"delay {s['delay_minutes']} min vs expected "
                           f"about {expected:.0f} min"),
                "expected_minutes": round(expected, 1),
                "actual_minutes": s["delay_minutes"],
                "detected_by": detected_by,
            })

    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]],
                                 f["shipment_id"], f["kind"]))
    return findings
