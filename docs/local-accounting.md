# Local accounting deployment and later migration

The user selected local PostgreSQL on November 17, 2026 in
[#125](https://github.com/han33-dotcom/TechScout/issues/125), superseding the earlier
requirement to choose an online host before local development. One dedicated
`techscout_accounting` database was the initial accounting authority on Hassan's PC for
`techscout-december-2026`. Other machines must not create independent copies and
spend the same allowance. Remote access, firewall changes and online hosting were
not configured. This database is separate from the product corpus.

The current shared endpoint and cutover status are recorded in [#248](https://github.com/han33-dotcom/TechScout/issues/248).

## Original local deployment

PostgreSQL 18.1 runs on loopback port 5432. A separate `techscout_accounting` login
has database CONNECT, schema USAGE, table SELECT/UPDATE and sequence SELECT for
backups. It cannot create databases/roles and is not a superuser or replication
role. The administrator password was used in memory and not saved. The generated
application password is in a user-restricted libpq password file under
`%LOCALAPPDATA%\TechScout\accounting\accounting.pgpass`; the file is outside Git.
PostgreSQL password authentication was not changed by this setup.

The password-free connection URL (including the private password-file path) and
campaign identifier are saved in Windows user environment settings. Already-running
terminals and applications do not automatically inherit those new settings.
In a PowerShell terminal at the repository root, run:

```powershell
. ./scripts/Use-LocalAccounting.ps1
.venv/Scripts/python.exe -m app.accounting_admin status
```

Run the API, workers and probes from that configured shell. The repository `.env`
is not automatically loaded by host Python processes. Never paste the application
password into a command, commit it, or use the PostgreSQL administrator for normal
research. Containers require a separately reachable endpoint and credential mount;
this native local setup does not configure container networking.

Operational checks demonstrated two separate Python processes reading identical
usage, runtime rejection of an extra paid international call, a restricted application
role, and a custom-format backup restored into a disposable database with an
identical ledger payload. No retailer or paid provider was contacted for these checks.
One earlier restore attempt exposed a missing sequence-read privilege; it was fixed
before the successful backup/restore check. Both disposable restore-test databases
were removed after verification; the working ledger and verified backup remain.

## Dedicated schema and historical usage

For a new dedicated ledger, an administrator first creates its database and login,
then supplies an explicit `TECHSCOUT_ACCOUNTING_URL` and project and runs:

```text
python -m app.accounting_admin initialize
python -m app.accounting_admin reconcile --receipts config/accounting-reconciliation-2026-11-17.json
python -m app.accounting_admin status
```

The administrator's `initialize` command creates the dedicated version 2
accounting tables or converts an existing version 1 ledger in one transaction.
It verifies the reconstructed state against the full original payload before commit.
The campaign ID, historical usage, reservations, owners, receipts, credits, blocked
status and extra checkpoint fields survive the conversion. A second invocation
preserves existing records. Full corpus migrations and pgvector are separate.

Stop **all** ledger clients before changing the storage version. Build each client
from a revision that supports version 2, including experiment workers. Version 1
clients cannot operate a migrated ledger. Take a final database backup and keep
the source inactive during verification. If the destination has accepted writes,
use its latest state for recovery.

Give each runtime login CONNECT on the database, schema USAGE, SELECT/UPDATE on
`accounting_state` and SELECT/INSERT/UPDATE/DELETE on `accounting_entries`.
DELETE supports lease cleanup. A login used for backups also needs SELECT on
`accounting_state_id_seq`. The administrator retains DDL privileges. Compare the
restored snapshot before enabling clients and confirm both logins share the same
budget. Use TLS for remote connections.

The committed receipt file retains these **conservative debits**, not measured
provider invoices:

| Receipt | Fetch attempts | Paid calls held | Active time held | USD held |
| --- | ---: | ---: | ---: | ---: |
| November 16 retailer audit | 41 | 0 | 60 minutes | 0.00 |
| Mojitech/Beytech adapter checks | 18 | 0 | 15 minutes | 0.00 |
| International validation envelope | 100 | 60 | 15 minutes | 3.00 |

There were 70 observed direct requests across these artifacts; 100 international
attempt slots are retained for its eleven direct requests plus unmeasured browser
research. The full international paid-call allowance is held rather than treating
unreported browser usage as zero. This does not establish the provider's internal
request count, billed amount, or completion of a campaign-wide billing audit.
The time debits are conservative allowances, not observed worker durations.

At setup, the initial all-market scopes therefore retain 159 fetch attempts,
60 paid-call slots, 90 minutes and $3. The international subset retains 100
attempts, 60 paid-call slots, 15 minutes and $3. Before any other usage, the
remaining initial maxima are **2,841 attempts / 140 paid calls / 90 minutes / $47**;
the international subset has **900 attempts / 0 paid calls / 45 minutes / $12**.
These are overlapping scopes, not additive budgets. Runtime limits and later
usage always take precedence. Paid international work remains blocked by the hold;
unpaid gated retrieval can proceed within the remaining limits. Do not delete
receipts, reset the campaign, or provision another database to reclaim allowances.

## Backup and move to hosted PostgreSQL

The verified backup is a PostgreSQL custom-format dump under
`%LOCALAPPDATA%\TechScout\accounting`. It contains ledger data, not role passwords.
The password file must remain separate. An example backup from a configured
PowerShell shell is:

```powershell
$env:PGPASSFILE = Join-Path $env:LOCALAPPDATA 'TechScout/accounting/accounting.pgpass'
& 'C:/Program Files/PostgreSQL/18/bin/pg_dump.exe' -h 127.0.0.1 -p 5432 -U techscout_accounting -d techscout_accounting -w -Fc --no-owner --no-privileges -f ./var/accounting-backup.dump
```

Keep a protected copy off this PC and test restoration. Before moving online:

1. Pause all accounting writers and dispatchers. Do not run local and hosted
   copies simultaneously as authorities.
2. Take a final dump with a compatible PostgreSQL client. Provision a fresh hosted
   database and restricted runtime role; restore without local ownership/ACLs.
3. Restore **all** ledger state, including campaign ID, scope totals, jobs,
   outstanding reservations, reconciliation receipts and blocked flags. Compare
   the payload with the source before enabling writes. Recovery must not refund
   uncertain attempts or overwrite a newer ledger with an older backup.
4. Reconcile activity since the backup and provider billing before resuming if
   recovering from data loss. Regrant runtime permissions; configure TLS and
   the new endpoint/credentials for every participating process.
5. Verify cross-process budget enforcement, then enable only the hosted authority.
   Retain the old local database as an inactive backup, not a second spending pool.

These instructions preserve ordinary PostgreSQL portability. They do not migrate
the product corpus, raw evidence files, provider keys or buyer application hosting.
