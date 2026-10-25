from typing import Any, Dict
from app.router.task_profile import NormalizedTaskData


def extract_features(query: str, normalized: NormalizedTaskData) -> Dict[str, Any]:
    """Pure Python deterministic feature extraction from normalized task data."""
    # 1. Count constraints
    num_constraints = len(normalized.constraints)

    # 2. Compute number of target sites
    valid_sites = [s for s in normalized.target_sites if s.lower() != "unspecified"]
    if len(valid_sites) > 0:
        num_sites = len(valid_sites)
    elif normalized.requires_comparison or normalized.task_type == "multi_site":
        num_sites = 2
    else:
        num_sites = 1

    # 3. Compute expected steps
    if num_sites > 1 or normalized.requires_comparison:
        expected_steps = "high"
    elif normalized.requires_form_fill or normalized.requires_filtering or num_constraints >= 2:
        expected_steps = "medium"
    else:
        expected_steps = "low"

    # 4. Compute reasoning requirement
    if (
        normalized.requires_comparison
        or normalized.requires_decision
        or num_constraints >= 3
        or normalized.task_type in ("comparison", "multi_site")
    ):
        reasoning_requirement = "high"
    elif normalized.requires_filtering or normalized.requires_form_fill or num_constraints >= 1:
        reasoning_requirement = "medium"
    else:
        reasoning_requirement = "low"

    return {
        "num_constraints": num_constraints,
        "num_sites": num_sites,
        "expected_steps": expected_steps,
        "reasoning_requirement": reasoning_requirement,
    }
