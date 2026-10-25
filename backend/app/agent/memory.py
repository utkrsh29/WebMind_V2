import asyncio
import hashlib
import json
import logging
import math
from typing import Any, Dict, List, Optional
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ExperienceMemoryModel
from app.router.task_profile import TaskProfile

logger = logging.getLogger(__name__)

_EMBEDDER = None


def get_embedder():
    """Lazily loads sentence-transformers model with fallback."""
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            from sentence_transformers import SentenceTransformer
            _EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded sentence-transformers model 'all-MiniLM-L6-v2'")
        except Exception as e:
            logger.warning(f"Could not load sentence-transformers ({e}). Using deterministic fallback embedder.")
            _EMBEDDER = "fallback"
    return _EMBEDDER


def compute_embedding(text: str) -> List[float]:
    """Generates a 384-dimensional normalized embedding vector."""
    embedder = get_embedder()
    if embedder != "fallback":
        try:
            emb = embedder.encode(text, normalize_embeddings=True)
            return emb.tolist()
        except Exception as e:
            logger.warning(f"SentenceTransformer encoding failed: {e}. Falling back.")

    # Fallback pseudo-embedding (384-dimensional deterministic bag-of-hashes)
    vec = [0.0] * 384
    words = text.lower().split()
    for w in words:
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % 384
        vec[idx] += 1.0

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Computes cosine similarity between two float vectors."""
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


class ExperienceMemory:
    @staticmethod
    async def store_experience(
        session: AsyncSession,
        query: str,
        profile: TaskProfile,
        tier: str,
        action_summary: str,
        outcome: str,
        failure_types: List[str],
    ) -> None:
        """Stores a completed task run into experience memory."""
        try:
            embedding = await asyncio.to_thread(compute_embedding, query)
            record = ExperienceMemoryModel(
                query=query,
                embedding=embedding,
                task_type=profile.task_type,
                tier=tier,
                profile_summary={
                    "constraints": profile.constraints,
                    "target_sites": profile.target_sites,
                    "requires_filtering": profile.requires_filtering,
                    "requires_comparison": profile.requires_comparison,
                },
                action_summary=action_summary,
                outcome=outcome,
                failure_types=failure_types,
            )
            session.add(record)
            await session.commit()
            logger.info(f"Stored experience memory for task: '{query[:40]}...'")
        except Exception as e:
            logger.warning(f"Failed to store experience memory: {e}")

    @staticmethod
    async def retrieve_similar_experiences(
        session: AsyncSession,
        query: str,
        top_k: int = 3,
        min_similarity: float = 0.45,
    ) -> List[str]:
        """Retrieves helpful hints from past successful or failed runs."""
        try:
            query_emb = await asyncio.to_thread(compute_embedding, query)
            stmt = select(ExperienceMemoryModel).order_by(ExperienceMemoryModel.created_at.desc()).limit(50)
            result = await session.execute(stmt)
            memories = result.scalars().all()

            scored: List[Tuple[float, ExperienceMemoryModel]] = []
            for mem in memories:
                sim = cosine_similarity(query_emb, mem.embedding)
                if sim >= min_similarity:
                    scored.append((sim, mem))

            scored.sort(key=lambda x: x[0], reverse=True)
            hints = []
            for sim, mem in scored[:top_k]:
                if mem.outcome == "success":
                    hints.append(f"Similar task ({mem.task_type}) succeeded on {mem.tier}: {mem.action_summary[:150]}")
                else:
                    failures = ", ".join(mem.failure_types) if mem.failure_types else "unknown"
                    hints.append(f"Caution: Similar past task failed due to [{failures}]. Avoid repetitive actions.")
            return hints
        except Exception as e:
            logger.warning(f"Error querying experience memory: {e}")
            return []
