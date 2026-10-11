"""Trust scores: how much a dispatcher should lean on an AI recommendation.

Pure functions with a transparent formula - every score ships with the
factors that produced it, so the number is explainable, not a black box.

ETA trust starts from a neutral 50 and is moved by measured reality:
+ up to 15 for accuracy (holdout error 15 min or less earns the full bonus),
+ up to 30 for honest ranges (interval coverage close to 1),
+ up to 5 for a tight uncertainty range,
- 5 if the model learned from little data,
- 20 if drift was detected on recent trips.
Planner trust starts from 70 and is moved by input quality:
- 25 when travel times are straight-line estimates (no road network loaded),
- 25 when no vehicle is eligible at all,
- 10 when more than half the shipments had to be skipped,
+ 5 when the plan covers a meaningful number of shipments.
"""
TRUST_DRIFT_PENALTY = 20
TRUST_TINY_DATA_PENALTY = 5
TRUST_MIN_SAMPLES = 40


def _clamp(score):
    return int(max(0, min(100, round(score))))


def eta_trust(mae_minutes, coverage, uncertainty_width=None,
              drift_detected=False, samples=None):
    """Score 0-100 for an ETA prediction, plus the factors behind it."""
    factors = {}
    if mae_minutes is None:
        return 0, {"reason": "model not trained"}
    score = 50.0
    accuracy_bonus = 15.0 * max(0.0, min(1.0, 1 - (mae_minutes - 15) / 45))
    score += accuracy_bonus
    factors["accuracy_bonus"] = round(accuracy_bonus, 1)
    coverage_bonus = 30.0 * (coverage or 0.0)
    score += coverage_bonus
    factors["coverage_bonus"] = round(coverage_bonus, 1)
    if uncertainty_width is not None:
        width_bonus = 5.0 if uncertainty_width <= 30 \
            else 2.0 if uncertainty_width <= 90 else -10.0
        score += width_bonus
        factors["range_width_adjustment"] = width_bonus
    if samples is not None and samples < TRUST_MIN_SAMPLES:
        score -= TRUST_TINY_DATA_PENALTY
        factors["small_data_penalty"] = -TRUST_TINY_DATA_PENALTY
    if drift_detected:
        score -= TRUST_DRIFT_PENALTY
        factors["drift_penalty"] = -TRUST_DRIFT_PENALTY
    return _clamp(score), factors


def planner_trust(shipments_considered, shipments_skipped, travel_model,
                  eligible_vehicles):
    """Score 0-100 for a capacity plan proposal, plus its factors."""
    factors = {}
    score = 70.0
    if travel_model == "offline-estimate":
        score -= 25.0
        factors["offline_travel_penalty"] = -25.0
    if eligible_vehicles == 0:
        score -= 25.0
        factors["no_vehicles_penalty"] = -25.0
    if shipments_considered:
        skip_ratio = shipments_skipped / shipments_considered
        if skip_ratio > 0.5:
            score -= 10.0
            factors["many_skips_penalty"] = -10.0
    if shipments_considered >= 10:
        score += 5.0
        factors["meaningful_load_bonus"] = 5.0
    return _clamp(score), factors
