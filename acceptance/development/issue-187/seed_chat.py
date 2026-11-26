"""Give one local browser session the synthetic two-revision chat used by the #187 walkthrough.

The chat is the synthetic v2 workspace fixture (Book-Local verified, Book-Other provisional,
Book-Forward overseas and uncertain), stored directly as that session's chat. No worker,
retailer, model or ledger runs. Refuses anything but a local SQLite database.

    python acceptance/development/issue-187/seed_chat.py --database-url sqlite:///walk.db \
        --session-token <cookie value> --suffix first
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

from app.storage import Storage  # noqa: E402
from test_buyer_accounts import seed_chat  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--session-token', required=True)
    parser.add_argument('--suffix', required=True)
    args = parser.parse_args(argv)
    if not args.database_url.startswith('sqlite:///'):
        raise SystemExit('Refusing: walkthrough chats are seeded only into a local SQLite database')
    with tempfile.TemporaryDirectory() as evidence:
        storage = Storage(args.database_url, evidence)
        try:
            workspace = seed_chat(storage, args.session_token, args.suffix)
        finally:
            storage.engine.dispose()
    print(json.dumps({'run_id': workspace.current.run.run_id, 'previous_run_id': workspace.previous.run.run_id,
                      'request_id': workspace.current.request.request_id}))


if __name__ == '__main__':
    main()
