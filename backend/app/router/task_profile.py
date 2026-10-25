from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.schemas import AgentTier, TaskType


class NormalizedTaskData(BaseModel):
    task_type: TaskType = "other"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    target_sites: List[str] = Field(default_factory=lambda: ["unspecified"])
    requires_comparison: bool = False
    requires_decision: bool = False
    requires_extraction: bool = False
    requires_filtering: bool = False
    requires_form_fill: bool = False


class TaskProfile(BaseModel):
    raw_query: str
    task_type: TaskType = "other"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    target_sites: List[str] = Field(default_factory=list)
    requires_comparison: bool = False
    requires_decision: bool = False
    requires_extraction: bool = False
    requires_filtering: bool = False
    requires_form_fill: bool = False

    expected_steps: Literal["low", "medium", "high"] = "low"
    num_sites: int = 1
    num_constraints: int = 0
    reasoning_requirement: Literal["low", "medium", "high"] = "low"

    routed_tier: AgentTier = "FAST"
    routing_reason: List[str] = Field(default_factory=list)
