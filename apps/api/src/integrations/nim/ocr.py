"""NeMo Retriever extraction NIMs: page layout, table structure and OCR.

Verified from NVIDIA documentation (plan §2): the object-detection container
``nvcr.io/nim/nvidia/nemotron-object-detection:2.0`` serves
``POST /v1/page-elements`` (nvidia/nemotron-page-elements-v3) and
``POST /v1/table-structure`` (nvidia/nemotron-table-structure-v1) with the body
``{"input": [{"type": "image_url", "url": "data:image/jpeg;base64,..."}]}``.
The response shapes and the OCR container's route are **unverified**: the
parsers below accept the documented NeMo Retriever variants (``data[]`` with
``bounding_boxes[]`` / ``text_detections[]``, normalized or pixel coordinates)
and must be pinned against ``GET /v1/openapi.json`` of the running container
(``tests/fixtures/nim/layout_*.json``, ``ocr_*.json``).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from .http import NimError, NimHttp

_TEXT_KINDS = {"text", "title", "caption", "list", "paragraph", "section_header"}
_TABLE_KINDS = {"table"}
_SKIP_KINDS = {
    "chart",
    "infographic",
    "image",
    "figure",
    "header",
    "footer",
    "page_number",
    "picture",
}


@dataclass(frozen=True)
class Box:
    """Normalized [0, 1] bounding box, origin top-left."""

    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = ""
    confidence: float = 1.0
    text: str = ""

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


def image_input(image: bytes, mime_type: str) -> dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    return {"input": [{"type": "image_url", "url": f"data:{mime_type};base64,{encoded}"}]}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalize(
    x0: float, y0: float, x1: float, y1: float, width: int, height: int
) -> tuple[float, float, float, float]:
    if max(x0, y0, x1, y1) > 1.0 and width > 0 and height > 0:
        x0, x1 = x0 / width, x1 / width
        y0, y1 = y0 / height, y1 / height
    x0, x1 = sorted((_clamp(x0), _clamp(x1)))
    y0, y1 = sorted((_clamp(y0), _clamp(y1)))
    return x0, y0, x1, y1


def parse_box(item: dict[str, Any], *, width: int, height: int) -> Box | None:
    """Accept ``x_min..y_max``, ``bbox: [x0,y0,x1,y1]`` or polygon ``points``."""

    coords: tuple[float, float, float, float] | None = None
    keys = ("x_min", "y_min", "x_max", "y_max")
    if all(key in item for key in keys):
        values = [_num(item[key]) for key in keys]
        if all(v is not None for v in values):
            coords = (values[0] or 0.0, values[1] or 0.0, values[2] or 0.0, values[3] or 0.0)
    if coords is None:
        raw = item.get("bbox") or item.get("box")
        if isinstance(raw, list) and len(raw) == 4:
            values = [_num(v) for v in raw]
            if all(v is not None for v in values):
                coords = (values[0] or 0.0, values[1] or 0.0, values[2] or 0.0, values[3] or 0.0)
    if coords is None:
        polygon = item.get("bounding_box") if isinstance(item.get("bounding_box"), dict) else item
        points = polygon.get("points") if isinstance(polygon, dict) else None
        if isinstance(points, list) and points:
            xs = [_num(p.get("x")) for p in points if isinstance(p, dict)]
            ys = [_num(p.get("y")) for p in points if isinstance(p, dict)]
            if xs and ys and all(v is not None for v in xs + ys):
                fx = [v for v in xs if v is not None]
                fy = [v for v in ys if v is not None]
                coords = (min(fx), min(fy), max(fx), max(fy))
    if coords is None:
        return None
    x0, y0, x1, y1 = _normalize(*coords, width=width, height=height)
    kind = str(item.get("type") or item.get("label") or item.get("class") or "").lower()
    confidence = _num(item.get("confidence"))
    if confidence is None:
        prediction = item.get("text_prediction")
        confidence = _num(prediction.get("confidence")) if isinstance(prediction, dict) else None
    text = ""
    prediction = item.get("text_prediction")
    if isinstance(prediction, dict) and isinstance(prediction.get("text"), str):
        text = prediction["text"]
    elif isinstance(item.get("text"), str):
        text = str(item["text"])
    return Box(
        x0, y0, x1, y1, kind=kind, confidence=1.0 if confidence is None else confidence, text=text
    )


def _first_data_item(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("data")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    if isinstance(data.get("bounding_boxes"), list) or isinstance(
        data.get("text_detections"), list
    ):
        return data
    raise NimError("extraction response has no data item")


def parse_boxes(data: dict[str, Any], *, width: int, height: int, key: str) -> list[Box]:
    item = _first_data_item(data)
    raw = item.get(key)
    if raw is None:
        # Some builds group boxes per class: {"table": [...], "chart": [...]}
        grouped = [
            {**box, "type": kind}
            for kind, boxes in item.items()
            if isinstance(boxes, list)
            for box in boxes
            if isinstance(box, dict)
        ]
        raw = grouped
    if not isinstance(raw, list):
        raise NimError(f"extraction response has no {key}")
    boxes: list[Box] = []
    for entry in raw:
        if isinstance(entry, dict):
            box = parse_box(entry, width=width, height=height)
            if box is not None:
                boxes.append(box)
    return boxes


class NimLayoutClient:
    """Page elements + table structure (one container, two routes)."""

    def __init__(self, http: NimHttp) -> None:
        self.http = http

    async def page_elements(
        self, image: bytes, mime_type: str, *, width: int, height: int
    ) -> list[Box]:
        data = await self.http.post_json("/v1/page-elements", image_input(image, mime_type))
        return parse_boxes(data, width=width, height=height, key="bounding_boxes")

    async def table_structure(
        self, image: bytes, mime_type: str, *, width: int, height: int
    ) -> list[Box]:
        data = await self.http.post_json("/v1/table-structure", image_input(image, mime_type))
        return parse_boxes(data, width=width, height=height, key="bounding_boxes")


class NimOcrClient:
    """Text detections with boxes (nemotron-ocr-v2 / paddleocr NIM)."""

    def __init__(
        self, http: NimHttp, *, path: str = "/v1/infer", model: str = "nemotron-ocr-v2"
    ) -> None:
        self.http = http
        self.path = path
        self.model = model

    async def read(self, image: bytes, mime_type: str, *, width: int, height: int) -> list[Box]:
        data = await self.http.post_json(self.path, image_input(image, mime_type))
        return parse_boxes(data, width=width, height=height, key="text_detections")


__all__ = [
    "_SKIP_KINDS",
    "_TABLE_KINDS",
    "_TEXT_KINDS",
    "Box",
    "NimLayoutClient",
    "NimOcrClient",
    "image_input",
    "parse_box",
    "parse_boxes",
]
