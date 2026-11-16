"""Validate the checked-in research seed offline; never activate live capabilities."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collection_contracts import RetailerDirectoryEntry
from app.contracts import UtcTime
from pydantic import TypeAdapter


def main():
    seeds = json.loads((ROOT / 'config/retailer-directory-seed.json').read_text(encoding='utf-8'))
    audit = json.loads((ROOT / 'docs/retailer-audit-2026-11-16.json').read_text(encoding='utf-8'))
    entries = [RetailerDirectoryEntry.model_validate(row) for row in seeds]
    ids = [entry.retailer_id for entry in entries]
    if len(ids) != len(set(ids)) or len({str(e.origin) for e in entries}) != len(entries):
        raise ValueError('Duplicate retailer ID or canonical origin')
    findings = {row['retailer_id']: row for row in audit['retailers']}
    if set(findings) != set(ids) or len(findings) != len(audit['retailers']):
        raise ValueError('Seed and audit must have exactly matching identities')
    observations = {row['observation_id']: row for row in audit['observations']}
    if len(observations) != len(audit['observations']):
        raise ValueError('Duplicate observation IDs')
    if audit['method']['direct_probe_count'] != len(observations):
        raise ValueError('Probe count must reflect recorded attempts')
    for row in observations.values():
        TypeAdapter(UtcTime).validate_python(row['checked_at'])
        if row.get('status') == 200 and (len(row.get('sha256', '')) != 64 or row['bytes'] <= 0):
            raise ValueError('Successful direct observations need a body digest and size')
    for entry in entries:
        finding = findings[entry.retailer_id]
        if entry.capability or entry.policy_evidence_ids:
            raise ValueError('Research notes cannot activate capabilities or invent corpus evidence')
        if finding['qualifying_offer_status'] != 'not_established':
            raise ValueError('This audit does not establish qualifying offers')
        if set(finding['category_coverage']) != {'phone', 'laptop', 'tablet', 'monitor', 'audio'}:
            raise ValueError('Record coverage or unknown for every category')
        if set(entry.categories) != {k for k, v in finding['category_coverage'].items()
                                     if v == 'catalog_observed'}:
            raise ValueError('Seed categories must agree with the audit')
        for oid in finding['observation_ids']:
            if oid not in observations:
                raise ValueError('Missing observation')
            if urlsplit(observations[oid]['url']).hostname != entry.origin.host:
                raise ValueError('Observation belongs to a different storefront')
        for urls in finding['entry_points'].values():
            for url in urls:
                parts = urlsplit(url)
                if parts.scheme != 'https' or not parts.hostname or parts.username:
                    raise ValueError('Research entry points must be public HTTPS references')
        for policy in finding['policy_findings']:
            if policy['url'] not in finding['entry_points']['policy']:
                raise ValueError('Policy finding needs its reviewed source route')
    print(f'Validated {len(entries)} retailer seeds and {len(observations)} direct observations offline.')


if __name__ == '__main__':
    main()
