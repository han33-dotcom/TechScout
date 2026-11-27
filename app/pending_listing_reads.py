"""Run-scoped Firecrawl transfers. Only the buyer thread interprets a capture."""
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from queue import SimpleQueue

from app.provider_http import _read_observer


@dataclass
class ListingTransfer:
    index: int
    url: str
    job: object
    future: Future


class PendingListingReads:
    capacity = 2

    def __init__(self, elapsed):
        self.elapsed = elapsed
        self.pool = ThreadPoolExecutor(max_workers=self.capacity, thread_name_prefix='buyer-page')
        self.pending = {}
        self.events = SimpleQueue()

    def record(self, url, stage, **details):
        self.events.put(dict(url=url, stage=stage, at_ms=round(self.elapsed() * 1000), **details))

    def start(self, index, url, job, inspect):
        if index in self.pending or len(self.pending) >= self.capacity:
            raise ValueError('A listing transfer is already pending or both reader slots are busy')

        def fetch():
            token = _read_observer.set(lambda stage, **details: self.record(url, stage, **details))
            try:
                return inspect(job, url, browser=None)
            finally:
                _read_observer.reset(token)

        self.record(url, 'read_requested')
        transfer = ListingTransfer(index, url, job, self.pool.submit(fetch))
        self.pending[index] = transfer
        return transfer

    def wait_for_page(self, room, stopped):
        futures = [t.future for t in self.pending.values()]
        while futures and room() > 0 and not stopped():
            if wait(futures, timeout=min(.25, max(0, room())), return_when=FIRST_COMPLETED).done:
                break

    def close(self):
        for transfer in self.pending.values():
            transfer.job.cancel()
            transfer.future.cancel()
            self.record(transfer.url, 'unconsumed_at_close')
        self.pool.shutdown(wait=False, cancel_futures=True)
        events = []
        while not self.events.empty():
            events.append(self.events.get())
        return events
