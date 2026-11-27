"""Give one local browser session the synthetic delivery-quote chat used by the #186 walkthrough.

The chat is the offline test chat from tests/test_delivery_quotes.py, built on the synthetic v2
workspace: Book-Local verified at USD 930, and four Lebanese sellers whose delivery fee is
unpublished (City-Shop, Street-A, Street-B and No-Route). It is stored directly as that session's
finished search. Refuses anything but a local SQLite database.

    python acceptance/development/issue-186/seed_chat.py --database-url sqlite:///walk.db \
        --session-token <cookie value> --suffix first
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

from app.buyer_runs import session_hash  # noqa: E402
from app.storage import Storage, buyer_runs  # noqa: E402
from test_delivery_quotes import chat_snapshot  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--session-token', required=True)
    parser.add_argument('--suffix', required=True)
    args = parser.parse_args(argv)
    if not args.database_url.startswith('sqlite:///'):
        raise SystemExit('Refusing: walkthrough chats are seeded only into a local SQLite database')
    snapshot = chat_snapshot(args.suffix)
    with tempfile.TemporaryDirectory() as evidence:
        storage = Storage(args.database_url, evidence)
        try:
            with storage.transaction() as db:
                db.execute(buyer_runs.insert().values(
                    run_id=snapshot.run.run_id, session_hash=session_hash(args.session_token),
                    request_id=snapshot.request.request_id, request_revision=snapshot.request.revision,
                    status=snapshot.status, snapshot=snapshot.model_dump(mode='json'), answers=[], active_ms=0))
        finally:
            storage.engine.dispose()
    print(json.dumps({'run_id': snapshot.run.run_id, 'request_id': snapshot.request.request_id}))


if __name__ == '__main__':
    main()
