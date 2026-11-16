"""Explicitly invoked, bounded embedding calls with a durable usage ledger."""
from decimal import Decimal
import uuid

import httpx2
from sqlalchemy import insert, select, update

from app.corpus import Corpus
from app.retrieval import MODEL, DIMENSIONS, checked_vector
from app.storage import embedding_calls, _now


class OpenAIEmbeddings:
    """Direct API transport. No implicit keys, retries or paid calls at import time."""
    def __init__(self, api_key: str, *, transport=None):
        if not api_key or not api_key.strip():
            raise ValueError('An explicit API key is required')
        self.api_key = api_key
        self.transport = transport

    def __call__(self, texts):
        with httpx2.Client(transport=self.transport, timeout=20, follow_redirects=False) as client:
            with client.stream('POST','https://api.openai.com/v1/embeddings',
                headers={'Authorization':f'Bearer {self.api_key}'},
                json={'model':MODEL,'dimensions':DIMENSIONS,'encoding_format':'float','input':list(texts)}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4_000_000:
                        raise ValueError('Embedding response exceeded its bound')
                import json
                return json.loads(body)


class EmbeddingService:
    def __init__(self, corpus: Corpus, provider, *, usd_per_million: Decimal):
        if not isinstance(usd_per_million,Decimal) or not usd_per_million.is_finite() or usd_per_million <= 0:
            raise ValueError('Supply the configured positive Decimal embedding rate')
        self.corpus = corpus
        self.provider = provider
        self.rate = usd_per_million

    def embed(self, texts, *, owner_id: str, purpose: str, index_version: str,
              max_spend_usd: Decimal):
        texts = tuple(texts)
        if (not 1 <= len(texts) <= 32 or any(not isinstance(t,str) or not t.strip()
                or len(t.encode('utf-8')) > 8000 for t in texts)):
            raise ValueError('Embed 1–32 nonempty texts, each at most 8000 UTF-8 bytes')
        if not owner_id.strip() or not index_version.strip() or purpose not in {'query','index'}:
            raise ValueError('Embedding usage needs an owner, purpose and index version')
        if not isinstance(max_spend_usd,Decimal) or not max_spend_usd.is_finite() or max_spend_usd < 0:
            raise ValueError('Embedding spend allowance must be a nonnegative Decimal')
        # UTF-8 byte count conservatively bounds tokenizer input tokens. Reserve
        # before dispatch; crashes and ambiguous failures retain this allowance.
        reserved_tokens = sum(len(t.encode('utf-8')) for t in texts)
        reserved = Decimal(reserved_tokens)*self.rate/Decimal(1_000_000)
        if reserved > max_spend_usd:
            raise ValueError('Embedding call exceeds its spend allowance')
        call_id = uuid.uuid4().hex
        with self.corpus.storage.transaction() as db:
            db.execute(insert(embedding_calls).values(call_id=call_id,owner_id=owner_id,
                purpose=purpose,model=MODEL,index_version=index_version,status='reserved',
                reserved_usd=reserved,rate_usd_per_million=self.rate,recorded_at=_now()))
        try:
            result = self.provider(texts)
            tokens = result['usage']['total_tokens']
            if type(tokens) is not int or tokens < 0 or tokens > reserved_tokens or result['model'] != MODEL:
                raise ValueError('Invalid embedding usage or model')
            rows = result['data']
            if len(rows) != len(texts) or any(type(row['index']) is not int for row in rows):
                raise ValueError('Invalid embedding response indexes')
            rows = sorted(rows,key=lambda row:row['index'])
            if [row['index'] for row in rows] != list(range(len(texts))):
                raise ValueError('Embedding response indexes do not match inputs')
            vectors = tuple(checked_vector(row['embedding']) for row in rows)
            actual = Decimal(tokens)*self.rate/Decimal(1_000_000)
        except Exception:
            with self.corpus.storage.transaction() as db:
                db.execute(update(embedding_calls).where(embedding_calls.c.call_id==call_id).values(status='uncertain'))
            raise
        with self.corpus.storage.transaction() as db:
            db.execute(update(embedding_calls).where(embedding_calls.c.call_id==call_id)
                .values(status='completed',tokens=tokens,actual_usd=actual))
        return vectors

    def index_document(self, document_id: int, *, owner_id: str, max_spend_usd: Decimal):
        pending = set(self.corpus.pending_embeddings(document_id,MODEL,DIMENSIONS))
        if not pending:
            return 0
        from app.storage import corpus_documents
        with self.corpus.storage.engine.connect() as db:
            recipe = db.execute(select(corpus_documents.c.processing)
                .where(corpus_documents.c.document_id==document_id)).scalar_one()
        selected = [p for p in self.corpus.passages(document_id) if int(p.passage_id) in pending][:32]
        vectors = self.embed([p.text for p in selected],owner_id=owner_id,purpose='index',
            index_version=recipe['index_version'],max_spend_usd=max_spend_usd)
        for passage,vector in zip(selected,vectors):
            self.corpus.save_embedding(int(passage.passage_id),recipe['index_version'],MODEL,list(vector))
        return len(selected)
