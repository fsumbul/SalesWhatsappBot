"""Company-owned workflow definition; no company routing in the engine."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SelectionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=24)
    description: str | None = Field(default=None, max_length=72)


class SelectionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=24)
    question: str = Field(min_length=1, max_length=300)
    kind: Literal["text", "number", "choice", "attachment"]
    options: list[SelectionOption] = Field(default_factory=list, max_length=100)
    unit: str | None = None
    value_pattern: str | None = None
    integer: bool = False
    allow_custom: bool = False
    allow_unknown: bool = False
    allow_drawing: bool = False
    # Conditions reference a previous step, never executable expressions.
    skip_if: dict[str, list[str]] = Field(default_factory=dict)


class SelectionFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    version: int = Field(ge=1)
    title: str = Field(max_length=80)
    start_phrases: list[str] = Field(min_length=1)
    greeting_phrases: list[str]
    steps: list[SelectionStep] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_steps(self) -> Any:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen or not set(step.skip_if).issubset(seen):
                raise ValueError("Duplicate step or forward/unknown condition reference")
            if len({x.value for x in step.options}) != len(step.options):
                raise ValueError("Duplicate option value")
            seen.add(step.id)
        if self.steps[0].id != "contact_name":
            raise ValueError("First step must collect contact_name")
        return self
