"""Separate-process worker runner; inject the shared-code shopping controller.

No in-process API task owns run state. Controllers publish through the lease and
must pass context.cancelled/remaining_seconds to their tool/provider boundaries.

With --quotes the same process also runs the delivery quote loop (#186) in a daemon thread:
it plans finished chats and sends their pending pre-order quotes through the source gate and
the shared ledger, so a buyer's chosen address resumes them without a new search.
"""
import argparse
import importlib
import threading
import time

from app.buyer_runs import BuyerRunService, RunError
from app.storage import Storage

# A controller ignoring cancellation cannot accumulate unlimited orphan threads
# in one worker process. Its slot remains held until its actual return.
_slots=threading.BoundedSemaphore(2)


class RunContext:
    def __init__(self,service,claim,stop):
        self.service,self.claim,self.stop=service,claim,stop

    @property
    def request(self):
        return self.claim.snapshot.request

    @property
    def answers(self):
        return self.claim.answers

    def cancelled(self):
        if self.stop.is_set():
            return True
        try:
            self.service.heartbeat(self.claim)
            return False
        except RunError:
            return True

    def remaining_seconds(self):
        try:
            return self.service.heartbeat(self.claim)/1000
        except RunError:
            return 0

    def publish(self,**changes):
        if self.stop.is_set():
            raise RunError(409,'Worker stopped')
        return self.service.publish(self.claim,**changes)


def execute_one(service,controller):
    if not _slots.acquire(blocking=False):
        return False
    try:
        claim=service.claim()
    except BaseException:
        _slots.release()
        raise
    if claim is None:
        _slots.release()
        return False
    stop,done=threading.Event(),threading.Event()
    context=RunContext(service,claim,stop)
    def work():
        try:
            controller(context)
            if not context.cancelled():
                context.publish(status='partial',stop_reason='Controller ended without a final result')
        except Exception:
            try:
                context.publish(status='error',stop_reason='Shopping controller failed')
            except (RunError,ValueError):
                pass
        finally:
            _slots.release()
            done.set()
    try:
        threading.Thread(target=work,name='buyer-controller',daemon=True).start()
    except BaseException:
        _slots.release()
        service.abandon(claim)
        raise
    deadline=time.monotonic()+claim.remaining_ms/1000
    try:
        while not done.wait(.05):
            if time.monotonic()>=deadline or context.cancelled():
                break
    finally:
        stop.set()
        service.abandon(claim)
        service.recover()
    return True


def quote_service(storage,service):
    """The production quote loop's dependencies: shared ledger, live registry and gate."""
    from app.accounting import Ledger
    from app.buyer_accounts import BuyerAccounts
    from app.delivery_quotes import QuoteService
    from app.onboarding import ActivationRegistry
    from app.sources import SourceGate
    registry=ActivationRegistry(storage)
    accounts=BuyerAccounts(service,quote_registry=registry)
    return QuoteService(accounts,gate=SourceGate(registry),ledger=Ledger.configured(),registry=registry)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller',required=True,help='Trusted backend module:function; never supplied by a buyer')
    parser.add_argument('--quotes',action='store_true',help='Also run the delivery quote loop (#186)')
    args=parser.parse_args()
    module,sep,name=args.controller.partition(':')
    if not sep:
        parser.error('Controller must be module:function')
    controller=getattr(importlib.import_module(module),name)
    storage=Storage()
    service=BuyerRunService(storage)
    stop=threading.Event()
    if args.quotes:
        from app.delivery_quotes import run_forever
        threading.Thread(target=run_forever,args=(quote_service(storage,service),stop),
                         name='delivery-quotes',daemon=True).start()
    try:
        while True:
            if not execute_one(service,controller):
                time.sleep(.5)
    finally:
        stop.set()
        storage.engine.dispose()


if __name__=='__main__':
    main()
