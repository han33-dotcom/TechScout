"""Start and monitor the ingestion worker process.

Job claiming and execution are supplied by the ingestion implementation.
"""
import argparse
import logging
import os
from pathlib import Path
import signal
from threading import Event
from time import time

from sqlalchemy.exc import SQLAlchemyError

from app.storage import Storage

HEARTBEAT = Path('/tmp/techscout-ingestion.heartbeat')
PROBE_INTERVAL = 5
MAX_HEARTBEAT_AGE = 20
log = logging.getLogger(__name__)


def serve(storage: Storage, stop: Event) -> None:
    HEARTBEAT.unlink(missing_ok=True)
    try:
        while not stop.is_set():
            try:
                storage.ping()
                if not os.access(storage.snapshots.root, os.W_OK | os.X_OK):
                    raise OSError('The evidence directory is not writable')
                HEARTBEAT.touch()
            except (OSError, SQLAlchemyError):
                HEARTBEAT.unlink(missing_ok=True)
                log.warning('Ingestion worker dependencies are unavailable', exc_info=True)
            stop.wait(PROBE_INTERVAL)
    finally:
        HEARTBEAT.unlink(missing_ok=True)
        storage.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check the worker heartbeat')
    if parser.parse_args().check:
        try:
            return int(not 0 <= time() - HEARTBEAT.stat().st_mtime < MAX_HEARTBEAT_AGE)
        except FileNotFoundError:
            return 1

    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    log.info('Starting ingestion worker')
    serve(Storage(), stop)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
