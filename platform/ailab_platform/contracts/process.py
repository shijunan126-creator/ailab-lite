"""Independently versioned historical process API, never part of Run.result."""

from typing import Literal, NotRequired

from pydantic import BaseModel
from typing_extensions import TypedDict


class ProcessSource(TypedDict):
    url: str
    title: NotRequired[str]
    site: NotRequired[str]


class ProcessStep(TypedDict):
    name: str
    label: str
    status: Literal["running", "ok", "failed", "note"]
    detail: NotRequired[str]
    output: NotRequired[str]
    modelStep: NotRequired[int]
    sources: NotRequired[list[ProcessSource]]


class ProcessPlanStep(TypedDict):
    id: str
    title: str
    status: Literal["pending", "running", "done", "skipped", "failed"]


class ProcessPlan(TypedDict):
    kind: Literal["plan"]
    steps: list[ProcessPlanStep]


class ProcessAsk(TypedDict):
    kind: Literal["ask"]
    id: str
    question: str
    options: list[str]
    allowText: bool
    answer: NotRequired[str]


class ProcessText(TypedDict):
    kind: Literal["text"]
    step: int
    text: str


class ProcessThinking(TypedDict):
    kind: Literal["thinking"]
    step: int
    text: str
    seconds: float
    done: bool


class ProcessSteps(TypedDict):
    kind: Literal["steps"]
    steps: list[ProcessStep]


class ProcessSteer(TypedDict):
    kind: Literal["steer"]
    id: str
    prompt: str


class ProcessView(BaseModel):
    schema_version: Literal[1] = 1
    source: Literal["snapshot", "events"]
    items: list[ProcessPlan | ProcessAsk | ProcessText | ProcessThinking | ProcessSteps | ProcessSteer]
