"""Explicit ledger administration; never invoked by normal request handling."""
import argparse
import json
from decimal import Decimal

from sqlalchemy import insert, inspect, select, text

from app.accounting import BudgetDenied, Ledger, LIMITS, money
from app.storage import accounting_state
from app.ledger_storage import migrate_rows


def initialize(ledger):
    """Create only the accounting schema, preserving existing campaign state."""
    with ledger.storage.engine.begin() as db:
        if db.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(718503125)'))
        existed = inspect(db).has_table(accounting_state.name)
        accounting_state.create(db, checkfirst=True)
        if db.execute(select(accounting_state.c.id).where(accounting_state.c.id == 1)).first() is None:
            if existed:
                raise BudgetDenied('Existing ledger has no state row; restore/reconcile it instead of resetting')
            db.execute(insert(accounting_state).values(id=1, payload={'project': None}))
        try:
            migrate_rows(db, expected_project=ledger.project)
        except ValueError as exc:
            raise BudgetDenied(str(exc)) from exc
    ledger.provision()


def reconcile(ledger, receipt):
    """Append an immutable historical debit/hold, not a fictional new dispatch.

    Historical overages are recorded and block the campaign, rather than being
    rejected and forgotten. Replaying a receipt cannot charge twice or refund it.
    """
    required = {'id', 'international', 'fetch_attempts', 'paid_calls', 'active_seconds',
                'usd', 'external_calls', 'basis', 'status'}
    if set(receipt) != required:
        raise ValueError('Receipt fields do not match the reconciliation contract')
    if not isinstance(receipt['id'], str) or not receipt['id'].strip():
        raise ValueError('An explicit receipt identifier is required')
    if type(receipt['international']) is not bool:
        raise ValueError('International scope must be explicit')
    for key in ('fetch_attempts', 'paid_calls', 'active_seconds', 'external_calls'):
        if type(receipt[key]) is not int or receipt[key] < 0:
            raise ValueError('Historical usage requires nonnegative integer bounds')
    if not isinstance(receipt['basis'], str) or not receipt['basis'].strip():
        raise ValueError('An evidence basis is required')
    if receipt['status'] not in {'measured', 'conservative_hold'}:
        raise ValueError('Unknown reconciliation status')
    if not isinstance(receipt['usd'], str):
        raise ValueError('Money must be a decimal string')
    cost = money(Decimal(receipt['usd']))
    values = [receipt['fetch_attempts'], receipt['paid_calls'],
              receipt['active_seconds'] * 1000, str(cost), receipt['external_calls']]
    if values[4] < max(values[0], values[1]):
        raise ValueError('External calls cannot be less than either call counter')
    scopes = ['project', 'bootstrap']
    if receipt['international']:
        scopes.append('bootstrap:international')
    overrun = False
    with ledger._transaction() as (state, now):
        receipts = state.setdefault('reconciliation', {})
        if receipt['id'] in receipts:
            if receipts[receipt['id']]['receipt'] != receipt:
                raise ValueError('Receipt conflicts with an existing historical adjustment')
            return
        for scope in scopes:
            used = state['scopes'].setdefault(scope, [0, 0, 0, '0', 0])
            Ledger._add(used, values)
            overrun |= any(bound is not None and Decimal(str(used[i])) > Decimal(str(bound))
                           for i, bound in enumerate(LIMITS[scope]))
        receipts[receipt['id']] = {'receipt': receipt, 'recorded_at': now, 'scopes': scopes}
        if overrun:
            state['blocked'] = True
    if overrun:
        raise BudgetDenied('Historical usage exceeds a limit; debit recorded and campaign blocked')


def summary(ledger):
    with ledger._transaction(readonly=True) as (state, _):
        return {'project': state['project'], 'blocked': state['blocked'], 'scopes': dict(state['scopes']),
                'jobs': len(state['jobs']), 'attempts': len(state['attempts']),
                'reconciliation_receipts': sorted(state.get('reconciliation', {}))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['initialize', 'status', 'reconcile'])
    parser.add_argument('--receipts', help='JSON file containing a receipts array')
    args = parser.parse_args()
    ledger = Ledger.configured()
    try:
        if args.command == 'initialize':
            initialize(ledger)
        elif args.command == 'reconcile':
            if not args.receipts:
                parser.error('--receipts is required')
            with open(args.receipts, encoding='utf-8') as source:
                receipts = json.load(source)['receipts']
            for receipt in receipts:
                reconcile(ledger, receipt)
        print(json.dumps(summary(ledger), indent=2))
    finally:
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    main()
