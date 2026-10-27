"""Persistent, evidence-backed experience memory for browser actions.

Structured records and evidence remain in WebMind's SQL database, while ChromaDB
provides persistent vector retrieval. Retrieved experiences are advisory; page
evidence and the verifier remain the source of truth for every action.
"""

import asyncio
import hashlib
import logging
import math
import re
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple
from urllib.parse import urlparse

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory_schema import (
    ExperienceMemoryData,
    MemoryAction,
    MemoryAlternative,
    MemoryFailure,
    MemoryGateDecision,
    MemoryPageContext,
    MemoryRetrievalContext,
)
from app.config import settings
from app.models import ExperienceMemoryModel, MemoryAccessModel, MemoryEvidenceModel

logger = logging.getLogger(__name__)

_EMBEDDER: Any = None
_EMBEDDER_LOCK = None

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+|token[=: ]+\S+)\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
REUSABLE_MEMORY_ACTIONS = frozenset({"click", "type", "select", "goto", "go_back", "new_tab", "switch_tab"})


def is_reusable_memory_action(action_type: Optional[str]) -> bool:
    """Whether an action represents a task-progressing browser interaction."""
    return action_type in REUSABLE_MEMORY_ACTIONS


def redact_sensitive_text(value: Any, max_length: int = 1000) -> str:
    """Redact common secrets and personal contact values before persistence."""
    text = str(value or "")
    text = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _CARD_RE.sub("[REDACTED_NUMBER]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text[:max_length]


def normalized_domain(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    host = urlparse(value if "://" in value else f"https://{value}").hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def url_pattern(url: str) -> str:
    """Drop query/fragment and replace numeric path segments with placeholders."""
    path = urlparse(url).path or "/"
    path = re.sub(r"(?<![a-z])\d{3,}(?![a-z])", "{id}", path, flags=re.I)
    return path[:500]


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) > 1}


def lexical_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_attributable_memory_failure(
    failure_type: Optional[str],
    reason: str,
    action_matches_memory: bool,
) -> bool:
    """Only penalize a suggestion when its matching action failed for a local reason."""
    if not action_matches_memory:
        return False
    if failure_type not in {"element_not_found", "no_state_change", "loop_detected", "timeout", "wrong_page"}:
        return False
    lowered = (reason or "").lower()
    unrelated_signals = (
        "rate limit", "rate_limit", "429", "quota", "connection refused",
        "network error", "dns", "api key", "connection reset", "name resolution",
    )
    return not any(signal in lowered for signal in unrelated_signals)


def get_embedder():
    """Lazily load the configured local model; failure means lexical fallback."""
    global _EMBEDDER, _EMBEDDER_LOCK
    if _EMBEDDER is not None:
        return _EMBEDDER
    if _EMBEDDER_LOCK is None:
        import threading
        _EMBEDDER_LOCK = threading.Lock()
    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None:
            return _EMBEDDER
        try:
            from sentence_transformers import SentenceTransformer
            _EMBEDDER = SentenceTransformer(settings.MEMORY_EMBEDDING_MODEL)
            logger.info("Loaded local memory embedding model %s", settings.MEMORY_EMBEDDING_MODEL)
        except Exception as exc:
            _EMBEDDER = "fallback"
            logger.warning("Local memory embeddings unavailable; using deterministic fallback: %s", exc)
    return _EMBEDDER


def _fallback_embedding(text: str, dimensions: int = 384) -> List[float]:
    vector = [0.0] * dimensions
    for token in _tokens(text):
        hashed = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        vector[hashed % dimensions] += 1.0 if (hashed >> 8) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def compute_embedding_with_model(text: str) -> Tuple[List[float], str]:
    embedder = get_embedder()
    if embedder != "fallback":
        try:
            values = embedder.encode(text, normalize_embeddings=True)
            return values.tolist(), f"sentence-transformers:{settings.MEMORY_EMBEDDING_MODEL}"
        except Exception as exc:
            logger.warning("Local embedding failed; using deterministic fallback: %s", exc)
    return _fallback_embedding(text), "hash-v1"


def compute_embedding(text: str) -> List[float]:
    """Compatibility helper used by existing code and tests."""
    return compute_embedding_with_model(text)[0]


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    if len(v1) != len(v2) or not v1:
        return 0.0
    a = np.asarray(v1, dtype=np.float32)
    b = np.asarray(v2, dtype=np.float32)
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(max(0.0, min(1.0, np.dot(a, b) / (norm_a * norm_b))))


def _context_text(context: MemoryRetrievalContext) -> str:
    page = context.page_context
    return " ".join([
        context.task_description,
        context.task_type,
        context.website_domain or "",
        context.page_url_pattern,
        page.page_title,
        " ".join(page.relevant_elements),
        " ".join(page.available_controls),
        page.page_state_summary,
        context.intended_action or "",
        context.failure_type or "",
    ])


def _stored_text(record: ExperienceMemoryModel) -> str:
    page = record.page_context or {}
    return " ".join([
        record.query or "",
        record.task_type or "",
        record.domain or "",
        record.page_url_pattern or "",
        page.get("page_title", ""),
        " ".join(page.get("relevant_elements", []) or []),
        " ".join(page.get("available_controls", []) or []),
        page.get("page_state_summary", ""),
        record.action_summary or "",
        " ".join(f"{step.get('action_type')} {step.get('target_description')}" for step in (record.verified_workflow or [])),
    ])


@lru_cache(maxsize=1)
def _chroma_client():
    """Return one shared persistent or server-backed Chroma client."""
    import chromadb

    if settings.CHROMA_HOST:
        return chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)
    return chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIRECTORY)


def _chroma_collection(embedding_model: str):
    """Keep different embedding spaces in separate cosine-indexed collections."""
    model_suffix = hashlib.sha256(embedding_model.encode("utf-8")).hexdigest()[:12]
    return _chroma_client().get_or_create_collection(
        name=f"webmind_memory_{model_suffix}",
        metadata={"hnsw:space": "cosine", "embedding_model": embedding_model},
        embedding_function=None,
    )


def _page_similarity(current: MemoryPageContext, stored: Dict[str, Any], current_path: str, stored_path: str) -> float:
    current_text = " ".join([
        current.page_title,
        *current.relevant_elements,
        *current.available_controls,
    ])
    stored_text = " ".join([
        stored.get("page_title", ""),
        " ".join(stored.get("relevant_elements", []) or []),
        " ".join(stored.get("available_controls", []) or []),
    ])
    semantic = lexical_similarity(current_text, stored_text)
    path = 1.0 if current_path and current_path == stored_path else lexical_similarity(current_path, stored_path) * 0.5
    return min(1.0, 0.8 * semantic + 0.2 * path)


def _reliability(successes: int, failures: int) -> float:
    alpha, beta = settings.MEMORY_BETA_ALPHA, settings.MEMORY_BETA_BETA
    return (successes + alpha) / (successes + failures + alpha + beta)


def _recency_score(updated_at: Optional[datetime]) -> float:
    """Linear decay from 1.0 to 0.6 over one year; unknown dates score 0.5."""
    if updated_at is None:
        return 0.5
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 86400)
    return round(1.0 - 0.4 * min(age_days / 365.0, 1.0), 4)


def evaluate_applicability(
    memory: ExperienceMemoryModel,
    context: MemoryRetrievalContext,
    task_similarity: float,
) -> MemoryGateDecision:
    """Deterministic gate: semantic relevance, domain, page, action, and evidence."""
    same_domain = bool(
        context.website_domain
        and memory.domain
        and normalized_domain(context.website_domain) == normalized_domain(memory.domain)
    )
    page_sim = _page_similarity(
        context.page_context,
        memory.page_context or {},
        context.page_url_pattern,
        memory.page_url_pattern or "",
    )
    reliability = float(memory.reliability_score or 0.5)
    recency = _recency_score(memory.updated_at)
    task_type_match = not context.task_type or context.task_type == memory.task_type
    stored_failure_type = (memory.failure or {}).get("failure_type")
    if context.failure_type and stored_failure_type:
        failure_match = 1.0 if context.failure_type == stored_failure_type else 0.2
    else:
        failure_match = 0.5
    alternative = memory.successful_alternative or {}
    workflow = memory.verified_workflow or []
    reusable_alternative = alternative.get("action_type") if is_reusable_memory_action(alternative.get("action_type")) else None
    reusable_workflow = [
        step.get("action_type") for step in workflow
        if is_reusable_memory_action(step.get("action_type"))
    ]
    action_match = 0.55
    reusable_actions = [reusable_alternative] + reusable_workflow
    reusable_actions = [action for action in reusable_actions if action]
    if context.intended_action and reusable_actions:
        action_match = 1.0 if context.intended_action in reusable_actions else 0.2
    website_score = 1.0 if same_domain else (0.35 if not context.website_domain or not memory.domain else 0.1)
    relevance = (
        0.30 * (0.85 * task_similarity + 0.15 * (1.0 if task_type_match else 0.0))
        + 0.22 * page_sim
        + 0.15 * website_score
        + 0.15 * action_match
        + 0.10 * reliability
        + 0.05 * recency
        + 0.03 * failure_match
    )
    if memory.outcome == "recovered":
        relevance = min(1.0, relevance + 0.03)

    if memory.outcome == "recovered" and not reusable_actions:
        decision, reason = "IGNORE", "This recovery only observed or waited on the page; it did not verify a task-progressing alternative."
    elif task_similarity < settings.MEMORY_SIMILARITY_THRESHOLD:
        decision, reason = "IGNORE", "Task similarity is below the configured retrieval threshold."
    elif reliability < settings.MEMORY_MIN_RELIABILITY:
        decision, reason = "IGNORE", "Historical reliability is below the configured minimum."
    elif (
        same_domain
        and page_sim >= 0.45
        and relevance >= settings.MEMORY_USE_THRESHOLD
        and recency >= 0.6
        and (not context.failure_type or failure_match >= 0.7)
    ):
        decision, reason = "USE", "Same website and similar page controls with reliable verified evidence. Re-identify the control from the current page."
    elif relevance >= settings.MEMORY_CAUTION_THRESHOLD:
        decision, reason = "CAUTIOUS", "Potentially relevant, but domain, page, action, or confidence is incomplete; inspect current controls before acting."
    else:
        decision, reason = "IGNORE", "The combined task, website, page, and reliability evidence is insufficient."

    return MemoryGateDecision(
        memory_id=memory.id,
        decision=decision,
        relevance_score=round(relevance, 4),
        task_similarity=round(task_similarity, 4),
        task_type_match=task_type_match,
        website_match=same_domain,
        page_similarity=round(page_sim, 4),
        failure_match=round(failure_match, 4),
        reliability_score=round(reliability, 4),
        recency_score=recency,
        reason=reason,
    )


class ExperienceMemoryService:
    """SQL-backed memory lifecycle with ChromaDB semantic vector retrieval."""

    def __init__(self, session_factory: Optional[async_sessionmaker] = None):
        # An injected session factory is used by isolated unit tests. Keep those
        # tests hermetic unless they explicitly exercise the Chroma integration.
        self.chroma_enabled = session_factory is None
        if session_factory is None:
            from app.db import async_session_maker
            session_factory = async_session_maker
        self.session_factory = session_factory

    @staticmethod
    def _chroma_metadata(record: ExperienceMemoryModel) -> Dict[str, Any]:
        updated = record.updated_at or record.created_at or datetime.now(timezone.utc)
        return {
            "embedding_model": record.embedding_model or "hash-v1",
            "domain": record.domain or "",
            "task_type": record.task_type or "other",
            "outcome": record.outcome or "failure",
            "reliability_score": float(record.reliability_score or 0.5),
            "updated_at": updated.isoformat(),
        }

    async def _upsert_chroma_record(self, record: ExperienceMemoryModel) -> None:
        if not self.chroma_enabled or not record.embedding:
            return
        model = record.embedding_model or "hash-v1"
        payload = {
            "id": record.id,
            "embedding": list(record.embedding),
            "document": _stored_text(record),
            "metadata": self._chroma_metadata(record),
        }

        def upsert() -> None:
            collection = _chroma_collection(model)
            collection.upsert(
                ids=[payload["id"]],
                embeddings=[payload["embedding"]],
                documents=[payload["document"]],
                metadatas=[payload["metadata"]],
            )

        try:
            await asyncio.to_thread(upsert)
        except Exception as exc:
            logger.warning("Could not update ChromaDB memory index: %s", exc)

    async def rebuild_vector_index(self, batch_size: int = 250) -> int:
        """Idempotently backfill existing SQL memories into ChromaDB."""
        if not self.chroma_enabled or not settings.MEMORY_ENABLED:
            return 0
        indexed = 0
        offset = 0
        while True:
            async with self.session_factory() as session:
                rows = (await session.execute(
                    select(ExperienceMemoryModel)
                    .order_by(ExperienceMemoryModel.id)
                    .offset(offset)
                    .limit(batch_size)
                )).scalars().all()
                records = [{
                    "id": row.id,
                    "embedding": list(row.embedding or []),
                    "document": _stored_text(row),
                    "metadata": self._chroma_metadata(row),
                    "embedding_model": row.embedding_model or "hash-v1",
                } for row in rows if row.embedding]
            if not rows:
                break

            by_model: Dict[str, List[Dict[str, Any]]] = {}
            for record in records:
                by_model.setdefault(record["embedding_model"], []).append(record)

            try:
                for model, batch in by_model.items():
                    def upsert_batch(collection_model=model, collection_batch=batch) -> None:
                        collection = _chroma_collection(collection_model)
                        collection.upsert(
                            ids=[item["id"] for item in collection_batch],
                            embeddings=[item["embedding"] for item in collection_batch],
                            documents=[item["document"] for item in collection_batch],
                            metadatas=[item["metadata"] for item in collection_batch],
                        )
                    await asyncio.to_thread(upsert_batch)
                indexed += len(records)
            except Exception as exc:
                logger.warning("ChromaDB memory backfill stopped at offset %s: %s", offset, exc)
                return indexed
            offset += len(rows)
        logger.info("Indexed %s existing experience memories in ChromaDB", indexed)
        return indexed

    async def delete_vector_record(self, memory_id: str, embedding_model: Optional[str]) -> None:
        if not self.chroma_enabled:
            return
        embedding_model = embedding_model or "hash-v1"
        try:
            await asyncio.to_thread(
                lambda: _chroma_collection(embedding_model).delete(ids=[memory_id])
            )
        except Exception as exc:
            logger.warning("Could not delete memory %s from ChromaDB: %s", memory_id, exc)

    async def clear_vector_index(self) -> None:
        if not self.chroma_enabled:
            return

        def clear() -> None:
            client = _chroma_client()
            collections = client.list_collections()
            for collection in collections:
                name = collection if isinstance(collection, str) else collection.name
                if name.startswith("webmind_memory_"):
                    client.delete_collection(name)

        try:
            await asyncio.to_thread(clear)
        except Exception as exc:
            logger.warning("Could not clear ChromaDB memory index: %s", exc)

    async def _query_chroma(self, embedding: List[float], embedding_model: str) -> Dict[str, float]:
        if not self.chroma_enabled:
            return {}

        def query() -> Dict[str, float]:
            collection = _chroma_collection(embedding_model)
            count = collection.count()
            if not count:
                return {}
            result = collection.query(
                query_embeddings=[embedding],
                n_results=min(settings.MEMORY_CANDIDATE_LIMIT, count),
                include=["distances"],
            )
            id_rows = result.get("ids")
            distance_rows = result.get("distances")
            ids = id_rows[0] if id_rows is not None and len(id_rows) else []
            distances = distance_rows[0] if distance_rows is not None and len(distance_rows) else []
            return {
                memory_id: max(0.0, min(1.0, 1.0 - float(distance)))
                for memory_id, distance in zip(ids, distances)
            }

        return await asyncio.to_thread(query)

    async def _embed(self, text: str) -> Tuple[List[float], str]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(compute_embedding_with_model, text),
                timeout=settings.MEMORY_EMBEDDING_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Memory embedding unavailable; using deterministic fallback: %s", exc)
            return _fallback_embedding(text), "hash-v1"

    async def retrieve_for_step(
        self,
        context: MemoryRetrievalContext,
        *,
        task_id: str,
        step_number: int,
    ) -> List[Dict[str, Any]]:
        if not settings.MEMORY_ENABLED:
            return []
        safe_context = context.model_copy(update={
            "task_description": redact_sensitive_text(context.task_description, 2000),
            "page_context": context.page_context.model_copy(update={
                "page_title": redact_sensitive_text(context.page_context.page_title, 200),
                "relevant_elements": [redact_sensitive_text(x, 120) for x in context.page_context.relevant_elements[:60]],
                "available_controls": [redact_sensitive_text(x, 120) for x in context.page_context.available_controls[:60]],
                "page_state_summary": redact_sensitive_text(context.page_context.page_state_summary, 1000),
            }),
        })
        query_text = _context_text(safe_context)
        query_embedding, embedding_model = await self._embed(query_text)
        try:
            async with self.session_factory() as session:
                chroma_scores: Dict[str, float] = {}
                if self.chroma_enabled:
                    try:
                        chroma_scores = await self._query_chroma(query_embedding, embedding_model)
                    except Exception as exc:
                        logger.warning("ChromaDB query failed; using SQL fallback: %s", exc)

                if chroma_scores:
                    result = await session.execute(
                        select(ExperienceMemoryModel).where(
                            ExperienceMemoryModel.id.in_(chroma_scores)
                        )
                    )
                    records = list(result.scalars().all())
                    # Preserve lexical fallback for records written with another
                    # embedding model; vectors from different models are incomparable.
                    other_models = await session.execute(
                        select(ExperienceMemoryModel)
                        .where(ExperienceMemoryModel.embedding_model != embedding_model)
                        .order_by(ExperienceMemoryModel.updated_at.desc())
                        .limit(min(settings.MEMORY_CANDIDATE_LIMIT, 100))
                    )
                    records.extend(other_models.scalars().all())
                else:
                    result = await session.execute(
                        select(ExperienceMemoryModel)
                        .order_by(ExperienceMemoryModel.updated_at.desc())
                        .limit(settings.MEMORY_CANDIDATE_LIMIT)
                    )
                    records = list(result.scalars().all())
                scored: List[Tuple[float, ExperienceMemoryModel]] = []
                for record in records:
                    if record.id in chroma_scores:
                        score = chroma_scores[record.id]
                    elif record.embedding_model and record.embedding_model == embedding_model:
                        score = cosine_similarity(query_embedding, record.embedding or [])
                    else:
                        score = lexical_similarity(query_text, _stored_text(record))
                    if score >= settings.MEMORY_SIMILARITY_THRESHOLD:
                        scored.append((score, record))
                scored.sort(key=lambda item: (item[0], item[1].updated_at or item[1].created_at), reverse=True)
                candidates = scored[:settings.MEMORY_TOP_K]

                evaluated: List[Dict[str, Any]] = []
                now = datetime.now(timezone.utc)
                for similarity, record in candidates:
                    decision = evaluate_applicability(record, safe_context, similarity)
                    included = decision.decision in ("USE", "CAUTIOUS")
                    existing = await session.scalar(
                        select(MemoryAccessModel).where(
                            MemoryAccessModel.task_id == task_id,
                            MemoryAccessModel.step_number == step_number,
                            MemoryAccessModel.memory_id == record.id,
                        )
                    )
                    if existing is None:
                        session.add(MemoryAccessModel(
                            task_id=task_id,
                            step_number=step_number,
                            memory_id=record.id,
                            decision=decision.decision,
                            scores=decision.model_dump(mode="json"),
                            reason=decision.reason,
                            included_in_planner=included,
                        ))
                        record.retrieval_count = (record.retrieval_count or 0) + 1
                        if included:
                            record.last_used_at = now
                    evaluated.append({
                        "memory": self.serialize_memory(record),
                        "gate": decision.model_dump(mode="json"),
                        "included_in_planner": included,
                    })
                    await session.flush()
                await session.commit()
                return evaluated
        except Exception as exc:
            logger.warning("Experience memory retrieval failed; continuing without memory: %s", exc)
            return []

    async def mark_planner_selection(self, task_id: str, step_number: int, memory_ids: Iterable[str]) -> None:
        ids = set(memory_ids)
        if not ids:
            return
        try:
            async with self.session_factory() as session:
                rows = (await session.execute(select(MemoryAccessModel).where(
                    MemoryAccessModel.task_id == task_id,
                    MemoryAccessModel.step_number == step_number,
                    MemoryAccessModel.memory_id.in_(ids),
                    MemoryAccessModel.included_in_planner.is_(True),
                ))).scalars().all()
                for row in rows:
                    row.planner_selected = True
                await session.commit()
        except Exception as exc:
            logger.warning("Could not record planner memory selection: %s", exc)

    async def record_feedback(
        self,
        *,
        task_id: str,
        step_number: int,
        memory_ids: Iterable[str],
        outcome: Literal["success", "failure"],
        evidence: Dict[str, Any],
    ) -> List[str]:
        if not settings.MEMORY_ENABLED:
            return []
        updated: List[str] = []
        try:
            async with self.session_factory() as session:
                for memory_id in set(memory_ids):
                    access = await session.scalar(select(MemoryAccessModel).where(
                        MemoryAccessModel.task_id == task_id,
                        MemoryAccessModel.step_number == step_number,
                        MemoryAccessModel.memory_id == memory_id,
                        MemoryAccessModel.included_in_planner.is_(True),
                    ))
                    if access is None or access.feedback_applied:
                        continue
                    record = await session.get(ExperienceMemoryModel, memory_id)
                    if record is None:
                        continue
                    claim = await session.execute(
                        update(MemoryAccessModel)
                        .where(
                            MemoryAccessModel.id == access.id,
                            MemoryAccessModel.feedback_applied.is_(False),
                        )
                        .values(feedback_applied=True, attributed_outcome=outcome)
                    )
                    if claim.rowcount != 1:
                        continue
                    evidence_key = f"reuse:{task_id}:{step_number}:{outcome}"
                    added = await self._add_evidence(
                        session, record, task_id, evidence_key, outcome, evidence
                    )
                    if added:
                        await self._update_reliability_atomic(session, record, outcome)
                        updated.append(memory_id)
                await session.commit()
            return updated
        except Exception as exc:
            logger.warning("Could not update memory reliability: %s", exc)
            return []

    async def record_experience(
        self,
        memory: ExperienceMemoryData,
        *,
        task_id: str,
        evidence_key: str,
        evidence_outcome: Literal["success", "failure"],
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not settings.MEMORY_ENABLED:
            return None
        try:
            safe = self._sanitize_experience(memory)
            embedding_text = self._experience_embedding_text(safe)
            embedding, embedding_model = await self._embed(embedding_text)
            dedupe_key = self._dedupe_key(safe)
            async with self.session_factory() as session:
                record = await session.scalar(select(ExperienceMemoryModel).where(
                    ExperienceMemoryModel.dedupe_key == dedupe_key
                ))
                if record is None:
                    record = ExperienceMemoryModel(
                        id=safe.memory_id,
                        query=safe.task_description,
                        embedding=embedding,
                        embedding_model=embedding_model,
                        task_type=safe.task_type,
                        tier=safe.tier,
                        profile_summary={},
                        action_summary=self._action_summary(safe),
                        outcome=safe.outcome,
                        failure_types=[safe.failure.failure_type] if safe.failure and safe.failure.occurred else [],
                        schema_version=safe.schema_version,
                        domain=safe.website_domain,
                        page_url_pattern=safe.page_url_pattern,
                        page_context=safe.page_context.model_dump(mode="json"),
                        action_attempted=safe.action_attempted.model_dump(mode="json"),
                        failure=safe.failure.model_dump(mode="json") if safe.failure else None,
                        successful_alternative=safe.successful_alternative.model_dump(mode="json") if safe.successful_alternative else None,
                        verified_workflow=[step.model_dump(mode="json") for step in safe.verified_workflow],
                        reliability_score=_reliability(0, 0),
                        success_count=0,
                        failure_count=0,
                        retrieval_count=0,
                        dedupe_key=dedupe_key,
                    )
                    session.add(record)
                    await session.flush()
                elif safe.outcome == "recovered":
                    record.outcome = "recovered"
                    record.successful_alternative = safe.successful_alternative.model_dump(mode="json") if safe.successful_alternative else record.successful_alternative
                    record.updated_at = datetime.now(timezone.utc)
                added = await self._add_evidence(
                    session, record, task_id, evidence_key, evidence_outcome,
                    self._safe_evidence(evidence or {}),
                )
                if added:
                    await self._update_reliability_atomic(session, record, evidence_outcome)
                await session.commit()
                await self._upsert_chroma_record(record)
                return record.id
        except Exception as exc:
            logger.warning("Experience memory ingestion failed; continuing without memory: %s", exc)
            return None

    async def list_access_history(self, memory_id: str, session: AsyncSession, limit: int = 100) -> List[Dict[str, Any]]:
        rows = (await session.execute(select(MemoryAccessModel)
            .where(MemoryAccessModel.memory_id == memory_id)
            .order_by(MemoryAccessModel.created_at.desc())
            .limit(limit))).scalars().all()
        return [{
            "task_id": row.task_id,
            "step_number": row.step_number,
            "decision": row.decision,
            "scores": row.scores or {},
            "reason": row.reason,
            "included_in_planner": row.included_in_planner,
            "planner_selected": row.planner_selected,
            "feedback_applied": row.feedback_applied,
            "attributed_outcome": row.attributed_outcome,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]

    async def list_evidence(self, memory_id: str, session: AsyncSession, limit: int = 100) -> List[Dict[str, Any]]:
        rows = (await session.execute(select(MemoryEvidenceModel)
            .where(MemoryEvidenceModel.memory_id == memory_id)
            .order_by(MemoryEvidenceModel.created_at.desc())
            .limit(limit))).scalars().all()
        return [{
            "task_id": row.task_id,
            "outcome": row.outcome,
            "evidence": row.evidence or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]

    @staticmethod
    def serialize_memory(record: ExperienceMemoryModel) -> Dict[str, Any]:
        created = record.created_at or datetime.now(timezone.utc)
        updated = record.updated_at or created
        page = dict(record.page_context or {})
        page = {
            "page_title": redact_sensitive_text(page.get("page_title", ""), 200),
            "relevant_elements": [redact_sensitive_text(x, 120) for x in (page.get("relevant_elements") or [])[:60]],
            "available_controls": [redact_sensitive_text(x, 120) for x in (page.get("available_controls") or [])[:60]],
            "page_state_summary": redact_sensitive_text(page.get("page_state_summary", ""), 1000),
        }
        attempted = dict(record.action_attempted or {"action_type": "unknown", "target_description": record.action_summary or ""})
        attempted["target_description"] = redact_sensitive_text(attempted.get("target_description", ""), 300)
        attempted["action_parameters"] = {
            key: value for key, value in (attempted.get("action_parameters") or {}).items()
            if key in {"submit", "input_kind", "input_length", "direction", "amount", "key"}
        }
        failure = dict(record.failure) if record.failure else None
        if failure:
            failure["reason"] = redact_sensitive_text(failure.get("reason", ""), 1000)
            failure["verification_evidence"] = redact_sensitive_text(failure.get("verification_evidence", ""), 1000)
        alternative = dict(record.successful_alternative) if record.successful_alternative else None
        if alternative:
            alternative["target_description"] = redact_sensitive_text(alternative.get("target_description", ""), 300)
            alternative["recovery_steps"] = [redact_sensitive_text(x, 300) for x in (alternative.get("recovery_steps") or [])[:20]]
        workflow = []
        for step in (record.verified_workflow or [])[:30]:
            safe_step = dict(step)
            safe_step["target_description"] = redact_sensitive_text(safe_step.get("target_description", ""), 300)
            safe_step["action_parameters"] = {
                key: value for key, value in (safe_step.get("action_parameters") or {}).items()
                if key in {"submit", "input_kind", "input_length", "direction", "amount", "key"}
            }
            workflow.append(safe_step)
        return {
            "memory_id": record.id,
            "schema_version": record.schema_version or 1,
            "task_description": redact_sensitive_text(record.query, 2000),
            "task_type": record.task_type or "other",
            "tier": record.tier or "GENERAL",
            "website_domain": record.domain,
            "page_url_pattern": record.page_url_pattern or "",
            "page_context": page,
            "action_attempted": attempted,
            "failure": failure,
            "successful_alternative": alternative,
            "verified_workflow": workflow,
            "outcome": record.outcome,
            "reliability_score": float(record.reliability_score if record.reliability_score is not None else 0.5),
            "success_count": int(record.success_count or 0),
            "failure_count": int(record.failure_count or 0),
            "retrieval_count": int(record.retrieval_count or 0),
            "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
        }

    @staticmethod
    async def _update_reliability_atomic(
        session: AsyncSession,
        record: ExperienceMemoryModel,
        outcome: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        if outcome == "success":
            values = {"success_count": func.coalesce(ExperienceMemoryModel.success_count, 0) + 1}
        else:
            values = {"failure_count": func.coalesce(ExperienceMemoryModel.failure_count, 0) + 1}
        values["updated_at"] = now
        await session.execute(
            update(ExperienceMemoryModel).where(ExperienceMemoryModel.id == record.id).values(**values)
        )
        await session.refresh(record)
        record.reliability_score = _reliability(record.success_count, record.failure_count)
        await session.flush()

    @staticmethod
    async def _add_evidence(
        session: AsyncSession,
        record: ExperienceMemoryModel,
        task_id: str,
        evidence_key: str,
        outcome: str,
        evidence: Dict[str, Any],
    ) -> bool:
        existing = await session.scalar(select(MemoryEvidenceModel).where(
            MemoryEvidenceModel.memory_id == record.id,
            MemoryEvidenceModel.evidence_key == evidence_key,
        ))
        if existing is not None:
            return False
        session.add(MemoryEvidenceModel(
            memory_id=record.id,
            task_id=task_id,
            evidence_key=evidence_key,
            outcome=outcome,
            evidence=evidence,
        ))
        await session.flush()
        return True

    @staticmethod
    def _safe_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"failure_type", "reason", "verification_status", "action_type", "target_description", "step_number", "task_type"}
        return {key: redact_sensitive_text(value, 500) for key, value in evidence.items() if key in allowed and value is not None}

    @staticmethod
    def _sanitize_experience(memory: ExperienceMemoryData) -> ExperienceMemoryData:
        data = memory.model_dump(mode="python")
        data["task_description"] = redact_sensitive_text(data["task_description"], 2000)
        page = data.get("page_context") or {}
        page["page_title"] = redact_sensitive_text(page.get("page_title"), 200)
        page["relevant_elements"] = [redact_sensitive_text(x, 120) for x in page.get("relevant_elements", [])[:60]]
        page["available_controls"] = [redact_sensitive_text(x, 120) for x in page.get("available_controls", [])[:60]]
        page["page_state_summary"] = redact_sensitive_text(page.get("page_state_summary"), 1000)
        data["page_context"] = page
        attempted = data.get("action_attempted") or {}
        attempted["target_description"] = redact_sensitive_text(attempted.get("target_description"), 300)
        # Values are never retained in action parameters; only safe structural metadata.
        attempted["action_parameters"] = {
            key: value for key, value in (attempted.get("action_parameters") or {}).items()
            if key in {"submit", "input_kind", "input_length", "direction", "amount", "key"}
        }
        data["action_attempted"] = attempted
        if data.get("failure"):
            data["failure"]["reason"] = redact_sensitive_text(data["failure"].get("reason"), 1000)
            data["failure"]["verification_evidence"] = redact_sensitive_text(data["failure"].get("verification_evidence"), 1000)
        if data.get("successful_alternative"):
            alt = data["successful_alternative"]
            alt["target_description"] = redact_sensitive_text(alt.get("target_description"), 300)
            alt["recovery_steps"] = [redact_sensitive_text(x, 300) for x in alt.get("recovery_steps", [])[:20]]
        data["verified_workflow"] = [
            {
                **step,
                "target_description": redact_sensitive_text(step.get("target_description"), 300),
                "action_parameters": {
                    key: value for key, value in (step.get("action_parameters") or {}).items()
                    if key in {"submit", "input_kind", "input_length", "direction", "amount", "key"}
                },
            }
            for step in data.get("verified_workflow", [])[:30]
        ]
        return ExperienceMemoryData.model_validate(data)

    @staticmethod
    def _experience_embedding_text(memory: ExperienceMemoryData) -> str:
        page = memory.page_context
        alternative = memory.successful_alternative
        return " ".join([
            memory.task_description,
            memory.task_type,
            memory.website_domain or "",
            memory.page_url_pattern,
            page.page_title,
            " ".join(page.relevant_elements),
            " ".join(page.available_controls),
            page.page_state_summary,
            memory.failure.failure_type if memory.failure else "",
            memory.action_attempted.action_type,
            memory.action_attempted.target_description,
            alternative.action_type or "" if alternative else "",
            alternative.target_description if alternative else "",
            " ".join(f"{step.action_type} {step.target_description}" for step in memory.verified_workflow),
        ])

    @staticmethod
    def _action_summary(memory: ExperienceMemoryData) -> str:
        attempted = memory.action_attempted
        if memory.successful_alternative:
            alt = memory.successful_alternative
            return f"{attempted.action_type} on {attempted.target_description} failed; verified {alt.action_type} on {alt.target_description}."
        if memory.verified_workflow:
            return "Verified workflow: " + " → ".join(
                f"{step.action_type} {step.target_description}" for step in memory.verified_workflow[:8]
            )
        return f"{attempted.action_type} on {attempted.target_description} ({memory.outcome})."

    @staticmethod
    def _dedupe_key(memory: ExperienceMemoryData) -> str:
        alternative = memory.successful_alternative
        parts = [
            memory.website_domain or "",
            memory.page_url_pattern,
            memory.task_type,
            memory.failure.failure_type if memory.failure else "",
            memory.action_attempted.action_type,
            memory.action_attempted.target_description,
            alternative.action_type if alternative else "",
            alternative.target_description if alternative else "",
            " ".join(f"{step.action_type}:{step.target_description}" for step in memory.verified_workflow[:4]),
        ]
        normalized = "|".join(" ".join(part.lower().split()) for part in parts)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Compatibility name retained for integrations that imported the former facade.
ExperienceMemory = ExperienceMemoryService
