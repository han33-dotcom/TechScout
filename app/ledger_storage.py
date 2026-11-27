"""Version 2 accounting storage. The singleton header locks one campaign at a time."""
from collections.abc import MutableMapping
from copy import deepcopy

from sqlalchemy import delete, func, insert, inspect, or_, select, update

from app.storage import accounting_entries as entries, accounting_state


MISSING = object()


def row_values(section, key, value):
    result = dict(section=section, key=key, payload=value)
    if section == 'attempts':
        result.update(job_id=value['job'], status=value['status'], source=value['source'],
                      created=value['created'], expires=value['expires'])
    return result


class Records(MutableMapping):
    """Load records on demand and write back only entries edited inside the transaction."""

    def __init__(self, db, section):
        self.db = db
        self.section = section
        self.original = {}
        self.current = {}
        self.loaded_all = False

    def _load(self, where):
        query = select(entries.c.key, entries.c.payload).where(entries.c.section == self.section, where)
        for key, value in self.db.execute(query):
            if key not in self.original:
                self.original[key] = deepcopy(value)
                self.current[key] = value
            if key in self.current:
                yield key, self.current[key]

    def matching(self, where):
        # Filter on persisted columns before changing the matching entries.
        return [value for _, value in self._load(where)]

    def matching_items(self, where):
        return list(self._load(where))

    def for_job(self, job_id, *, prefix=False):
        condition = entries.c.job_id.startswith(job_id, autoescape=True) if prefix else entries.c.job_id == job_id
        return self.matching(condition)

    def source_counts(self, source, job_id, since):
        recent = entries.c.created > since
        in_job = entries.c.job_id == job_id
        query = select(func.count().filter(recent), func.count().filter(in_job)).where(
            entries.c.section == self.section, entries.c.source == source,
            entries.c.status != 'released', or_(recent, in_job))
        return self.db.execute(query).one()

    def boolean_field(self, key, name):
        query = select(entries.c.payload[name].as_boolean()).where(entries.c.section == self.section, entries.c.key == key)
        return self.db.execute(query).scalar_one()

    def _all(self):
        if not self.loaded_all:
            self.matching(True)
            self.loaded_all = True
        return self.current

    def __getitem__(self, key):
        if key not in self.original and not self.loaded_all:
            self.matching(entries.c.key == key)
            self.original.setdefault(key, MISSING)
        return self.current[key]

    def __setitem__(self, key, value):
        self.get(key)
        self.current[key] = value

    def __delitem__(self, key):
        self[key]
        del self.current[key]

    def __iter__(self):
        return iter(self._all())

    def __len__(self):
        if self.loaded_all:
            return len(self.current)
        count = self.db.scalar(select(func.count()).select_from(entries).where(entries.c.section == self.section))
        added = sum(self.original.get(k, MISSING) is MISSING for k in self.current)
        removed = sum(v is not MISSING and k not in self.current for k, v in self.original.items())
        return count + added - removed

    def flush(self):
        for key in self.current.keys() | self.original.keys():
            old = self.original.get(key, MISSING)
            value = self.current.get(key, MISSING)
            if value == old:
                continue
            target = (entries.c.section == self.section) & (entries.c.key == key)
            if value is MISSING:
                self.db.execute(delete(entries).where(target))
            elif old is MISSING:
                self.db.execute(insert(entries).values(**row_values(self.section, key, value)))
            else:
                self.db.execute(update(entries).where(target).values(**row_values(self.section, key, value)))


def split_state(state):
    fields, sections = {}, []
    for key, value in state.items():
        if isinstance(value, (dict, Records)):
            sections.append(key)
        else:
            fields[key] = value
    return dict(version=2, fields=fields, sections=sorted(sections))


def read_state(db, header):
    if header.get('version') != 2:
        raise ValueError('The accounting administrator must migrate the ledger to version 2')
    return {**deepcopy(header['fields']), **{s: Records(db, s) for s in header['sections']}}


def materialize(state):
    return {k: dict(v) if isinstance(v, Records) else v for k, v in state.items()}


def save_state(db, header, state):
    next_header = split_state(state)
    for section in set(header['sections']) | set(next_header['sections']):
        records = state.get(section)
        if isinstance(records, Records):
            records.flush()
            continue
        if section in header['sections']:
            db.execute(delete(entries).where(entries.c.section == section))
        if isinstance(records, dict) and records:
            db.execute(insert(entries), [row_values(section, k, v) for k, v in records.items()])
    if next_header != header:
        db.execute(update(accounting_state).where(accounting_state.c.id == 1).values(payload=next_header))


def migrate_rows(db, *, expected_project=None):
    """Run only under administrator control. Verify the complete logical round trip before commit."""
    query = select(accounting_state.c.payload).where(accounting_state.c.id == 1).with_for_update()
    old = db.execute(query).scalar_one()
    if old.get('version') == 2:
        if not inspect(db).has_table(entries.name):
            raise ValueError('Existing ledger is missing its entries table; restore it')
        if expected_project is not None and old['fields']['project'] not in (None, expected_project):
            raise ValueError('Ledger belongs to another campaign')
        return
    if 'project' not in old or (expected_project is not None and old['project'] not in (None, expected_project)):
        raise ValueError('Invalid ledger or campaign mismatch')
    entries.create(db, checkfirst=True)
    if db.execute(select(entries.c.key).limit(1)).first():
        raise ValueError('Found ledger entries alongside an unmigrated header')
    save_state(db, {'sections': []}, old)
    header = db.execute(query).scalar_one()
    if materialize(read_state(db, header)) != old:
        raise ValueError('Ledger migration failed its full-state comparison')
