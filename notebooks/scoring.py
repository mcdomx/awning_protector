"""Scoring functions for awning AI prompt evaluation.

Objectives (weighted, user-adjustable):
  protection       - retract when wind/rain is dangerous
  solar_shielding  - deploy when hot/sunny/safe
  token_efficiency - lower token usage is better
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

# Update after the first full run to reflect observed token usage.
MAX_TOKENS_REFERENCE: int = 6000


@dataclass
class ScenarioScore:
    scenario_name: str
    prompt_variant: str
    expected_action: str
    actual_action: str
    correct: bool
    objectives: List[str]
    protection_score: Optional[float]  # 1.0/0.0 or None if not a protection scenario
    solar_score: Optional[float]       # 1.0/0.0 or None if not a solar scenario
    token_score: float                 # always set; higher = fewer tokens used
    composite: float                   # weighted average of applicable scores


def score_results(
    results: list,
    scenarios_by_name: Dict[str, dict],
    weights: Dict[str, float],
    max_tokens: int = MAX_TOKENS_REFERENCE,
) -> List[ScenarioScore]:
    """Score a list of RunResult objects.

    Args:
        results: list of RunResult dataclasses from sandbox_runner.run_scenario()
        scenarios_by_name: {name: scenario_dict} mapping
        weights: {"protection": float, "solar_shielding": float, "token_efficiency": float}
        max_tokens: reference ceiling for token normalization
    """
    scores: List[ScenarioScore] = []

    for r in results:
        sc = scenarios_by_name[r.scenario_name]
        objectives = sc["objectives"]
        expected = sc["expected_action"]
        correct = r.action_taken == expected

        protection_score: Optional[float] = None
        solar_score: Optional[float] = None

        if "protection" in objectives:
            protection_score = 1.0 if correct else 0.0

        if "solar_shielding" in objectives:
            solar_score = 1.0 if correct else 0.0

        token_score = 1.0 - min(r.total_tokens / max(max_tokens, 1), 1.0)

        # Composite: weighted average using only applicable objectives
        weight_sum = 0.0
        weighted_total = 0.0

        if protection_score is not None:
            w = weights.get("protection", 0.0)
            weighted_total += protection_score * w
            weight_sum += w

        if solar_score is not None:
            w = weights.get("solar_shielding", 0.0)
            weighted_total += solar_score * w
            weight_sum += w

        # Token efficiency always contributes
        w = weights.get("token_efficiency", 0.0)
        weighted_total += token_score * w
        weight_sum += w

        composite = weighted_total / weight_sum if weight_sum > 0 else 0.0

        scores.append(ScenarioScore(
            scenario_name=r.scenario_name,
            prompt_variant=r.prompt_variant,
            expected_action=expected,
            actual_action=r.action_taken,
            correct=correct,
            objectives=objectives,
            protection_score=protection_score,
            solar_score=solar_score,
            token_score=round(token_score, 4),
            composite=round(composite, 4),
        ))

    return scores


def summary_by_variant(scores: List[ScenarioScore]) -> Dict[str, Dict[str, float]]:
    """Compute mean scores per prompt variant.

    Returns:
        {variant_name: {"composite": float, "protection": float, "solar": float,
                        "token": float, "accuracy": float}}
    """
    from collections import defaultdict

    buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {
        "composite": [], "protection": [], "solar": [], "token": [], "correct": []
    })

    for s in scores:
        b = buckets[s.prompt_variant]
        b["composite"].append(s.composite)
        b["token"].append(s.token_score)
        b["correct"].append(1.0 if s.correct else 0.0)
        if s.protection_score is not None:
            b["protection"].append(s.protection_score)
        if s.solar_score is not None:
            b["solar"].append(s.solar_score)

    result = {}
    for variant, b in buckets.items():
        result[variant] = {
            "composite": round(sum(b["composite"]) / len(b["composite"]), 4),
            "accuracy": round(sum(b["correct"]) / len(b["correct"]), 4),
            "protection": round(sum(b["protection"]) / len(b["protection"]), 4) if b["protection"] else None,
            "solar": round(sum(b["solar"]) / len(b["solar"]), 4) if b["solar"] else None,
            "token_efficiency": round(sum(b["token"]) / len(b["token"]), 4),
        }
    return result
