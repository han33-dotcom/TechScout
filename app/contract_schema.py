"""Print the v2 JSON Schema bundle for API, agent and worker consumers."""
import json

from pydantic.json_schema import models_json_schema

from app.collection_contracts import (
    CollectionSnapshot, EvidencePassage, IngestionCheckpoint, IngestionJob, IngestionManifest,
    RetailerDirectoryEntry,
)
from app.contract_interfaces import AgentTask, Evaluation, Explanation, ResearchPlan
from app.contracts_v2 import (
    AgentFinding, ComparisonWorkspace, Evidence, MatchAssessment, Offer, RunSnapshot,
    ShoppingRequest, Variant,
)

MODELS = (
    ShoppingRequest, Variant, Offer, Evidence, MatchAssessment, RunSnapshot, ComparisonWorkspace,
    AgentTask, ResearchPlan, AgentFinding, Evaluation, Explanation, RetailerDirectoryEntry,
    IngestionManifest, IngestionJob, IngestionCheckpoint, CollectionSnapshot, EvidencePassage,
)


def schema_bundle() -> dict:
    references, bundle = models_json_schema(
        [(model, 'validation') for model in MODELS], title='TechScout v2 contracts',
    )
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'anyOf': [references[model, 'validation'] for model in MODELS],
        **bundle,
    }


if __name__ == '__main__':
    print(json.dumps(schema_bundle(), indent=2))
