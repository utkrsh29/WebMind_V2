import pytest
from app.router.feature_extractor import extract_features
from app.router.routing_policy import evaluate_routing_policy, route_task
from app.router.task_normalizer import fallback_rule_based_normalizer
from app.router.task_profile import NormalizedTaskData


def test_router_determinism():
    """Verifies that the same query consistently routes to the exact same tier and reason."""
    query = "Find laptops under 70k with 16GB RAM and 512GB SSD"
    
    # Run 5 times in a row
    decisions = []
    for _ in range(5):
        norm = fallback_rule_based_normalizer(query)
        feats = extract_features(query, norm)
        profile_dict = {
            "task_type": norm.task_type,
            "constraints": norm.constraints,
            "target_sites": norm.target_sites,
            "requires_comparison": norm.requires_comparison,
            "requires_decision": norm.requires_decision,
            "requires_extraction": norm.requires_extraction,
            "requires_filtering": norm.requires_filtering,
            "requires_form_fill": norm.requires_form_fill,
            **feats,
        }
        tier, reason = evaluate_routing_policy(profile_dict)
        decisions.append((tier, tuple(reason)))

    # All decisions must be identical
    assert len(set(decisions)) == 1
    assert decisions[0][0] in ("GENERAL", "REASONING")


def test_boundary_cases_routing():
    """Tests zero, moderate, and high constraint boundaries."""
    # 1. Zero constraint simple search -> FAST
    norm_fast = NormalizedTaskData(
        task_type="search",
        constraints={},
        target_sites=["unspecified"],
        requires_comparison=False,
    )
    feats_fast = extract_features("search python", norm_fast)
    tier_fast, _ = evaluate_routing_policy({**norm_fast.model_dump(), **feats_fast})
    assert tier_fast == "FAST"

    # 2. Moderate filtering -> GENERAL
    norm_gen = NormalizedTaskData(
        task_type="filtering",
        constraints={"budget": "under 5000"},
        target_sites=["unspecified"],
        requires_filtering=True,
    )
    feats_gen = extract_features("find shoes under 5000", norm_gen)
    tier_gen, _ = evaluate_routing_policy({**norm_gen.model_dump(), **feats_gen})
    assert tier_gen == "GENERAL"

    # 3. Multi-site / Comparison -> REASONING
    norm_re = NormalizedTaskData(
        task_type="comparison",
        constraints={"item": "iphone"},
        target_sites=["amazon", "flipkart"],
        requires_comparison=True,
    )
    feats_re = extract_features("compare iphone on amazon and flipkart", norm_re)
    tier_re, _ = evaluate_routing_policy({**norm_re.model_dump(), **feats_re})
    assert tier_re == "REASONING"


def test_configurable_threshold_change():
    """Tests dynamic policy adjustment via modified policy thresholds."""
    # Profile with 2 constraints
    norm = NormalizedTaskData(
        task_type="filtering",
        constraints={"budget": "70k", "ram": "16gb"},
        target_sites=["unspecified"],
        requires_filtering=True,
    )
    feats = extract_features("find laptop", norm)
    profile_dict = {**norm.model_dump(), **feats}

    # Default policy (min_constraints = 3 for REASONING) -> GENERAL
    tier_default, _ = evaluate_routing_policy(profile_dict)
    assert tier_default == "GENERAL"

    # Lower threshold policy (min_constraints = 2 for REASONING) -> REASONING
    custom_policy = {
        "thresholds": {
            "reasoning": {
                "min_constraints": 2,
                "require_comparison": True,
                "require_multi_site": True,
                "task_types": ["comparison"],
            },
            "general": {
                "min_constraints": 1,
            },
        }
    }
    tier_modified, reasons = evaluate_routing_policy(profile_dict, policy_dict=custom_policy)
    assert tier_modified == "REASONING"
    assert any("High constraint density" in r for r in reasons)
