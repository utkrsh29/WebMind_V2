from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml
from app.router.feature_extractor import extract_features
from app.router.task_normalizer import normalize_task
from app.router.task_profile import NormalizedTaskData, TaskProfile
from app.schemas import AgentTier

POLICY_FILE_PATH = Path(__file__).parent / "policy.yaml"


def load_policy(policy_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads routing policy thresholds from YAML file."""
    path = policy_path or POLICY_FILE_PATH
    if not path.exists():
        # Fallback default policy if file missing
        return {
            "thresholds": {
                "reasoning": {
                    "min_constraints": 3,
                    "require_comparison": True,
                    "require_multi_site": True,
                    "require_decision": True,
                    "task_types": ["comparison", "multi_site"],
                    "reasoning_requirement": "high",
                },
                "general": {
                    "min_constraints": 1,
                    "require_filtering": True,
                    "require_form_fill": True,
                    "require_extraction": True,
                    "task_types": ["form_fill", "filtering", "extraction", "navigation"],
                    "expected_steps": ["medium"],
                },
                "fast": {"fallback_tier": "FAST"},
            }
        }
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def evaluate_routing_policy(
    profile_data: Dict[str, Any],
    policy_dict: Optional[Dict[str, Any]] = None,
) -> Tuple[AgentTier, List[str]]:
    """Evaluates deterministic rule hierarchy and produces tier + reason explanations."""
    policy = policy_dict or load_policy()
    thresholds = policy.get("thresholds", {})
    re_cfg = thresholds.get("reasoning", {})
    gen_cfg = thresholds.get("general", {})

    reasons: List[str] = []

    # 1. Evaluate REASONING rules
    if re_cfg.get("require_comparison") and profile_data.get("requires_comparison"):
        reasons.append("Task requires multi-entity or cross-product comparison")

    if re_cfg.get("require_decision") and profile_data.get("requires_decision"):
        reasons.append("Task involves complex decision-making and trade-off evaluation")

    if profile_data.get("task_type") in re_cfg.get("task_types", []):
        reasons.append(f"Task type '{profile_data.get('task_type')}' requires deep reasoning")

    if re_cfg.get("require_multi_site") and profile_data.get("num_sites", 1) > 1:
        reasons.append(f"Multi-site orchestration across {profile_data.get('num_sites')} target sites")

    min_re_constraints = re_cfg.get("min_constraints", 3)
    if profile_data.get("num_constraints", 0) >= min_re_constraints:
        reasons.append(f"High constraint density ({profile_data.get('num_constraints')} >= {min_re_constraints})")

    if profile_data.get("reasoning_requirement") == re_cfg.get("reasoning_requirement", "high"):
        reasons.append("Feature extractor computed reasoning requirement as 'high'")

    if reasons:
        return "REASONING", reasons

    # 2. Evaluate GENERAL rules
    if gen_cfg.get("require_form_fill") and profile_data.get("requires_form_fill"):
        reasons.append("Task involves form filling or multi-field input submission")

    if gen_cfg.get("require_filtering") and profile_data.get("requires_filtering"):
        reasons.append("Task specifies parameter constraints or search filtering")

    if gen_cfg.get("require_extraction") and profile_data.get("requires_extraction"):
        reasons.append("Task requires structured data extraction")

    if profile_data.get("task_type") in gen_cfg.get("task_types", []):
        reasons.append(f"Task type '{profile_data.get('task_type')}' routed to GENERAL")

    min_gen_constraints = gen_cfg.get("min_constraints", 1)
    if profile_data.get("num_constraints", 0) >= min_gen_constraints:
        reasons.append(f"Moderate constraint count ({profile_data.get('num_constraints')} >= {min_gen_constraints})")

    if profile_data.get("expected_steps") in gen_cfg.get("expected_steps", ["medium"]):
        reasons.append(f"Expected step complexity is '{profile_data.get('expected_steps')}'")

    if reasons:
        return "GENERAL", reasons

    # 3. Fallback to FAST
    return "FAST", ["Simple navigation or direct search task without heavy constraints"]


async def route_task(
    query: str,
    forced_tier: Optional[AgentTier] = None,
    policy_dict: Optional[Dict[str, Any]] = None,
) -> TaskProfile:
    """Full router pipeline: Normalization -> Feature Extraction -> Policy Evaluation -> Profile."""
    normalized = await normalize_task(query)
    features = extract_features(query, normalized)

    profile_dict = {
        "raw_query": query,
        "task_type": normalized.task_type,
        "constraints": normalized.constraints,
        "target_sites": normalized.target_sites,
        "requires_comparison": normalized.requires_comparison,
        "requires_decision": normalized.requires_decision,
        "requires_extraction": normalized.requires_extraction,
        "requires_filtering": normalized.requires_filtering,
        "requires_form_fill": normalized.requires_form_fill,
        **features,
    }

    if forced_tier:
        tier = forced_tier
        reasons = [f"Manual override selected tier '{forced_tier}'"]
    else:
        tier, reasons = evaluate_routing_policy(profile_dict, policy_dict)

    profile = TaskProfile(
        routed_tier=tier,
        routing_reason=reasons,
        **profile_dict,
    )
    return profile
