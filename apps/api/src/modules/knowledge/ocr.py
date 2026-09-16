"""OCR / layout chain for scanned PDF pages (plan WP2).

Pages whose text layer is (almost) empty are rasterized locally with
pypdfium2 and read by the operator's NeMo Retriever containers. The output is
ordinary ``TextUnit`` objects whose locator pins the page and the normalized
bounding box (``katalog.pdf#page=3&bbox=0.1000,0.1200,0.9000,0.4000``), so a
candidate fact extracted from a scan is cited exactly like one from a text
layer. Tables become the same ``Tablo:`` rendering XLSX/CSV sheets use.

Everything that decides what a page *means* stays deterministic: the OCR
adapters only return words and boxes; this module assembles them.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal, Protocol

import structlog

from src.core.config import Settings, get_settings
from src.integrations.nim.ocr import _SKIP_KINDS, _TABLE_KINDS, _TEXT_KINDS, Box

from .chunking import normalize_whitespace
from .extract import TextUnit, table_rows_to_units

logger = structlog.get_logger(__name__)

_MAX_RENDER_BYTES = 4 * 1024 * 1024
_MERGE_TARGET_CHARS = 1400
_MIN_UNIT_CHARS = 20
_CROP_PADDING = 0.005

RegionKind = Literal["text", "title", "table"]


@dataclass(frozen=True)
class OcrRegion:
    kind: RegionKind
    bbox: tuple[float, float, float, float]  # normalized x0, y0, x1, y1
    text: str
    confidence: float = 1.0
    cells: tuple[tuple[str, ...], ...] | None = None  # tables: header row first


class DocumentOcr(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def describe(self) -> dict[str, str]: ...

    async def read_page(self, image: bytes, mime_type: str) -> list[OcrRegion]: ...


class NullDocumentOcr:
    available = False

    @property
    def describe(self) -> dict[str, str]:
        return {}

    async def read_page(self, image: bytes, mime_type: str) -> list[OcrRegion]:
        return []


# --- rasterization ---------------------------------------------------------------


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(data))


def pages_without_text(data: bytes, units: Sequence[TextUnit], *, min_chars: int) -> list[int]:
    """1-based page numbers whose extracted text layer is shorter than ``min_chars``."""

    have: dict[int, int] = {}
    for unit in units:
        page = unit.meta.get("page")
        if isinstance(page, int):
            have[page] = have.get(page, 0) + len(unit.text)
    return [page for page in range(1, pdf_page_count(data) + 1) if have.get(page, 0) < min_chars]


def render_pdf_page(data: bytes, page_number: int, *, dpi: int = 150) -> tuple[bytes, int, int]:
    """JPEG bytes and pixel size of one page (1-based). Shrinks to stay under 4 MB."""

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    page = document[page_number - 1]
    scale = max(0.5, dpi / 72)
    image = page.render(scale=scale).to_pil().convert("RGB")
    quality = 85
    while True:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        output = buffer.getvalue()
        if len(output) <= _MAX_RENDER_BYTES or (quality <= 50 and max(image.size) <= 1200):
            return output, image.size[0], image.size[1]
        if quality > 50:
            quality -= 15
        else:
            image = image.resize((max(1, image.size[0] // 2), max(1, image.size[1] // 2)))


def crop_image(image: bytes, bbox: tuple[float, float, float, float]) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(image)) as source:
        width, height = source.size
        if (bbox[2] - bbox[0]) * width < 4 or (bbox[3] - bbox[1]) * height < 4:
            return b"", 0, 0
        x0 = int(max(0.0, bbox[0] - _CROP_PADDING) * width)
        y0 = int(max(0.0, bbox[1] - _CROP_PADDING) * height)
        x1 = int(min(1.0, bbox[2] + _CROP_PADDING) * width)
        y1 = int(min(1.0, bbox[3] + _CROP_PADDING) * height)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return b"", 0, 0
        crop = source.crop((x0, y0, x1, y1)).convert("RGB")
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue(), crop.size[0], crop.size[1]


# --- word assembly -------------------------------------------------------------------


def words_to_lines(words: Sequence[Box]) -> list[list[Box]]:
    """Group word boxes into reading-order lines by vertical overlap."""

    ordered = sorted((w for w in words if w.text.strip()), key=lambda b: (b.cy, b.x0))
    if not ordered:
        return []
    tolerance = median(b.height for b in ordered) * 0.6 or 0.01
    lines: list[list[Box]] = []
    for word in ordered:
        if lines and abs(word.cy - lines[-1][-1].cy) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda b: b.x0) for line in lines]


def lines_to_text(lines: Sequence[Sequence[Box]]) -> str:
    return normalize_whitespace("\n".join(" ".join(w.text.strip() for w in line) for line in lines))


def _spans(boxes: Sequence[Box], axis: Literal["x", "y"]) -> list[tuple[float, float]]:
    spans = sorted(((b.x0, b.x1) if axis == "x" else (b.y0, b.y1)) for b in boxes)
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def assemble_table(words: Sequence[Box], structure: Sequence[Box]) -> tuple[tuple[str, ...], ...]:
    """Place OCR words into the row x column grid the structure model detected.

    Falls back to OCR line grouping (single column) when rows or columns are
    missing, so a table is never silently dropped.
    """

    rows = _spans([b for b in structure if b.kind == "row"], "y")
    columns = _spans([b for b in structure if b.kind == "column"], "x")
    if not rows:
        rows = [
            (min(w.y0 for w in line), max(w.y1 for w in line)) for line in words_to_lines(words)
        ]
    if not rows:
        return ()
    if not columns:
        columns = [(0.0, 1.0)]

    def _index(spans: Sequence[tuple[float, float]], value: float) -> int:
        for i, (start, end) in enumerate(spans):
            if start <= value <= end:
                return i
        return min(
            range(len(spans)), key=lambda i: min(abs(spans[i][0] - value), abs(spans[i][1] - value))
        )

    grid: list[list[list[Box]]] = [[[] for _ in columns] for _ in rows]
    for word in words:
        if not word.text.strip():
            continue
        grid[_index(rows, word.cy)][_index(columns, word.cx)].append(word)
    return tuple(
        tuple(
            " ".join(w.text.strip() for w in sorted(cell, key=lambda b: (b.cy, b.x0)))
            for cell in row
        )
        for row in grid
    )


# --- regions → TextUnit ----------------------------------------------------------------


def bbox_locator(filename: str, page: int, bbox: tuple[float, float, float, float]) -> str:
    return f"{filename}#page={page}&bbox={bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}"


def _row_locator(base: str) -> Callable[[int, int], str]:
    def locator(first: int, last: int) -> str:
        return f"{base}&rows={first}-{last}"

    return locator


def _union(boxes: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def regions_to_units(filename: str, page: int, regions: Sequence[OcrRegion]) -> list[TextUnit]:
    """Adjacent text regions merge up to ~1400 chars; every table is its own unit."""

    ordered = sorted(regions, key=lambda r: (round(r.bbox[1], 2), r.bbox[0]))
    units: list[TextUnit] = []
    pending: list[OcrRegion] = []
    table_index = 0

    def _flush() -> None:
        nonlocal pending
        if not pending:
            return
        text = normalize_whitespace("\n\n".join(r.text for r in pending))
        if len(text) >= _MIN_UNIT_CHARS:
            bbox = _union([r.bbox for r in pending])
            title = next((r.text[:300] for r in pending if r.kind == "title"), None)
            units.append(
                TextUnit(
                    locator=bbox_locator(filename, page, bbox),
                    text=text,
                    title=title,
                    meta={
                        "page": page,
                        "bbox": [round(v, 4) for v in bbox],
                        "ocr": True,
                        "kind": "text",
                        "confidence": round(min(r.confidence for r in pending), 3),
                        "blocks": [
                            {"kind": r.kind, "bbox": [round(v, 4) for v in r.bbox]} for r in pending
                        ],
                    },
                )
            )
        pending = []

    for region in ordered:
        if region.kind == "table" and region.cells:
            _flush()
            table_index += 1
            rows = [list(row) for row in region.cells]
            label = f"sayfa {page} tablo {table_index}"
            base = bbox_locator(filename, page, region.bbox)
            units.extend(
                table_rows_to_units(
                    filename,
                    label,
                    rows,
                    locator_for=_row_locator(base),
                    extra_meta={
                        "page": page,
                        "bbox": [round(v, 4) for v in region.bbox],
                        "ocr": True,
                        "kind": "table",
                        "confidence": round(region.confidence, 3),
                    },
                )
            )
            continue
        if not region.text.strip():
            continue
        if pending and sum(len(r.text) for r in pending) + len(region.text) > _MERGE_TARGET_CHARS:
            _flush()
        pending.append(region)
    _flush()
    return units


# --- NIM pipeline -------------------------------------------------------------------------


class NimDocumentOcr:
    """page-elements → OCR (text) / table-structure + OCR (tables); whole page without layout."""

    available = True

    def __init__(self, ocr: Any, layout: Any | None, *, min_confidence: float = 0.3) -> None:
        self.ocr = ocr
        self.layout = layout
        self.min_confidence = min_confidence

    @property
    def describe(self) -> dict[str, str]:
        return {
            "ocr_model": str(getattr(self.ocr, "model", "")),
            "layout": "page-elements+table-structure" if self.layout is not None else "none",
        }

    async def read_page(self, image: bytes, mime_type: str) -> list[OcrRegion]:
        from PIL import Image

        with Image.open(io.BytesIO(image)) as source:
            width, height = source.size
        elements: list[Box] = []
        if self.layout is not None:
            try:
                elements = await self.layout.page_elements(
                    image, mime_type, width=width, height=height
                )
            except Exception as exc:  # layout is an optimization; whole-page OCR still works
                logger.warning("knowledge.ocr.layout_failed", error=type(exc).__name__)
                elements = []
        if not elements:
            elements = [Box(0.0, 0.0, 1.0, 1.0, kind="text")]

        regions: list[OcrRegion] = []
        for element in sorted(elements, key=lambda b: (b.y0, b.x0)):
            kind = element.kind
            if kind in _SKIP_KINDS or (kind not in _TEXT_KINDS and kind not in _TABLE_KINDS):
                continue
            bbox = (element.x0, element.y0, element.x1, element.y1)
            crop, crop_w, crop_h = crop_image(image, bbox)
            if not crop:
                continue
            words = [
                w
                for w in await self.ocr.read(crop, "image/jpeg", width=crop_w, height=crop_h)
                if w.confidence >= self.min_confidence and w.text.strip()
            ]
            if not words:
                continue
            confidence = min(w.confidence for w in words)
            if kind in _TABLE_KINDS:
                structure: list[Box] = []
                if self.layout is not None:
                    try:
                        structure = await self.layout.table_structure(
                            crop, "image/jpeg", width=crop_w, height=crop_h
                        )
                    except Exception as exc:
                        logger.warning(
                            "knowledge.ocr.table_structure_failed", error=type(exc).__name__
                        )
                cells = assemble_table(words, structure)
                text = "\n".join(" | ".join(c for c in row if c) for row in cells)
                regions.append(OcrRegion("table", bbox, text, confidence, cells))
            else:
                text = lines_to_text(words_to_lines(words))
                regions.append(
                    OcrRegion("title" if kind == "title" else "text", bbox, text, confidence)
                )
        return regions


async def ocr_pdf_pages(
    filename: str,
    data: bytes,
    pages: Sequence[int],
    ocr: DocumentOcr,
    *,
    dpi: int = 150,
    max_pages: int = 60,
) -> tuple[list[TextUnit], dict[str, Any]]:
    """OCR the given pages; the report never contains page text."""

    units: list[TextUnit] = []
    report: dict[str, Any] = {"pages": [], "skipped": [], "errors": {}, "dpi": dpi, **ocr.describe}
    for page in pages:
        if len(report["pages"]) >= max_pages:
            report["skipped"].append(page)
            continue
        try:
            image, _width, _height = await asyncio.to_thread(render_pdf_page, data, page, dpi=dpi)
            regions = await ocr.read_page(image, "image/jpeg")
        except Exception as exc:
            report["skipped"].append(page)
            report["errors"][str(page)] = type(exc).__name__
            logger.warning("knowledge.ocr.page_failed", page=page, error=type(exc).__name__)
            continue
        page_units = regions_to_units(filename, page, regions)
        units.extend(page_units)
        report["pages"].append({"page": page, "regions": len(regions), "units": len(page_units)})
    return units, report


def build_document_ocr(settings: Settings | None = None) -> DocumentOcr | None:
    """Settings → OCR pipeline; ``None`` keeps ingestion text-layer only."""

    s = settings or get_settings()
    if not s.knowledge_ocr_enabled or not s.knowledge_ocr_base_url.strip():
        return None
    from src.integrations.nim import NimHttp
    from src.integrations.nim.ocr import NimLayoutClient, NimOcrClient

    ocr = NimOcrClient(
        NimHttp(
            s.knowledge_ocr_base_url,
            api_key=s.knowledge_ocr_api_key or s.nim_api_key,
            timeout_seconds=s.nim_timeout_seconds,
            name="knowledge.ocr",
        ),
        path=s.knowledge_ocr_path,
        model=s.knowledge_ocr_model,
    )
    layout = None
    if s.knowledge_layout_base_url.strip():
        layout = NimLayoutClient(
            NimHttp(
                s.knowledge_layout_base_url,
                api_key=s.knowledge_layout_api_key or s.nim_api_key,
                timeout_seconds=s.nim_timeout_seconds,
                name="knowledge.layout",
            )
        )
    return NimDocumentOcr(ocr, layout, min_confidence=s.knowledge_ocr_min_confidence)
