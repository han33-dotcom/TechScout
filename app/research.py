"""Controller-owned, bounded fan-out; role clients remain framework-independent."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar
from decimal import Decimal
from threading import Event
import time

from app.accounting import BudgetDenied, Charge

_worker = ContextVar('research_worker', default=False)


def research(job, clients, request, *, timeout=60):
    """Run at most three cooperative clients with a shared job and stop signal.

    Clients must use the supplied job for every external attempt and honor stop.
    A stalled client cannot dispatch after cancellation or its durable deadline.
    Completed results survive sibling failure/cancellation; no recursive fan-out.
    """
    clients = tuple(clients)
    if _worker.get() or not 1 <= len(clients) <= 3 or not 0 < timeout <= 90:
        raise ValueError('Only the controller may dispatch one to three research roles')
    stop = Event()

    def run(client):
        token = _worker.set(True)
        lease = None
        started = time.monotonic()
        try:
            lease = job.reserve('worker', Charge(Decimal(0)), timeout)
            lease.dispatch()
            return client(request, job, stop)
        finally:
            if lease:
                try:
                    lease.complete(Decimal(0), time.monotonic() - started)
                except BudgetDenied:
                    job.ledger.recover()
            _worker.reset(token)

    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='research')
    futures = [pool.submit(run, client) for client in clients]
    deadline = time.monotonic() + timeout
    pending = set(futures)
    while pending and time.monotonic() < deadline and not job.cancelled:
        _, pending = wait(pending, timeout=min(.05, max(0, deadline - time.monotonic())))
    done = set(futures) - pending
    if pending:
        job.cancel()
        for future in pending:
            future.cancel()
    stop.set()
    # Give cooperative clients a short chance to settle their leases. A client
    # ignoring cancellation cannot hold up the controller indefinitely.
    if pending:
        wait(pending, timeout=.2)
    pool.shutdown(wait=False, cancel_futures=True)
    return tuple({'status': 'completed', 'result': f.result()} if f in done and f.exception() is None
                 else {'status': 'failed' if f in done else 'cancelled'} for f in futures)
