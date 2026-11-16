"""Synthetic restart and restore fixture, executed inside the API container."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from app.contracts import Offer, ShoppingRequest, ToolResult
from app.storage import INTERRUPTED, RUN_LEASE, Storage

SNAPSHOT = b'{"fixture":"synthetic backup and restore"}'
PRICE = Decimal('123456789012.3456789')
SPEND = Decimal('0.123456789')
IN_FLIGHT = Decimal('0.000000001')


def start_interrupted(storage, run_id):
    request = ShoppingRequest.model_validate_json(
        Path('examples/request-iphone17-beirut.json').read_text(encoding='utf-8')
    )
    started = datetime.now(timezone.utc) - RUN_LEASE - timedelta(minutes=1)
    storage.start_run(run_id, request, started_at=started)
    storage.record_action(run_id, 'search', {}, ToolResult(status='ok', duration_ms=5),
                          external_calls=1, spend=SPEND)
    storage.note_usage(run_id, external_calls=1, spend=IN_FLIGHT)


def seed(storage):
    original = storage.load_offer('offer-1').model_dump_json()
    offer = json.loads(original.replace('"ev-', '"recovery-ev-'))
    sha = storage.save_snapshot(SNAPSHOT, 'application/json')
    offer['offer_id'] = 'recovery-offer'
    offer['price']['value']['amount'] = str(PRICE)
    for receipt in offer['evidence']:
        receipt['response_sha256'] = sha
    storage.save_offer(Offer.model_validate(offer))
    start_interrupted(storage, 'recovery-before-restart')


def verify(storage, *run_ids):
    offer = storage.load_offer('recovery-offer')
    assert offer.price.value.amount == PRICE
    assert storage.offer_price('recovery-offer').amount == PRICE
    for receipt in offer.evidence:
        assert storage.load_snapshot(receipt.response_sha256) == SNAPSHOT

    for run_id in run_ids:
        assert storage.run_status(run_id) == 'partial'
        run = storage.load_run(run_id)
        assert run.stop_reason == INTERRUPTED
        assert run.recommendations == []
        assert run.usage.external_calls == 2
        assert run.usage.spend.amount == SPEND + IN_FLIGHT
        assert [action['seq'] for action in storage.list_actions(run_id)] == [1]


if __name__ == '__main__':
    storage = Storage()
    action, *run_ids = sys.argv[1:]
    try:
        if action == 'seed':
            seed(storage)
        elif action == 'interrupt':
            start_interrupted(storage, *run_ids)
        elif action == 'verify':
            verify(storage, *run_ids)
        else:
            raise SystemExit(f'Unknown fixture action {action!r}')
    finally:
        storage.engine.dispose()
