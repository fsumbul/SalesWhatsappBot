"""Versioned server-owned UI contract. No model-generated UI or routes."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WorkflowStatus = Literal[
    "awaiting_input", "ready", "running", "completed", "failed", "paused", "cancelled"
]
WorkflowKind = Literal[
    "create_template",
    "owner_invite",
    "rollback",
    "request_update",
    "conversation",
    "reply",
    "resume_bot",
    "outreach",
    "member",
    "records",
    "person",
    "contact",
    "invite",
    "create_company",
    "create_agent",
    "configure",
    "publish",
    "test",
]


class WorkflowField(BaseModel):
    key: str
    label: str
    control: Literal["text", "email", "tel", "select", "textarea", "file", "checkbox"] = "text"
    required: bool = False
    options: dict[str, str] = Field(default_factory=dict)


class WorkflowView(BaseModel):
    schema_version: Literal[1] = 1
    type: Literal["workflow"] = "workflow"
    id: UUID
    session_id: UUID
    anchor_sequence: int = Field(default=0, ge=0)
    kind: WorkflowKind
    revision: int
    title: str
    step: str
    steps: list[str]
    status: WorkflowStatus
    fields: dict[str, str]
    controls: list[WorkflowField]
    errors: dict[str, str]
    primary_action: str | None
    primary_label: str | None
    records: list[dict[str, Any]] = Field(default_factory=list)
    record_actions: list[dict[str, str]] = Field(default_factory=list)
    page: int = 1
    has_more: bool = False
    scope: dict[str, Any] | None = None
    changes: list[dict[str, str]] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, str]


class WorkflowCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_operation_id: UUID
    expected_revision: int = Field(ge=0)
    action: Literal["update", "continue", "back", "pause", "resume", "cancel", "complete", "launch"]
    fields: dict[str, str] = Field(default_factory=dict, max_length=64)


class WorkflowStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_operation_id: UUID
    kind: WorkflowKind = "person"
    fields: dict[str, str] = Field(default_factory=dict, max_length=64)
