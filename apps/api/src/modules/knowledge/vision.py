"""Vision verification of discovered product images (plan WP3).

The heuristic discovery in ``media.py`` guesses which offering an image shows
from alt text and page titles. A vision model on the operator's own GPU host
looks at the pixels and answers a fixed JSON schema: is it a product photo,
which approved offering does it show, how confident, and a short Turkish
description. Trusted code then decides: reject, override the subject, or
keep — and stores a text-free verification trail on ``KnowledgeMedia``.
The model never publishes anything; the publisher still owns that step.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Protocol

import structlog

from src.core.config import Settings, get_settings
from src.modules.agents.company_runtime import has_protected_intent
from src.modules.agents.grounded_audit import _CURRENCY_RE, _INJECTION_RE, _LINK_RE

from .chunking import normalize_whitespace
from .media import FetchedImage, ImageCandidate

logger = structlog.get_logger(__name__)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ALT_MAX_CHARS = 300
_HEURISTIC_FULL_SCORE = 5.0
_HEURISTIC_KEEP_SCORE = 2.0


@dataclass(frozen=True)
class VisionVerdict:
    is_product_photo: bool
    subject_id: str | None
    confidence: float
    alt_text: str | None
    contains_text_overlay: bool = False
    model: str = ""


class MediaVerifier(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def model(self) -> str: ...

    async def verify(
        self,
        image: bytes,
        mime_type: str,
        *,
        subject_labels: dict[str, str],
        context: str,
    ) -> VisionVerdict: ...


class NullMediaVerifier:
    available = False
    model = ""

    async def verify(
        self,
        image: bytes,
        mime_type: str,
        *,
        subject_labels: dict[str, str],
        context: str,
    ) -> VisionVerdict:
        raise RuntimeError("vision verification is disabled")


@dataclass(frozen=True)
class MediaDecision:
    store: bool
    subject_id: str | None
    score: float
    alt_text: str | None
    verification: dict[str, object]


def sanitize_alt_text(text: str | None) -> tuple[str | None, tuple[str, ...]]:
    """Model-written alt text is data: strip control characters, refuse anything
    that looks like an instruction, a price, a protected claim or a link."""

    if text is None:
        return None, ()
    clean = normalize_whitespace(_CONTROL_RE.sub("", text))[:_ALT_MAX_CHARS].strip()
    if not clean:
        return None, ()
    flags: list[str] = []
    if _INJECTION_RE.search(clean):
        flags.append("alt_injection")
    if _CURRENCY_RE.search(clean) or has_protected_intent(clean):
        flags.append("alt_protected")
    if _LINK_RE.search(clean):
        flags.append("alt_link")
    if flags:
        return None, tuple(flags)
    return clean, ()


def downscale_for_model(content: bytes, *, max_edge: int) -> bytes:
    """JPEG copy no larger than ``max_edge`` on its longest side (stored image untouched)."""

    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        converted = image.convert("RGB")
        if max(converted.size) > max_edge:
            converted.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        converted.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()


def _heuristic_decision(
    candidate: ImageCandidate, verification: dict[str, object]
) -> MediaDecision:
    return MediaDecision(
        store=True,
        subject_id=candidate.subject_id,
        score=candidate.score,
        alt_text=(candidate.alt or candidate.context or None),
        verification=verification,
    )


async def verify_media(
    candidate: ImageCandidate,
    image: FetchedImage,
    *,
    subject_labels: dict[str, str],
    verifier: MediaVerifier | None,
    min_confidence: float,
    max_edge: int,
    context: str = "",
) -> MediaDecision:
    """Combine the discovery heuristic with the vision verdict.

    * not a product photo and a weak heuristic → not stored;
    * confident verdict for another approved offering → subject overridden;
    * otherwise kept; ``score`` blends both signals; alt text is only written
      when the page had none and the model text passes ``sanitize_alt_text``.
    A verifier outage keeps today's heuristic behaviour and records it.
    """

    if verifier is None or not verifier.available:
        return _heuristic_decision(candidate, {})
    try:
        payload = downscale_for_model(image.content, max_edge=max_edge)
        verdict = await verifier.verify(
            payload,
            "image/jpeg",
            subject_labels=subject_labels,
            context=context[:300],
        )
    except Exception as exc:
        logger.warning("knowledge.vision.unavailable", error=type(exc).__name__)
        return _heuristic_decision(
            candidate,
            {"status": "unavailable", "error": type(exc).__name__, "model": verifier.model},
        )

    heuristic = min(candidate.score / _HEURISTIC_FULL_SCORE, 1.0)
    subject_before = candidate.subject_id
    subject_after = subject_before
    flags: list[str] = []
    decision = "kept"
    store = True
    if not verdict.is_product_photo:
        if candidate.score < _HEURISTIC_KEEP_SCORE:
            store, decision = False, "rejected"
        else:
            flags.append("not_product_photo")
    if (
        store
        and verdict.subject_id is not None
        and verdict.subject_id in subject_labels
        and verdict.confidence >= min_confidence
        and verdict.subject_id != subject_before
    ):
        subject_after, decision = verdict.subject_id, "overridden"
    if verdict.contains_text_overlay:
        flags.append("text_overlay")

    alt_text = candidate.alt or candidate.context or None
    alt_from_model = False
    if store and not alt_text:
        alt_text, alt_flags = sanitize_alt_text(verdict.alt_text)
        alt_from_model = alt_text is not None
        flags.extend(alt_flags)
    score = round(0.6 * heuristic + 0.4 * max(0.0, min(1.0, verdict.confidence)), 4)
    verification: dict[str, object] = {
        "status": "verified",
        "model": verdict.model or verifier.model,
        "decision": decision,
        "confidence": round(verdict.confidence, 4),
        "is_product_photo": verdict.is_product_photo,
        "subject_before": subject_before,
        "subject_after": subject_after,
        "alt_text_written": alt_from_model,
        "flags": flags,
    }
    return MediaDecision(
        store=store,
        subject_id=subject_after,
        score=score if store else candidate.score,
        alt_text=alt_text,
        verification=verification,
    )


def build_media_verifier(settings: Settings | None = None) -> MediaVerifier | None:
    """Settings → verifier; ``None`` keeps the heuristic-only behaviour."""

    s = settings or get_settings()
    if not s.knowledge_vision_enabled or not s.knowledge_vision_base_url.strip():
        return None
    from src.integrations.nim import NimHttp
    from src.integrations.nim.vision import NimVisionClient

    return NimVisionClient(
        NimHttp(
            s.knowledge_vision_base_url,
            api_key=s.knowledge_vision_api_key or s.nim_api_key,
            timeout_seconds=max(s.nim_timeout_seconds, 30.0),
            name="knowledge.vision",
        ),
        model=s.knowledge_vision_model,
        schema_mode=s.knowledge_vision_schema_mode,
    )
