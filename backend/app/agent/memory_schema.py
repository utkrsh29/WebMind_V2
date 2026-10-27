from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class MemoryPageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_title: str = ""
    relevant_elements: List[str] = Field(default_factory=list)
    available_controls: List[str] = Field(default_factory=list)
    page_state_summary: str = ""


class MemoryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1, max_length=40)
    target_description: str = Field(default="", max_length=300)
    selector_strategy: str = Field(default="", max_length=120)
    action_parameters: Dict[str, Any] = Field(default_factory=dict)


class MemoryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred: bool = True
    failure_type: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=1000)
    verification_evidence: str = Field(default="", max_length=1000)


class MemoryAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Optional[str] = Field(default=None, max_length=40)
    target_description: str = Field(default="", max_length=300)
    selector_strategy: str = Field(default="", max_length=120)
    recovery_steps: List[str] = Field(default_factory=list)


class ExperienceMemoryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal[1] = 1
    task_description: str = Field(min_length=1, max_length=2000)
    task_type: str = Field(default="other", max_length=64)
    tier: str = Field(default="GENERAL", max_length=32)
    website_domain: Optional[str] = Field(default=None, max_length=253)
    page_url_pattern: str = Field(default="", max_length=500)
    page_context: MemoryPageContext = Field(default_factory=MemoryPageContext)
    action_attempted: MemoryAction
    failure: Optional[MemoryFailure] = None
    successful_alternative: Optional[MemoryAlternative] = None
    verified_workflow: List[MemoryAction] = Field(default_factory=list)
    outcome: Literal["success", "failure", "recovered"]
    reliability_score: float = Field(default=0.5, ge=0.0, le=1.0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    retrieval_count: int = Field(default=0, ge=0)
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    decision: Literal["USE", "CAUTIOUS", "IGNORE"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    task_similarity: float = Field(ge=0.0, le=1.0)
    task_type_match: bool = True
    website_match: bool
    page_similarity: float = Field(ge=0.0, le=1.0)
    failure_match: float = Field(default=0.5, ge=0.0, le=1.0)
    reliability_score: float = Field(ge=0.0, le=1.0)
    recency_score: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(max_length=500)


class MemoryRetrievalContext(BaseModel):
    task_description: str
    task_type: str = "other"
    website_domain: Optional[str] = None
    page_url_pattern: str = ""
    page_context: MemoryPageContext = Field(default_factory=MemoryPageContext)
    intended_action: Optional[str] = None
    failure_type: Optional[str] = None


class MemoryRetrievalEventData(BaseModel):
    memory_id: str
    decision: Literal["USE", "CAUTIOUS", "IGNORE"]
    relevance_score: float
    task_similarity: float
    task_type_match: bool = True
    website_match: bool
    page_similarity: float
    failure_match: float = 0.5
    reliability_score: float
    recency_score: float = 0.5
    reason: str
