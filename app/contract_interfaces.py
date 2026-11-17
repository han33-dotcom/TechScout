"""Ports shared by the FastAPI, orchestration and collection modules.

Transport, execution, authorization, persistence and retry behavior are supplied by implementations.
"""
from collections.abc import Sequence
from typing import Annotated, Literal, Protocol, TypeVar

from pydantic import Field

from app.collection_contracts import (
    CollectionSnapshot, IngestionCheckpoint, IngestionJob, IngestionManifest, RetailerDirectoryEntry,
)
from app.contracts import Amount, Count, Model, Text, UtcTime
from app.contracts_v2 import (
    AgentFinding, Clarification, ComparisonWorkspace, Evidence, Offer, Recommendation,
    RunReference, RunSnapshot, ShoppingRequest,
)
from app.contracts_v2 import CoverageCounts, UncertainAlternative


class AgentTask(Model):
    task_id: Text
    run: RunReference
    role: Literal['researcher']
    question: Text
    permitted_tools: tuple[Text, ...]
    deadline: UtcTime
    call_allowance: Count
    spend_allowance_usd: Amount


class ResearchPlan(Model):
    tasks: Annotated[tuple[AgentTask, ...], Field(max_length=3)] = ()
    clarification: Clarification | None = None


class Evaluation(Model):
    findings: tuple[AgentFinding, ...] = ()
    follow_up: ResearchPlan | None = None


class Explanation(Model):
    text: Text
    evidence_ids: tuple[Text, ...] = ()


InputT = TypeVar('InputT', contravariant=True)
OutputT = TypeVar('OutputT', covariant=True)


class AgentRole(Protocol[InputT, OutputT]):
    async def __call__(self, context: InputT, /) -> OutputT: ...


class PlanningContext(Model):
    run: RunReference
    request: ShoppingRequest
    findings: tuple[AgentFinding, ...] = ()


class ResearchContext(Model):
    task: AgentTask
    request: ShoppingRequest
    evidence: tuple[Evidence, ...] = ()


class WritingContext(Model):
    """What the writer explains. request, coverage and uncertain are optional (2026-11-17 revision)."""
    run: RunReference
    recommendations: tuple[Recommendation, ...]
    evidence: tuple[Evidence, ...] = ()
    request: ShoppingRequest | None = None
    coverage: CoverageCounts | None = None
    uncertain: tuple[UncertainAlternative, ...] = ()


Orchestrator = AgentRole[PlanningContext, ResearchPlan]
Researcher = AgentRole[ResearchContext, AgentFinding]
Evaluator = AgentRole[PlanningContext, Evaluation]
Writer = AgentRole[WritingContext, Explanation]


class BuyerRuns(Protocol):
    async def start(self, session_id: str, request: ShoppingRequest) -> RunSnapshot: ...
    async def read(self, session_id: str, run_id: str) -> ComparisonWorkspace | None: ...
    async def clarify(self, session_id: str, run: RunReference,
                      clarification_id: str, answer: str) -> RunSnapshot: ...
    async def cancel(self, session_id: str, run: RunReference) -> RunSnapshot: ...
    async def refine(self, session_id: str, run: RunReference,
                     request: ShoppingRequest) -> ComparisonWorkspace: ...


class CollectionJobs(Protocol):
    async def enqueue(self, manifest: IngestionManifest) -> IngestionJob: ...
    async def claim(self, worker_id: str, lease_expires_at: UtcTime) -> CollectionSnapshot | None: ...
    async def checkpoint(self, worker_id: str, checkpoint: IngestionCheckpoint,
                         *, evidence: Sequence[Evidence]) -> None: ...


class RetailerDirectory(Protocol):
    async def get(self, retailer_id: str) -> RetailerDirectoryEntry | None: ...


class EvidenceRepository(Protocol):
    async def get_evidence(self, evidence_id: str) -> Evidence | None: ...
    async def get_offer(self, offer_id: str) -> Offer | None: ...
