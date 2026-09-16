# ruff: noqa: RUF001
"""OCR / layout chain for scanned PDFs (NIM plan WP2)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import respx
from PIL import Image, ImageDraw

from src.core.config import Settings
from src.integrations.nim import NimHttp, NimUnavailableError
from src.integrations.nim.ocr import Box, NimLayoutClient, NimOcrClient, parse_box
from src.modules.knowledge.extract import TextUnit, extract_document
from src.modules.knowledge.ocr import (
    NimDocumentOcr,
    OcrRegion,
    assemble_table,
    bbox_locator,
    build_document_ocr,
    crop_image,
    ocr_pdf_pages,
    pages_without_text,
    regions_to_units,
    render_pdf_page,
    words_to_lines,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "nim"
_STRONG_SECRET = "fK9!vT2@qL7#sN4$wR8%mC5^xP1&zD6*"


def _fixture(name: str) -> dict[str, Any]:
    payload = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    return dict(payload)


def image_pdf(pages: int = 2, size: tuple[int, int] = (800, 600)) -> bytes:
    """A scanned-looking PDF: raster pages only, no text layer."""

    images = []
    for number in range(pages):
        image = Image.new("RGB", size, "white")
        ImageDraw.Draw(image).text((40, 40), f"Sayfa {number + 1}", fill="black")
        images.append(image)
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "production",
        "app_debug": False,
        "app_secret_key": _STRONG_SECRET,
        "database_url": "postgresql+asyncpg://app:pass@db/app",
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/1",
        "celery_result_backend": "redis://redis:6379/2",
        "whatsapp_app_secret": "s",
        "whatsapp_access_token": "t",
        "whatsapp_verify_token": "v",
        "whatsapp_phone_number_id": "p",
        "whatsapp_business_account_id": "w",
        "llm_provider": "ollama",
        "llm_model": "qwen3:8b",
        "llm_base_url": "http://127.0.0.1:11434/v1",
    }
    values.update(overrides)
    return Settings(**values)


# --- rasterization ---------------------------------------------------------------


def test_image_only_pdf_has_no_text_layer_and_renders_to_jpeg() -> None:
    data = image_pdf(pages=2)
    assert extract_document("tarama.pdf", "application/pdf", data) == []
    assert pages_without_text(data, [], min_chars=40) == [1, 2]
    with_text = [TextUnit(locator="tarama.pdf#page=1", text="x" * 100, meta={"page": 1})]
    assert pages_without_text(data, with_text, min_chars=40) == [2]

    jpeg, width, height = render_pdf_page(data, 2, dpi=72)
    assert jpeg.startswith(b"\xff\xd8") and (width, height) == (800, 600)
    crop, crop_w, crop_h = crop_image(jpeg, (0.0, 0.0, 0.5, 0.5))
    assert crop.startswith(b"\xff\xd8") and 380 <= crop_w <= 420 and 280 <= crop_h <= 320
    assert crop_image(jpeg, (0.5, 0.5, 0.501, 0.501)) == (b"", 0, 0)


# --- assembly --------------------------------------------------------------------------


def test_words_form_lines_and_tables_form_grids() -> None:
    words = [
        Box(0.02, 0.05, 0.20, 0.15, text="Döküm"),
        Box(0.22, 0.05, 0.40, 0.15, text="kasnak"),
        Box(0.02, 0.30, 0.20, 0.40, text="GG-25"),
        Box(0.32, 0.30, 0.55, 0.40, text="dökümdür."),
        Box(0.22, 0.30, 0.30, 0.40, text="pik"),
    ]
    lines = words_to_lines(words)
    assert [[w.text for w in line] for line in lines] == [["Döküm", "kasnak"], ["GG-25", "pik", "dökümdür."]]

    table_words = [
        Box(0.10, 0.10, 0.30, 0.25, text="Çap"),
        Box(0.60, 0.10, 0.90, 0.25, text="Halat"),
        Box(0.10, 0.42, 0.35, 0.58, text="320 mm"),
        Box(0.60, 0.42, 0.80, 0.58, text="4x8"),
    ]
    structure = [
        Box(0.0, 0.0, 1.0, 0.33, kind="row"),
        Box(0.0, 0.34, 1.0, 1.0, kind="row"),
        Box(0.0, 0.0, 0.49, 1.0, kind="column"),
        Box(0.5, 0.0, 1.0, 1.0, kind="column"),
    ]
    assert assemble_table(table_words, structure) == (("Çap", "Halat"), ("320 mm", "4x8"))
    # No structure at all: OCR lines become single-column rows, never dropped.
    assert assemble_table(table_words, []) == (("Çap Halat",), ("320 mm 4x8",))


def test_regions_merge_text_and_render_tables_with_bbox_locators() -> None:
    regions = [
        OcrRegion("table", (0.1, 0.45, 0.9, 0.8), "", 0.93, (("Çap", "Halat"), ("320 mm", "4x8"), ("400 mm", "5x8"))),
        OcrRegion("text", (0.1, 0.12, 0.9, 0.4), "Döküm kasnak GG-25 pik dökümdür.", 0.95),
        OcrRegion("title", (0.1, 0.05, 0.9, 0.1), "Asansör kasnakları", 0.97),
    ]
    units = regions_to_units("katalog.pdf", 2, regions)
    assert [u.meta["kind"] for u in units] == ["text", "table"]
    text = units[0]
    assert text.locator == "katalog.pdf#page=2&bbox=0.1000,0.0500,0.9000,0.4000"
    assert text.title == "Asansör kasnakları" and text.text.startswith("Asansör kasnakları\n\nDöküm kasnak")
    assert text.meta["page"] == 2 and text.meta["ocr"] is True and text.meta["confidence"] == 0.95
    assert [b["kind"] for b in text.meta["blocks"]] == ["title", "text"]
    table = units[1]
    assert table.locator == "katalog.pdf#page=2&bbox=0.1000,0.4500,0.9000,0.8000&rows=2-3"
    assert table.text == "Tablo: sayfa 2 tablo 1\nÇap: 320 mm | Halat: 4x8\nÇap: 400 mm | Halat: 5x8"
    assert table.meta["kind"] == "table" and table.meta["rows"] == [2, 3]
    assert bbox_locator("a.pdf", 1, (0.0, 0.0, 1.0, 1.0)) == "a.pdf#page=1&bbox=0.0000,0.0000,1.0000,1.0000"
    # Tiny fragments are dropped, long runs are split around ~1400 characters.
    assert regions_to_units("a.pdf", 1, [OcrRegion("text", (0, 0, 1, 1), "kısa", 1.0)]) == []
    long_regions = [OcrRegion("text", (0, i / 10, 1, (i + 1) / 10), "kelime " * 150, 1.0) for i in range(3)]
    assert len(regions_to_units("a.pdf", 1, long_regions)) == 3


def test_parse_box_accepts_pixel_and_polygon_shapes() -> None:
    pixel = parse_box({"type": "table", "x_min": 80, "y_min": 60, "x_max": 720, "y_max": 480, "confidence": 0.9}, width=800, height=600)
    assert pixel is not None and (pixel.x0, pixel.y0, pixel.x1, pixel.y1) == (0.1, 0.1, 0.9, 0.8)
    polygon = parse_box({"text_prediction": {"text": "Çap", "confidence": 0.5}, "bounding_box": {"points": [{"x": 10, "y": 10}, {"x": 90, "y": 10}, {"x": 90, "y": 30}, {"x": 10, "y": 30}]}}, width=100, height=100)
    assert polygon is not None and polygon.text == "Çap" and polygon.confidence == 0.5
    assert (polygon.x0, polygon.y0, polygon.x1, polygon.y1) == (0.1, 0.1, 0.9, 0.3)
    assert parse_box({"type": "text"}, width=1, height=1) is None


# --- NIM pipeline ------------------------------------------------------------------------


def _ocr_side_effect(request: httpx.Request) -> httpx.Response:
    # Text/title crops get the text fixture, the (third) table crop the table fixture.
    _ocr_side_effect.calls += 1  # type: ignore[attr-defined]
    name = "ocr_table_region.json" if _ocr_side_effect.calls == 3 else "ocr_text_region.json"  # type: ignore[attr-defined]
    return httpx.Response(200, json=_fixture(name))


@respx.mock
async def test_nim_pipeline_reads_text_regions_and_tables() -> None:
    _ocr_side_effect.calls = 0  # type: ignore[attr-defined]
    respx.post("http://gpu:8021/v1/page-elements").mock(return_value=httpx.Response(200, json=_fixture("layout_page_elements.json")))
    respx.post("http://gpu:8021/v1/table-structure").mock(return_value=httpx.Response(200, json=_fixture("layout_table_structure.json")))
    ocr_route = respx.post("http://gpu:8020/v1/infer").mock(side_effect=_ocr_side_effect)
    pipeline = NimDocumentOcr(NimOcrClient(NimHttp("http://gpu:8020")), NimLayoutClient(NimHttp("http://gpu:8021")), min_confidence=0.3)
    image, _w, _h = render_pdf_page(image_pdf(1), 1, dpi=72)

    regions = await pipeline.read_page(image, "image/jpeg")

    assert [r.kind for r in regions] == ["title", "text", "table"]  # chart skipped
    assert regions[0].text == "Döküm kasnak\nGG-25 pik dökümdür."
    assert "??" not in regions[1].text  # below min confidence
    assert regions[2].cells == (("Çap", "Halat"), ("320 mm", "4x8"), ("400 mm", "5x8"))
    assert ocr_route.call_count == 3
    body = json.loads(ocr_route.calls.last.request.content)
    assert body["input"][0]["type"] == "image_url" and body["input"][0]["url"].startswith("data:image/jpeg;base64,")
    assert pipeline.describe == {"ocr_model": "nemotron-ocr-v2", "layout": "page-elements+table-structure"}

    units = regions_to_units("katalog.pdf", 1, regions)
    assert len(units) == 2 and units[1].text.startswith("Tablo: sayfa 1 tablo 1\nÇap: 320 mm | Halat: 4x8")


@respx.mock
async def test_nim_pipeline_without_or_with_failing_layout_reads_the_whole_page() -> None:
    respx.post("http://gpu:8020/v1/infer").mock(return_value=httpx.Response(200, json=_fixture("ocr_text_region.json")))
    image, _w, _h = render_pdf_page(image_pdf(1), 1, dpi=72)

    no_layout = NimDocumentOcr(NimOcrClient(NimHttp("http://gpu:8020")), None)
    regions = await no_layout.read_page(image, "image/jpeg")
    assert [(r.kind, r.bbox) for r in regions] == [("text", (0.0, 0.0, 1.0, 1.0))]
    assert no_layout.describe["layout"] == "none"

    respx.post("http://gpu:8021/v1/page-elements").mock(return_value=httpx.Response(503, json={}))
    failing = NimDocumentOcr(NimOcrClient(NimHttp("http://gpu:8020")), NimLayoutClient(NimHttp("http://gpu:8021")))
    regions = await failing.read_page(image, "image/jpeg")
    assert [(r.kind, r.bbox) for r in regions] == [("text", (0.0, 0.0, 1.0, 1.0))]


class _FailingOcr:
    available = True
    describe = {"ocr_model": "fake"}  # noqa: RUF012

    async def read_page(self, image: bytes, mime_type: str) -> list[OcrRegion]:
        raise NimUnavailableError("down")


class _StaticOcr:
    available = True
    describe = {"ocr_model": "fake"}  # noqa: RUF012

    def __init__(self) -> None:
        self.pages = 0

    async def read_page(self, image: bytes, mime_type: str) -> list[OcrRegion]:
        self.pages += 1
        return [OcrRegion("text", (0.1, 0.1, 0.9, 0.5), "Artı Kasnak 1995'ten beri kasnak üretir ve ihracat yapar.", 0.9)]


async def test_ocr_pdf_pages_reports_failures_and_page_caps_without_text() -> None:
    data = image_pdf(2)
    units, report = await ocr_pdf_pages("tarama.pdf", data, [1, 2], _FailingOcr(), dpi=72)
    assert units == [] and report["skipped"] == [1, 2]
    assert report["errors"] == {"1": "NimUnavailableError", "2": "NimUnavailableError"}
    assert report["ocr_model"] == "fake" and report["dpi"] == 72

    ocr = _StaticOcr()
    units, report = await ocr_pdf_pages("tarama.pdf", data, [1, 2], ocr, dpi=72, max_pages=1)
    assert ocr.pages == 1 and len(units) == 1
    assert units[0].locator == "tarama.pdf#page=1&bbox=0.1000,0.1000,0.9000,0.5000"
    assert report["pages"] == [{"page": 1, "regions": 1, "units": 1}] and report["skipped"] == [2]
    assert "1995" not in json.dumps(report)


# --- settings ---------------------------------------------------------------------------


def test_build_document_ocr_from_settings_and_boundary_gate() -> None:
    assert build_document_ocr(_settings()) is None
    assert build_document_ocr(_settings(app_env="development", knowledge_ocr_enabled=True)) is None
    enabled = _settings(
        knowledge_ocr_enabled=True,
        knowledge_ocr_base_url="http://10.0.0.5:8020",
        knowledge_layout_base_url="http://10.0.0.5:8021/v1",
        knowledge_ocr_path="/v1/ocr",
    )
    pipeline = build_document_ocr(enabled)
    assert isinstance(pipeline, NimDocumentOcr) and pipeline.layout is not None
    assert pipeline.ocr.path == "/v1/ocr"
    assert [e.role for e in enabled.nim_endpoints()] == ["knowledge.ocr", "knowledge.layout"]
    assert enabled.production_runtime_errors() == []

    missing = _settings(knowledge_ocr_enabled=True)
    assert "KNOWLEDGE_OCR_BASE_URL is required" in missing.production_runtime_errors()
    assert [e.role for e in missing.nim_endpoints()] == ["knowledge.ocr"]
    only_ocr = build_document_ocr(_settings(knowledge_ocr_enabled=True, knowledge_ocr_base_url="http://10.0.0.5:8020"))
    assert isinstance(only_ocr, NimDocumentOcr) and only_ocr.layout is None
