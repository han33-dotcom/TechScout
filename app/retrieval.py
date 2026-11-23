"""Bounded public-corpus retrieval; exact identity is never inferred from similarity."""
from dataclasses import dataclass
from decimal import Decimal
import math
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy import func, literal_column, select

from app.collection_contracts import EvidencePassage
from app.contracts import Model, Text
from app.corpus import Corpus
from app.freshness import Freshness, FreshnessContext, assess_evidence
from app.storage import (
    corpus_documents as documents, corpus_document_passages as links,
    corpus_embeddings as embeddings, corpus_passages as passages,
    corpus_resources as resources, corpus_retirements as retirements, sources,
)

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
ROUTED_MODEL = 'openai/text-embedding-3-small'


def routed_index_version(document_index: str) -> str:
    """Keep routed vectors independent of the legacy direct-provider space."""
    return document_index + '/openrouter-v1'


class EvidenceQuery(Model):
    text: Annotated[Text, Field(max_length=1000)]
    source_ids: Annotated[tuple[Text, ...], Field(min_length=1, max_length=20)]
    context: FreshnessContext
    scope: Literal['source', 'product', 'variant', 'branch', 'route']
    field_paths: Annotated[tuple[Text, ...], Field(min_length=1, max_length=20)]
    corpus_version: Text
    index_version: Text
    chunk_version: Text
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 5
    candidate_limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 100

    @model_validator(mode='after')
    def bound_scope(self):
        needed = {'product': 'product_id', 'variant': 'variant_id', 'branch': 'branch_id', 'route': 'route_id'}
        if self.scope in needed and getattr(self.context, needed[self.scope]) is None:
            raise ValueError('Retrieval needs the requested exact scope')
        if self.context.variant_id and not self.context.product_id:
            raise ValueError('A variant requires a product')
        if self.limit > self.candidate_limit:
            raise ValueError('Result limit exceeds candidate bound')
        return self


@dataclass(frozen=True)
class EvidenceHit:
    passage: EvidencePassage
    freshness: Freshness
    score: float
    channels: tuple[str, ...]


def checked_vector(values):
    if len(values) != DIMENSIONS or any(type(v) not in (int, float, Decimal) for v in values):
        raise ValueError('Expected a finite 1536-dimensional embedding')
    values = tuple(float(v) for v in values)
    # pgvector stores float32. Reject coordinates that would overflow or a
    # nonzero double vector that would be stored as all zeroes.
    peak = max(abs(v) for v in values)
    if peak > 3.4028234663852886e38 or peak <= 2.0**-150:
        raise ValueError('Embedding exceeds pgvector numeric range')
    norm = math.sqrt(sum(v*v for v in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Embedding must have a finite nonzero norm')
    return values


class EvidenceRetrieval:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus

    def _eligible(self, query):
        heads = select(documents.c.resource_id, func.max(documents.c.document_id).label('head')).group_by(documents.c.resource_id).subquery()
        receipt = passages.c.payload['evidence']
        retired = select(1).where(retirements.c.resource_id == resources.c.resource_id,
                                 retirements.c.observed_at >= documents.c.observed_at).exists()
        statement = (select(passages.c.passage_id, passages.c.text, passages.c.payload,
                            resources.c.check_status)
            .select_from(passages.join(links).join(documents).join(heads, heads.c.head == documents.c.document_id)
                         .join(resources, documents.c.resource_id == resources.c.resource_id)
                         .join(sources, resources.c.source_id == sources.c.id))
            .where(resources.c.source_id.in_(query.source_ids), sources.c.status == 'active',
                   ~retired, ~resources.c.needs_processing,
                   documents.c.processing['corpus_version'].as_string() == query.corpus_version,
                   documents.c.processing['index_version'].as_string() == query.index_version,
                   documents.c.processing['chunk_version'].as_string() == query.chunk_version,
                   receipt['scope'].as_string() == query.scope,
                   receipt['field_path'].as_string().in_(query.field_paths)))
        for field in ('product_id', 'variant_id', 'branch_id', 'route_id'):
            requested = getattr(query.context, field)
            # Source-wide policies have no product bindings. Other scopes must match
            # every supplied dimension exactly; missing route/branch is not a wildcard.
            if query.scope == 'source':
                requested = None
            statement = statement.where(receipt[field].as_string() == requested)
        return statement

    @staticmethod
    def _vector_profile(query, model):
        if model == ROUTED_MODEL:
            version = routed_index_version(query.index_version)
        elif model == MODEL:
            version = query.index_version  # explicitly supplied legacy vectors
        else:
            raise ValueError('Unknown embedding model')
        return (embeddings.c.passage_id == passages.c.passage_id,
                embeddings.c.index_version == version, embeddings.c.model == model,
                embeddings.c.dimensions == DIMENSIONS)

    def has_routed_vectors(self, query: EvidenceQuery) -> bool:
        statement = self._eligible(query).where(*self._vector_profile(query, ROUTED_MODEL)).limit(1)
        with self.corpus.storage.engine.connect() as db:
            return db.execute(statement).first() is not None

    def retrieve(self, query: EvidenceQuery, *, vector=None, vector_model=MODEL) -> tuple[EvidenceHit, ...]:
        vector = checked_vector(vector) if vector is not None else None
        base = self._eligible(query)
        token_words = re.findall(r'\w+', query.text.lower(), flags=re.UNICODE)
        if not token_words:
            return ()
        with self.corpus.storage.engine.connect() as db:
            if db.dialect.name == 'postgresql':
                document = func.to_tsvector(literal_column("'simple'::regconfig"), passages.c.text)
                terms = func.plainto_tsquery(literal_column("'simple'::regconfig"), query.text)
                lexical = base.add_columns(func.ts_rank_cd(document, terms).label('rank')).where(document.op('@@')(terms))
                keyword = db.execute(lexical.order_by(func.ts_rank_cd(document, terms).desc(), passages.c.passage_id)
                                     .limit(query.candidate_limit)).all()
            else:
                # Offline SQLite compatibility; production uses the indexed SQL query above.
                lexical = base
                for word in token_words:
                    lexical = lexical.where(func.lower(passages.c.text).contains(word, autoescape=True))
                keyword = db.execute(lexical.order_by(passages.c.passage_id).limit(query.candidate_limit)).all()
            semantic = []
            if vector is not None:
                profile = self._vector_profile(query, vector_model)
                if db.dialect.name == 'postgresql':
                    distance = embeddings.c.embedding.cosine_distance(list(vector))
                    semantic = db.execute(base.add_columns(distance.label('distance')).where(*profile)
                        .order_by(distance, passages.c.passage_id).limit(query.candidate_limit)).all()
                else:
                    candidates = db.execute(base.add_columns(embeddings.c.embedding).where(*profile)
                        .order_by(passages.c.passage_id).limit(query.candidate_limit)).all()
                    def cosine(row):
                        values = row.embedding
                        return sum(a*b for a,b in zip(vector,values)) / (
                            math.sqrt(sum(a*a for a in vector))*math.sqrt(sum(b*b for b in values)))
                    semantic = sorted(candidates, key=lambda row: (-cosine(row),row.passage_id))
        ranked = {}
        for channel, rows in (('keyword',keyword),('vector',semantic)):
            for position,row in enumerate(rows,1):
                entry = ranked.setdefault(row.passage_id, [row,0.0,[]])
                entry[1] += 1/(60+position)
                entry[2].append(channel)
        results = []
        for row,score,channels in sorted(ranked.values(),key=lambda item:(-item[1],item[0].passage_id)):
            passage = EvidencePassage(passage_id=str(row.passage_id),text=row.text,
                evidence=row.payload['evidence'],chunk_version=query.chunk_version)
            freshness = assess_evidence(passage.evidence,query.context,check_status=row.check_status)
            if freshness.state in {'conflict','retired'}:
                continue
            # Text is source material, not an independently established claim. Even
            # historical passages keep their IDs, timestamps and freshness decision.
            results.append(EvidenceHit(passage,freshness,score,tuple(channels)))
            if len(results) == query.limit:
                break
        return tuple(results)
