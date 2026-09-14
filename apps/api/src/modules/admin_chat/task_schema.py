"""Bounded, composable administrative tasks and evidence-based completion."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GoalKind = Literal[
    "records",
    "conversation",
    "request",
    "delivery",
    "analytics",
    "capacity",
    "templates",
    "workflow",
    "unsupported",
]
Category = Literal[
    "inbox",
    "requests",
    "quotes",
    "agents",
    "contacts",
    "team",
    "companies",
    "knowledge",
    "versions",
]


class TaskGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: GoalKind
    text: str = Field(min_length=1, max_length=4000)


class TaskAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: int = Field(ge=0, le=7)
    evidence: list[str] = Field(default_factory=list, max_length=12)
    text: str = Field(min_length=1, max_length=1600)


class TaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal[
        "search",
        "conversation",
        "request",
        "delivery",
        "analytics",
        "capacity",
        "templates",
        "prepare",
        "finish",
    ]
    goal: int = Field(default=0, ge=0, le=7)
    category: Category | None = None
    query: str = Field(default="", max_length=160)
    ref: str = Field(default="", max_length=20)
    agent: str = Field(default="", max_length=120)
    page: int = Field(default=1, ge=1, le=10000)
    status: Literal["waiting_review", "in_review", "completed", "cancelled"] | None = None
    today: bool = False
    answers: list[TaskAnswer] = Field(default_factory=list, max_length=8)


class TaskCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: bool
    feedback: str = Field(default="", max_length=600)
