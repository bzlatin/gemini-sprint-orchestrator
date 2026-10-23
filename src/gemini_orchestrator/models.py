from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrchestratorError(Exception):
    """An actionable, safe-to-display application error."""


def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class State(StrEnum):
    NOT_SELECTED = "NOT_SELECTED"
    SELECTED = "SELECTED"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    QUEUED = "QUEUED"
    BLOCKED = "BLOCKED"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    INTERRUPTED = "INTERRUPTED"
    VERIFYING = "VERIFYING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    MR_READY = "MR_READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TRANSITIONS: dict[State, set[State]] = {
    State.NOT_SELECTED: {State.SELECTED},
    State.SELECTED: {State.NOT_SELECTED, State.AWAITING_PLAN_APPROVAL},
    State.AWAITING_PLAN_APPROVAL: {
        State.NOT_SELECTED,
        State.SELECTED,
        State.QUEUED,
        State.BLOCKED,
        State.CANCELLED,
    },
    State.QUEUED: {
        State.NOT_SELECTED,
        State.SELECTED,
        State.PLANNING,
        State.IN_PROGRESS,
        State.BLOCKED,
        State.CANCELLED,
        State.PAUSED,
    },
    State.BLOCKED: {State.NOT_SELECTED, State.QUEUED, State.SELECTED, State.CANCELLED},
    # Planning is a read-only model turn; only a human decision leaves plan review.
    State.PLANNING: {
        State.PLAN_REVIEW,
        State.FAILED,
        State.CANCELLED,
        State.INTERRUPTED,
        State.PAUSED,
    },
    State.PLAN_REVIEW: {State.QUEUED, State.SELECTED, State.CANCELLED},
    State.IN_PROGRESS: {
        State.VERIFYING,
        State.FAILED,
        State.CANCELLED,
        State.INTERRUPTED,
        State.PAUSED,
    },
    State.PAUSED: {State.QUEUED, State.SELECTED, State.CANCELLED},
    State.INTERRUPTED: {State.SELECTED, State.CANCELLED, State.QUEUED},
    State.VERIFYING: {
        State.READY_FOR_REVIEW,
        State.FAILED,
        State.INTERRUPTED,
        State.CANCELLED,
        State.PAUSED,
    },
    State.READY_FOR_REVIEW: {
        State.APPROVED,
        State.CHANGES_REQUESTED,
        State.VERIFYING,
        State.PAUSED,
        State.CANCELLED,
    },
    State.CHANGES_REQUESTED: {State.SELECTED, State.CANCELLED, State.QUEUED},
    State.APPROVED: {State.MR_READY, State.CHANGES_REQUESTED, State.VERIFYING},
    State.MR_READY: {State.CHANGES_REQUESTED, State.VERIFYING},
    State.FAILED: {State.SELECTED, State.CANCELLED, State.QUEUED},
    State.CANCELLED: {State.SELECTED, State.QUEUED},
}


class Identity(BaseModel):
    account_id: str = ""
    display_name: str = ""
    email: str | None = None


class Resource(BaseModel):
    id: str
    name: str
    board_id: str | None = None


class Ticket(BaseModel):
    key: str
    summary: str
    description: str = ""
    acceptance: list[str] = Field(default_factory=list)
    assignee: str
    sprint_id: str
    project_id: str = ""
    priority: str = "Medium"
    status: str = "To Do"
    issue_type: str = "Task"
    parent: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    dependency_reasons: dict[str, str] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    links: list[dict[str, str]] = Field(default_factory=list)
    completed: bool = False

    @field_validator("key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", value):
            raise ValueError("Ticket keys must look like DEMO-123")
        return value


class Check(BaseModel):
    name: str
    argv: list[str]
    required: bool = True
    timeout: int = Field(default=300, ge=1, le=86400)

    @field_validator("argv")
    @classmethod
    def nonempty(cls, value: list[str]) -> list[str]:
        if not value or any(not arg or "\x00" in arg for arg in value):
            raise ValueError("Use a non-empty command argument array")
        return value


class JiraSettings(BaseModel):
    adapter: Literal["mcp", "rest"] = "mcp"
    server: str = ""
    settings_path: Path = Path.home() / ".gemini/settings.json"
    base_url: str = ""
    token_env: str = "JIRA_TOKEN"
    email_env: str = "JIRA_EMAIL"
    cloud_id: str = ""
    include_completed: bool = False
    acceptance_field: str = ""

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        if value:
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Jira URL must be HTTPS without credentials, query or fragment")
        return value


BRANCH_FIELDS = {"key", "slug", "type", "id"}


class GeminiSettings(BaseModel):
    executable: str = "gemini"
    concurrency: int = Field(default=2, ge=1, le=16)
    timeout: int = Field(default=1800, ge=1, le=86400)
    retries: int = Field(default=1, ge=0, le=3)
    behavior: str = "Implement only the assigned acceptance criteria. Explain changes and risks."
    plan_first: bool = True
    use_instructions: bool = True
    reuse_skills: bool = False


class IsolationSettings(BaseModel):
    tool_read_paths: list[Path] = Field(default_factory=list)
    max_worktree_bytes: int = Field(default=512 * 1024 * 1024, ge=1024)
    max_runtime_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024)
    max_files: int = Field(default=50000, ge=10)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    mode: str = "connected"
    display_name: str
    email: str | None = None
    identity: Identity = Field(default_factory=Identity)
    team: str = ""
    projects: list[Resource] = Field(default_factory=list)
    boards: list[Resource] = Field(default_factory=list)
    board_id: str = ""
    sprint: Resource | None = None
    sprint_behavior: Literal["active", "manual"] = "active"
    repository: Path
    worktrees: Path
    jira: JiraSettings = Field(default_factory=JiraSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    checks: list[Check] = Field(default_factory=list)
    isolation: IsolationSettings = Field(default_factory=IsolationSettings)
    hosting: str = "local"
    remote_writes: bool = False
    protected_branches: list[str] = Field(
        default_factory=lambda: ["main", "master", "develop", "production"]
    )
    branch_template: str = "{type}/{key}-{slug}"

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value):
            raise ValueError("Profile names must be lowercase letters, digits and hyphens")
        return value

    @field_validator("branch_template")
    @classmethod
    def known_branch_fields(cls, value: str) -> str:
        from string import Formatter

        try:
            fields = {name for _, name, _, _ in Formatter().parse(value) if name is not None}
        except ValueError as exc:
            raise ValueError(f"Invalid branch template: {exc}") from exc
        if not fields <= BRANCH_FIELDS or "key" not in fields:
            raise ValueError(
                "Branch templates must include {key} and may use only "
                + ", ".join("{" + name + "}" for name in sorted(BRANCH_FIELDS))
            )
        return value

    @model_validator(mode="after")
    def paths_and_mode(self) -> Profile:
        self.repository = self.repository.expanduser().resolve()
        self.worktrees = self.worktrees.expanduser().resolve()
        if self.worktrees == self.repository or self.worktrees.is_relative_to(self.repository):
            raise ValueError("Worktrees must be outside the main checkout")
        if self.mode not in {"demo", "connected"} or self.hosting not in {
            "local",
            "github",
            "gitlab",
        }:
            raise ValueError("Invalid mode or hosting provider")
        return self


class Edge(BaseModel):
    prerequisite: str
    dependent: str
    kind: str = "confirmed"
    reason: str


class Plan(BaseModel):
    id: str
    base: str
    config_hash: str
    tickets: dict[str, str]
    groups: list[list[str]]
    blocked: dict[str, str]
    edges: list[Edge]
    branches: dict[str, str]
    worktrees: dict[str, str]
    checks: list[Check]
    context: dict[str, str] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return digest(self)
