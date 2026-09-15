"""Product image discovery, validation and normalization.

Images are candidates like facts: discovered on the tenant's own pages,
scored by how clearly they belong to an offering, downloaded, verified with
Pillow, converted to JPEG/PNG within Meta's 5 MB session-media limit and
stored in the database. They are served from the deployment's own public
origin (never hot-linked) and only attached to an offering by the publisher.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass

import httpx
from PIL import Image, UnidentifiedImageError

from .compiler import normalize_text, query_tokens
from .extract import PageExtract

_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_DOWNLOAD_CAP = 12 * 1024 * 1024
_MAX_EDGE = 1600
_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=8.0)
_SIZE_HINT_RE = re.compile(r"(\d{2,4})x(\d{2,4})")


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    score: float
    alt: str
    context: str
    subject_id: str | None
    source: str


@dataclass(frozen=True)
class FetchedImage:
    content: bytes
    mime_type: str
    width: int
    height: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _token_overlap(text: str, label_tokens: set[str]) -> int:
    return len(set(query_tokens(text)) & label_tokens)


def guess_subject(page: PageExtract, subject_labels: dict[str, str]) -> str | None:
    """Pick the offering whose approved label best matches the page title/product."""

    haystack = " ".join(filter(None, [page.title or "", page.product.get("name", ""), page.url]))
    best: tuple[int, str] | None = None
    for subject_id, label in subject_labels.items():
        tokens = {t for t in query_tokens(label) if len(t) > 2}
        if not tokens:
            continue
        overlap = _token_overlap(haystack, tokens)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, subject_id)
    return best[1] if best else None


def discover_images(
    page: PageExtract,
    subject_labels: dict[str, str],
    *,
    min_pixels: int = 300,
    limit: int = 6,
) -> list[ImageCandidate]:
    page_subject = guess_subject(page, subject_labels)
    candidates: list[ImageCandidate] = []
    for ref in page.images:
        score = {"jsonld": 3.0, "og": 2.0, "img": 1.0}[ref.source]
        width, height = ref.width, ref.height
        if width is None or height is None:
            hint = _SIZE_HINT_RE.search(ref.url)
            if hint:
                width, height = int(hint.group(1)), int(hint.group(2))
        if width is not None and height is not None:
            if width < min_pixels or height < min_pixels:
                continue
            score += 0.5
        subject_id = page_subject
        text = f"{ref.alt} {ref.context}"
        for candidate_subject, label in subject_labels.items():
            tokens = {t for t in query_tokens(label) if len(t) > 2}
            if tokens and _token_overlap(text, tokens) >= max(1, len(tokens) // 2):
                subject_id = candidate_subject
                score += 1.5
                break
        if page.product.get("name") and normalize_text(page.product["name"]) in normalize_text(
            text
        ):
            score += 1.0
        candidates.append(
            ImageCandidate(
                url=ref.url,
                score=score,
                alt=ref.alt[:300],
                context=ref.context[:300],
                subject_id=subject_id,
                source=ref.source,
            )
        )
    candidates.sort(key=lambda c: (-c.score, c.url))
    return candidates[:limit]


def normalize_image(data: bytes) -> FetchedImage | None:
    """Verify, downscale and re-encode to JPEG/PNG under Meta's limits."""

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            if width < 50 or height < 50:
                return None
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            target_format = "PNG" if has_alpha else "JPEG"
            converted = image.convert("RGBA" if has_alpha else "RGB")
            if max(width, height) > _MAX_EDGE:
                converted.thumbnail((_MAX_EDGE, _MAX_EDGE))
            buffer = io.BytesIO()
            if target_format == "JPEG":
                converted.save(buffer, format="JPEG", quality=85, optimize=True)
            else:
                converted.save(buffer, format="PNG", optimize=True)
            output = buffer.getvalue()
            if len(output) > _MAX_IMAGE_BYTES and target_format == "PNG":
                buffer = io.BytesIO()
                converted.convert("RGB").save(buffer, format="JPEG", quality=80, optimize=True)
                output = buffer.getvalue()
                target_format = "JPEG"
            if len(output) > _MAX_IMAGE_BYTES:
                return None
            return FetchedImage(
                content=output,
                mime_type="image/jpeg" if target_format == "JPEG" else "image/png",
                width=converted.size[0],
                height=converted.size[1],
            )
    except (UnidentifiedImageError, OSError, ValueError):
        return None


async def fetch_image(
    url: str,
    *,
    user_agent: str = "LeadPulseBot/1.0",
    client: httpx.AsyncClient | None = None,
    min_pixels: int = 300,
) -> FetchedImage | None:
    owns = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        response = await http.get(url, headers={"User-Agent": user_agent, "Accept": "image/*"})
        if response.status_code != 200 or len(response.content) > _DOWNLOAD_CAP:
            return None
        image = normalize_image(response.content)
        if image is None or image.width < min_pixels or image.height < min_pixels:
            return None
        return image
    except httpx.HTTPError:
        return None
    finally:
        if owns:
            await http.aclose()


def media_public_path(tenant_hex: str, sha256: str, mime_type: str) -> str:
    extension = "jpg" if mime_type == "image/jpeg" else "png"
    return f"/media/k/{tenant_hex}/{sha256}.{extension}"
