# ruff: noqa: RUF001
"""Pure deterministic reducer. All selectable product data belongs to the definition."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .schema import SelectionFlow, SelectionStep


def normalize(value: str) -> str:
    return value.replace("İ", "i").casefold().translate(str.maketrans("çğıöşü", "cgiosu")).strip()


@dataclass
class State:
    id: str
    definition: SelectionFlow
    answers: dict[str, Any]
    step_index: int = 0
    revision: int = 0
    status: str = "draft"


def visible(state: State, index: int) -> bool:
    step = state.definition.steps[index]
    return not any(
        state.answers.get(key, {}).get("value") in values for key, values in step.skip_if.items()
    )


def advance(state: State) -> None:
    while state.step_index < len(state.definition.steps):
        step = state.definition.steps[state.step_index]
        if visible(state, state.step_index) and step.id not in state.answers:
            break
        state.step_index += 1


def choices(step: SelectionStep) -> list[tuple[str, str]]:
    result = [(x.value, x.label) for x in step.options]
    if step.allow_custom:
        result.append(("@custom", "Farklı ölçü" if step.unit else "Farklı değer"))
    if step.allow_drawing:
        result.append(("@drawing", "Çizimde mevcut"))
    if step.allow_unknown:
        result.append(("@unknown", "Bilmiyorum"))
    return result


def answer_text(answer: dict[str, Any]) -> str:
    if answer.get("status") == "unknown":
        return "Bilmiyorum"
    if answer.get("status") == "drawing":
        return "Çizimde mevcut (inceleme bekliyor)"
    label = str(answer.get("label", answer.get("value", "")))
    unit = answer.get("unit")
    return label + (f" {unit}" if unit and unit not in label else "")


def summary(state: State) -> str:
    lines = ["Seçim özeti:"]
    for step in state.definition.steps:
        if step.id in state.answers:
            lines.append(f"{step.label}: {answer_text(state.answers[step.id])}")
    return "\n".join(lines)


def token(state: State, command: str) -> str:
    return f"sel:{state.id}:{state.revision}:{command}"


def prompt(
    state: State, *, page: int = 0, editing: bool = False, prefix: str = ""
) -> dict[str, Any]:
    done = state.step_index >= len(state.definition.steps)
    if editing:
        options = [
            (f"edit{idx}", step.label)
            for idx, step in enumerate(state.definition.steps)
            if step.id in state.answers
        ]
        body = "Düzeltmek istediğiniz alanı seçin. Sonraki bilgiler yeniden kontrol edilecek."
    elif done:
        # Keep a single Meta message, including the complete confirmed values.
        body = (
            summary(state)
            + "\n\nDosyalar ve bilinmeyen ölçüler ekipçe incelenecek. Nihai uygunluk ve fiyat onayı değildir."
        )
        options = [("confirm", "Onayla"), ("edit", "Düzenle"), ("cancel", "İptal")]
    else:
        step = state.definition.steps[state.step_index]
        body = step.question
        options = [(f"v{idx}", label) for idx, (_, label) in enumerate(choices(step))]
        body += "\n\nGeri, devam veya iptal yazabilirsiniz."
    # All pages have <=10 rows, including navigation controls.
    size = 8
    pages = max(1, (len(options) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    chunk = options[page * size : (page + 1) * size]
    if page:
        chunk.append((f'{"ep" if editing else "p"}{page-1}', "Önceki seçenekler"))
    if page + 1 < pages:
        chunk.append((f'{"ep" if editing else "p"}{page+1}', "Sonraki seçenekler"))
    body = (prefix + "\n\n" if prefix else "") + body
    # Long summaries use text with explicit commands instead of truncating values.
    if len(body) > 1000:
        return {"body": body + "\n\nOnayla, düzenle veya iptal yazın.", "options": []}
    return {
        "body": body,
        "options": [{"id": token(state, cmd), "title": label} for cmd, label in chunk],
    }


def parse_value(step: SelectionStep, text: str) -> dict[str, Any] | None:
    n = normalize(text)
    if n in {"@unknown", "bilmiyorum", "emin degilim"} and step.allow_unknown:
        return {"status": "unknown"}
    if (
        n in {"@drawing", "cizimde mevcut", "teknik resimde mevcut", "teknik cizimde mevcut"}
        and step.allow_drawing
    ):
        return {"status": "drawing"}
    for opt in step.options:
        if n in {normalize(opt.value), normalize(opt.label)}:
            if step.kind == "number" and re.fullmatch(r"\d+(?:\.\d+)?", opt.value):
                return {
                    "status": "value",
                    "value": format(Decimal(opt.value).normalize(), "f"),
                    **({"unit": step.unit} if step.unit else {}),
                }
            return {"status": "value", "value": opt.value, "label": opt.label}
    if step.kind == "number":
        # Accept a single explicit measurement, never numbers buried in questions.
        cleaned = n.replace("ø", "").strip()
        if step.unit and cleaned.endswith(normalize(step.unit)):
            cleaned = cleaned[: -len(step.unit)].strip()
        if not re.fullmatch(r"\d{1,7}(?:[.,]\d{1,3})?", cleaned):
            return None
        try:
            value = Decimal(cleaned.replace(",", "."))
        except InvalidOperation:
            return None
        if value <= 0 or (step.integer and value != value.to_integral_value()):
            return None
        return {
            "status": "value",
            "value": format(value.normalize(), "f"),
            **({"unit": step.unit} if step.unit else {}),
        }
    if step.value_pattern and not re.fullmatch(step.value_pattern, text.strip()):
        return None
    if step.kind == "text" or (step.kind == "choice" and step.allow_custom):
        if "?" in text or any(
            x in n.split()
            for x in ("nedir", "nasil", "neden", "fiyat", "fiyati", "anlat", "stok", "merhaba")
        ):
            return None
        if step.id == "contact_name":
            text = re.sub(r"^(?:adım|adim|ismim|ben)\s+", "", text.strip(), flags=re.I)
            if not re.fullmatch(r"[^\W\d_]+(?:[ \-\'][^\W\d_]+){0,3}", text, flags=re.UNICODE):
                return None
        if 1 <= len(text.strip()) <= 80 and not any(ord(c) < 32 for c in text):
            return {"status": "value", "value": text.strip()}
    return None


def reduce(state: State, message: str) -> tuple[dict[str, Any] | None, bool]:
    """Return response + confirmation flag; None means an informational side question."""
    raw = message.strip()
    command = normalize(raw)
    action = re.search(r"\bsel:([a-f0-9]{32}):(\d+):([a-z0-9]+)", raw)
    if action:
        if action[1] != state.id or int(action[2]) != state.revision:
            return prompt(
                state, prefix="Bu seçim eski bir adıma ait. Güncel sorudan devam edelim."
            ), False
        command = action[3]
    if command in {"iptal", "cancel"}:
        state.status = "cancelled"
        state.revision += 1
        return {
            "body": "Seçim taslağınız iptal edildi. Yeni seçim için: "
            + state.definition.start_phrases[0],
            "options": [],
        }, False
    if command in {"geri", "back"}:
        previous = [i for i in range(state.step_index) if visible(state, i)]
        if previous:
            state.step_index = previous[-1]
            state.revision += 1
        return prompt(state), False
    if command in {"devam", "continue"}:
        return prompt(state), False
    if command in {"duzenle", "edit"}:
        return prompt(state, editing=True), False
    if action and re.fullmatch(r"ep\d+", command):
        return prompt(state, page=int(command[2:]), editing=True), False
    if action and re.fullmatch(r"p\d+", command):
        return prompt(state, page=int(command[1:])), False
    if action and re.fullmatch(r"edit\d+", command):
        idx = int(command[4:])
        if idx < len(state.definition.steps) and state.definition.steps[idx].id in state.answers:
            state.step_index = idx
            state.revision += 1
        return prompt(state), False
    if state.step_index >= len(state.definition.steps):
        if command in {"onayla", "confirm"}:
            state.status = "waiting_review"
            state.revision += 1
            return {
                "body": "Talebiniz onayınızla kaydedildi. Teknik/satış ekibimiz ölçüleri ve dosyaları inceleyecek. Bu kayıt fiyat veya uygunluk onayı değildir.",
                "options": [],
            }, True
        return prompt(state), False
    step = state.definition.steps[state.step_index]
    if action and re.fullmatch(r"v\d+", command):
        idx = int(command[1:])
        opts = choices(step)
        if idx >= len(opts):
            return prompt(state), False
        raw = opts[idx][0]
        if raw == "@custom":
            return prompt(state, prefix="Farklı değeri birimiyle yazabilirsiniz."), False
    if (
        normalize(raw) in {"@custom", "farkli olcu", "farkli deger", "ozel olcu"}
        and step.allow_custom
    ):
        return prompt(state, prefix="Farklı değeri birimiyle yazabilirsiniz."), False
    if command in {"onayla", "confirm"}:
        return prompt(state, prefix="Onaylamadan önce bu alanı tamamlayalım."), False
    value = parse_value(step, raw)
    if value is None:
        if (
            action
            or normalize(raw) in {"bilmiyorum", "cizimde mevcut"}
            or re.fullmatch(r"[-+\d.,\s]+(?:mm|kg|m/s)?", raw)
        ):
            return prompt(state, prefix="Bu alan için geçerli bir değer seçin veya yazın."), False
        return None, False
    state.answers = deepcopy(state.answers)
    # Conservative dependency invalidation: a changed earlier answer requires recheck.
    old = state.answers.get(step.id)
    if old is not None and old != value:
        for later in state.definition.steps[state.step_index + 1 :]:
            state.answers.pop(later.id, None)
    state.answers[step.id] = value
    state.step_index += 1
    state.revision += 1
    advance(state)
    return prompt(state), False
