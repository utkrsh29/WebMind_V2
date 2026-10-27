from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator

# Core Tiers
AgentTier = Literal["FAST", "GENERAL", "REASONING"]

# Task Normalization Types
TaskType = Literal[
    "search",
    "navigation",
    "extraction",
    "form_fill",
    "filtering",
    "comparison",
    "multi_site",
    "other",
]

# Action Types
ActionName = Literal[
    "goto",
    "click",
    "type",
    "select",
    "press",
    "scroll",
    "scroll_to",
    "hover",
    "go_back",
    "wait",
    "extract",
    "screenshot",
    "new_tab",
    "switch_tab",
    "finish",
    "ask_user",
]

# Failure Types
FailureType = Literal[
    "element_not_found",
    "no_state_change",
    "wrong_page",
    "timeout",
    "blocked",
    "login_required",
    "reasoning_error",
    "loop_detected",
    "quota_exceeded",
]


class TaskCreateRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Natural language task prompt")
    forced_tier: Optional[AgentTier] = Field(
        None, description="Optional manual override for evaluation"
    )


class TaskConfirmRequest(BaseModel):
    approved: bool = Field(True, description="Whether the user approved the action")
    user_input: Optional[str] = Field(None, description="Optional response or text from user")


class HumanBrowserActionRequest(BaseModel):
    revision: int = Field(..., ge=1)
    action: Literal["click", "type", "select", "press", "scroll"]
    element_id: Optional[int] = Field(None, ge=1)
    text: Optional[str] = Field(None, max_length=2000)
    value: Optional[str] = Field(None, max_length=500)
    key: Optional[str] = Field(None, max_length=30)
    direction: Optional[Literal["up", "down"]] = "down"
    amount: Optional[int] = Field(default=600, ge=100, le=2000)

    @model_validator(mode="after")
    def require_action_fields(self):
        if self.action in ("click", "type", "select") and self.element_id is None:
            raise ValueError(f"{self.action} requires an observed element_id")
        if self.action == "type" and self.text is None:
            raise ValueError("type requires text")
        if self.action == "select" and self.value is None:
            raise ValueError("select requires a value from the observed options")
        if self.action == "press" and not self.key:
            raise ValueError("press requires a key")
        return self


class ActionSchema(BaseModel):
    action: ActionName
    url: Optional[str] = None
    element_id: Optional[int] = None
    text: Optional[str] = None
    value: Optional[str] = None
    submit: Optional[bool] = False
    key: Optional[str] = None
    direction: Optional[Literal["up", "down"]] = "down"
    amount: Optional[int] = None        # scroll pixels
    ms: Optional[int] = 1000
    tab_index: Optional[int] = None
    question: Optional[str] = None
    is_login: Optional[bool] = False    # ask_user: indicates login scenario
    schema_or_description: Optional[str] = None
    result: Optional[Any] = None

    @model_validator(mode="before")
    @classmethod
    def accept_action_type_alias(cls, value):
        """Normalize LLMs that emit `type` instead of the required `action` key."""
        if isinstance(value, dict) and "action" not in value and "type" in value:
            value = dict(value)
            value["action"] = value.pop("type")
        return value


class ExpectedStateSchema(BaseModel):
    url_contains: Optional[str] = None
    element_present: Optional[str] = None  # CSS selector or text description
    text_present: Optional[str] = None
    input_value_equals: Optional[Dict[str, str]] = None  # {selector_or_id: expected_val}


class PlannerOutputSchema(BaseModel):
    thought: str = Field(..., description="Internal chain of thought and observation critique")
    next_action: ActionSchema = Field(..., description="Action to execute in the browser")
    expected_state: Optional[ExpectedStateSchema] = Field(
        None, description="Verifiable expected state after action"
    )
    memory_ids_used: List[str] = Field(
        default_factory=list,
        description="IDs of supplied applicable memories that materially informed this action; otherwise empty",
    )


class TaskProfileSchema(BaseModel):
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


class StepEventSchema(BaseModel):
    step_number: int
    tier: AgentTier
    thought: Optional[str] = None
    action: Dict[str, Any]
    expected_state: Optional[Dict[str, Any]] = None
    actual_state: Optional[Dict[str, Any]] = None
    screenshot_b64: Optional[str] = None
    verification_status: Literal["success", "failure"]
    failure_type: Optional[str] = None
    retry_count: int = 0


class TaskStatusResponse(BaseModel):
    id: str
    query: str
    status: str
    forced_tier: Optional[str] = None
    final_result: Optional[Any] = None
    profile: Optional[TaskProfileSchema] = None
    step_count: int = 0
    current_tier: Optional[str] = None
    escalation_count: int = 0
    created_at: datetime
    updated_at: datetime


class MetricSummaryResponse(BaseModel):
    total_tasks: int
    success_rate: float
    avg_latency_seconds: float
    total_tokens_used: int
    escalation_rate: float
    recovery_rate: float
    action_efficiency: float
    tier_distribution: Dict[str, int]
    eval_benchmark_results: Optional[List[Dict[str, Any]]] = None
