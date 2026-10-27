"""Dense channel: an OpenAI-compatible embeddings endpoint, vector storage and the background indexer.

Everything here is optional. With no ``memory_embedding_base_url`` the history search stays lexical, and when the endpoint
is down a query falls back to lexical for that call (architecture doc §24: "Vector DB unavailable ... chat still works").
Vectors are derived data: they are keyed by turn and model, removed with the turn, and rebuilt when the model changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from array import array

import httpx
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.jobs import RunStatus
from ..db import SessionLocal
from ..locks import user_critical_section
from ..metrics import MEMORY_EMBEDDING
from ..models import Conversation, MemoryEmbedding, Run, User
from .policy import run_permissions

log = logging.getLogger("ailab.memory_recall.embeddings")

TEXT_CHARS = 1500  # of the prompt, and again of the reply, that a turn's vector is computed from
SCAN_ROWS = 500  # unindexed turns looked at per tick (some are skipped: memory switched off)


def enabled() -> bool:
    return bool(settings.memory_embedding_base_url.strip() and settings.memory_embedding_model.strip())


def index_model() -> str:
    fingerprint = hashlib.sha256(
        (settings.memory_embedding_model + "\n" + settings.memory_embedding_version + "\n" + settings.memory_embedding_doc_prefix).encode()
    ).hexdigest()[:24]
    return settings.memory_embedding_model[:80] + ":" + fingerprint


def _headers() -> dict:
    key = settings.memory_embedding_api_key.strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(math.fsum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def embed(texts: list[str], *, timeout: float) -> list[list[float]]:
    """One normalized vector per text. Raises on any transport, status or shape problem."""
    url = settings.memory_embedding_base_url.rstrip("/") + "/embeddings"
    try:
        # trust_env=False: the endpoint is configured infrastructure and the body carries the API key
        # plus user conversation text — an ambient HTTP_PROXY must never see either.
        response = httpx.post(
            url, json={"model": settings.memory_embedding_model, "input": texts}, headers=_headers(), timeout=timeout, trust_env=False
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda r: r["index"])
        vectors = [_normalize([float(x) for x in row["embedding"]]) for row in rows]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        MEMORY_EMBEDDING.labels("error").inc()
        raise EmbeddingError(f"embedding endpoint failure: {type(exc).__name__}") from exc
    if (
        len(vectors) != len(texts)
        or len({len(v) for v in vectors}) != 1
        or any(not v or len(v) > 8192 or any(not math.isfinite(x) for x in v) for v in vectors)
        or [row["index"] for row in rows] != list(range(len(texts)))
    ):
        MEMORY_EMBEDDING.labels("error").inc()
        raise EmbeddingError("embedding endpoint returned an unexpected number or size of vectors")
    MEMORY_EMBEDDING.labels("ok").inc()
    return vectors


class EmbeddingError(RuntimeError):
    pass


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> array:
    out = array("f")
    out.frombytes(blob)
    return out


def similarity(a: array, b: array) -> float:
    """Dot product of two normalized vectors."""
    return math.sumprod(a, b) if len(a) == len(b) else 0.0


def embed_query(query: str) -> array | None:
    """The query's vector, or None when the dense channel is off or unreachable."""
    if not enabled():
        return None
    try:
        (vector,) = embed([settings.memory_embedding_query_prefix + query], timeout=settings.memory_embedding_query_timeout_seconds)
    except EmbeddingError as exc:
        log.warning("history recall continues without the dense channel: %s", exc)
        return None
    return array("f", vector)


def vectors_for(db: Session, user_id: str, run_ids: list[str]) -> dict[str, array]:
    """Stored vectors of the configured model for these turns (only the user's own)."""
    if not run_ids:
        return {}
    rows = db.execute(
        select(MemoryEmbedding.run_id, MemoryEmbedding.vector).where(
            MemoryEmbedding.user_id == user_id,
            MemoryEmbedding.model == index_model(),
            MemoryEmbedding.run_id.in_(run_ids),
        )
    ).all()
    return {run_id: unpack(blob) for run_id, blob in rows}


def turn_text(prompt: str, summary: str) -> str:
    return settings.memory_embedding_doc_prefix + (prompt or "")[:TEXT_CHARS] + "\n" + (summary or "")[:TEXT_CHARS]


def delete_for_runs(db: Session, run_ids: list[str]) -> int:
    if not run_ids:
        return 0
    return db.execute(delete(MemoryEmbedding).where(MemoryEmbedding.run_id.in_(run_ids))).rowcount or 0


def index_pending(limit: int | None = None, *, user_id: str | None = None) -> int:
    """Embed finished turns that have no vector yet, newest first (one user's, if given). Returns how many were stored."""
    if not enabled():
        return 0
    limit = limit or settings.memory_embedding_batch
    with SessionLocal() as db:
        stored = select(MemoryEmbedding.run_id).where(MemoryEmbedding.run_id == Run.id, MemoryEmbedding.model == index_model()).exists()
        scope = [Run.user_id == user_id] if user_id else []
        rows = db.execute(
            select(
                Run.id,
                Run.user_id,
                func.substr(Run.prompt, 1, TEXT_CHARS),
                func.substr(Run.summary, 1, TEXT_CHARS),
                User.settings,
            )
            .join(User, User.id == Run.user_id)
            .outerjoin(Conversation, Conversation.id == Run.conversation_id)
            .where(
                Run.status == RunStatus.SUCCEEDED,
                Conversation.memory_policy["write"].as_boolean().is_not(False),
                Run.options["memory_policy"]["write"].as_boolean().is_not(False),
                Run.superseded_by_run_id.is_(None),
                User.disabled_at.is_(None),
                User.settings["memory"]["enabled"].as_boolean().is_not(False),
                User.settings["memory"]["reference_history"].as_boolean().is_not(False),
                or_(Run.conversation_id.is_(None), Conversation.deleted_at.is_(None)),
                ~stored,
                *scope,
            )
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(SCAN_ROWS)
        ).all()
        todo = []
        for run_id, user_id, prompt, summary, user_settings in rows:
            memory = (user_settings or {}).get("memory") or {}
            if memory.get("enabled") is False:
                continue  # a user who turned memory off is never sent to the endpoint
            todo.append((run_id, user_id, turn_text(prompt, summary)))
            if len(todo) == limit:
                break
        if not todo:
            return 0
        db.commit()
        vectors = embed([t for _, _, t in todo], timeout=60)
        for (run_id, user_id, _), vector in zip(todo, vectors, strict=True):
            with user_critical_section(db, user_id):
                owner = db.get(User, user_id, populate_existing=True)
                source = db.get(Run, run_id, populate_existing=True)
                conversation = (
                    db.get(Conversation, source.conversation_id, populate_existing=True) if source and source.conversation_id else None
                )
                memory = (owner.settings or {}).get("memory") or {} if owner else {}
                if not owner or owner.disabled_at or memory.get("enabled") is False or memory.get("reference_history") is False:
                    db.commit()
                    continue
                if (
                    not source
                    or source.superseded_by_run_id
                    or (conversation and conversation.deleted_at)
                    or not run_permissions(db, source)["write"]
                ):
                    db.commit()
                    continue
                if db.scalar(select(MemoryEmbedding.id).where(MemoryEmbedding.run_id == run_id, MemoryEmbedding.model == index_model())):
                    db.commit()
                    continue
                db.add(MemoryEmbedding(user_id=user_id, run_id=run_id, model=index_model(), dim=len(vector), vector=pack(vector)))
                db.commit()
        return len(todo)


async def embed_forever() -> None:
    if not enabled() or settings.env == "test":
        return
    log.info("memory embedding indexer started model=%s", settings.memory_embedding_model)
    while True:
        try:
            while await asyncio.to_thread(index_pending):  # keep going while there is a backlog
                await asyncio.sleep(0.2)
        except Exception as exc:
            log.warning("memory embedding pass failed: %s", exc)
        await asyncio.sleep(settings.memory_embedding_interval_seconds)
