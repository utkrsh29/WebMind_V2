from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query = Column(Text, nullable=False)
    status = Column(String(32), default="pending", nullable=False)
    forced_tier = Column(String(32), nullable=True)
    final_result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    profile = relationship("TaskProfileModel", back_populates="task", uselist=False, cascade="all, delete-orphan")
    runs = relationship("TaskRun", back_populates="task", order_by="TaskRun.created_at", cascade="all, delete-orphan")


class TaskProfileModel(Base):
    __tablename__ = "task_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    task_type = Column(String(64), nullable=False)
    constraints = Column(JSON, default=dict)
    target_sites = Column(JSON, default=list)
    requires_comparison = Column(Boolean, default=False)
    requires_decision = Column(Boolean, default=False)
    requires_extraction = Column(Boolean, default=False)
    requires_filtering = Column(Boolean, default=False)
    requires_form_fill = Column(Boolean, default=False)

    expected_steps = Column(String(32), default="low")
    num_sites = Column(Integer, default=1)
    num_constraints = Column(Integer, default=0)
    reasoning_requirement = Column(String(32), default="low")

    routed_tier = Column(String(32), nullable=False)
    routing_reason = Column(JSON, default=list)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    task = relationship("Task", back_populates="profile")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    tier = Column(String(32), nullable=False)
    status = Column(String(32), default="running", nullable=False)
    step_count = Column(Integer, default=0)
    
    # Token usage & costs
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    failure_type = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    escalation_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="runs")
    steps = relationship("Step", back_populates="run", order_by="Step.step_number", cascade="all, delete-orphan")


class Step(Base):
    __tablename__ = "steps"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String(36), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False)
    step_number = Column(Integer, nullable=False)
    tier = Column(String(32), nullable=False)

    thought = Column(Text, nullable=True)
    action = Column(JSON, nullable=False)
    expected_state = Column(JSON, nullable=True)
    actual_state = Column(JSON, nullable=True)
    screenshot_b64 = Column(Text, nullable=True)
    
    verification_status = Column(String(32), nullable=False)  # success | failure
    failure_type = Column(String(64), nullable=True)
    retry_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    run = relationship("TaskRun", back_populates="steps")


class ExperienceMemoryModel(Base):
    __tablename__ = "experience_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=False)  # Stored as JSON float array for universal DB compatibility
    embedding_model = Column(String(128), nullable=False, default="hash-v1", server_default="hash-v1")
    task_type = Column(String(64), nullable=False)
    tier = Column(String(32), nullable=False)
    profile_summary = Column(JSON, default=dict)
    action_summary = Column(Text, nullable=False)
    outcome = Column(String(32), nullable=False)  # success | failure | recovered
    failure_types = Column(JSON, default=list)
    schema_version = Column(Integer, nullable=False, default=1, server_default="1")
    domain = Column(String(253), nullable=True, index=True)
    page_url_pattern = Column(String(500), nullable=False, default="", server_default="")
    page_context = Column(JSON, nullable=False, default=dict)
    action_attempted = Column(JSON, nullable=False, default=dict)
    failure = Column(JSON, nullable=True)
    successful_alternative = Column(JSON, nullable=True)
    verified_workflow = Column(JSON, nullable=False, default=list)
    reliability_score = Column(Float, nullable=False, default=0.5, server_default="0.5")
    success_count = Column(Integer, nullable=False, default=0, server_default="0")
    failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    retrieval_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    dedupe_key = Column(String(64), nullable=True, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class MemoryAccessModel(Base):
    __tablename__ = "memory_access_events"
    __table_args__ = (
        UniqueConstraint("task_id", "step_number", "memory_id", name="uq_memory_access_task_step"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    memory_id = Column(String(36), ForeignKey("experience_memories.id", ondelete="CASCADE"), nullable=False, index=True)
    decision = Column(String(16), nullable=False)
    scores = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=False, default="")
    included_in_planner = Column(Boolean, nullable=False, default=False)
    planner_selected = Column(Boolean, nullable=False, default=False)
    feedback_applied = Column(Boolean, nullable=False, default=False)
    attributed_outcome = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class MemoryEvidenceModel(Base):
    __tablename__ = "memory_evidence_events"
    __table_args__ = (
        UniqueConstraint("memory_id", "evidence_key", name="uq_memory_evidence_key"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String(36), ForeignKey("experience_memories.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(36), nullable=False, index=True)
    evidence_key = Column(String(200), nullable=False)
    outcome = Column(String(16), nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class MetricRecord(Base):
    __tablename__ = "metric_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    metric_name = Column(String(64), nullable=False)
    metric_value = Column(Float, nullable=False)
    metadata_json = Column(JSON, default=dict)
