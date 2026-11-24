"""Measure five discovery channels against paired public development queries."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

from app.accounting import Ledger
from app.contracts_v2 import RunReference, ShoppingRequest, VerifiedOffer
from app.directory import Directory
from app.domestic_buyer import current_problem, domestic_assessment
from app.domestic_buyer_sources import DomesticBuyerSources
from app.domestic_discovery import CHANNELS, CHANNEL_LIMITS, DISCOVERY_ENV, DomesticDiscovery, SearchObjective
from app.inference import load_env_file
from app.onboarding import ActivationRegistry
from app.sources import SourceGate
from app.storage import Storage

PAIRS = (
    ('phone','iPhone 17','Beirut','بيروت'),
    ('laptop','ASUS Zenbook 14','Beirut','بيروت'),
    ('tablet','iPad Air','Tripoli','طرابلس'),
    ('monitor','ASUS ProArt PA278QV','Beirut','بيروت'),
    ('audio','Soundcore H30i','Sidon','صيدا'),
)


def measurement_id(series, category, lang, channels):
    job_id = 'development-channels:'+series+':'+category+':'+lang
    if tuple(channels) != CHANNELS:
        job_id += ':' + '-'.join(channels)
    return job_id


def measure(ledger, discovery, reader, category, model, city, query, lang, series, channels=CHANNELS):
    job_id = measurement_id(series, category, lang, channels)
    job = ledger.open_job(job_id,'maintenance',envelope=dict(calls=9,seconds=110,usd='0.25'))
    with ledger._transaction() as (state, stamp):
        marks = state.setdefault('channel_calibration',{})
        if job_id in marks:
            saved = marks[job_id]
            if not saved:
                raise ValueError('An earlier dispatch has no acknowledgement; inspect its ledger')
            if saved['query'] != query:
                raise ValueError('Series ID already belongs to a different public query')
            return saved
        marks[job_id] = {}
    start = datetime.fromtimestamp(stamp,timezone.utc)
    objective = SearchObjective(query,'Find domestic competing sources for the exact model',language=lang,limit=5)
    report = discovery.survey(objective,job,channels)
    req = ShoppingRequest(request_id=job_id,intent=dict(kind='exact_model',model=model),
                          requirements=dict(category=category),fulfillment=dict(mode='delivery',destination=dict(city=city)))
    run = RunReference(run_id=job_id,run_revision=1,request_id=job_id,request_revision=1)
    verified, checked = [], []
    for lead in (lead for lead in report.leads if lead.source_id):
        if len(checked) == 2:
            break
        read = reader.read(req,lead.url,job,run,room=lambda:max(0,110-(datetime.now(timezone.utc)-start).total_seconds()))
        checked.append(dict(seller=lead.seller_key,status=read.status))
        for offer in read.offers:
            now = datetime.now(timezone.utc)
            assessed = domestic_assessment(req,offer,now)
            if assessed.decision == 'recommendable' and current_problem(offer,assessed,read,run,start,now) is None:
                verified.append(VerifiedOffer(offer=offer,cost=assessed.cost,verification=dict(run=run,checked_at=now)))
    with ledger._transaction() as (state, now):
        attempts=[a for a in state['attempts'].values() if a['job']==job_id and a['status']!='released']
        seller_keys = {lead.seller_key for lead in report.leads}
        result=dict(job_id=job_id,category=category,language=lang,query=query,started_at=start.isoformat(),
            channels=[asdict(a) for a in report.attempts],coverage=report.coverage(verified),direct_checks=checked,
            distinct_leads_by_channel={
                channel: len({lead.seller_key for lead in report.leads
                              if any(attribution.channel == channel for attribution in lead.attribution)})
                for channel in channels},
            distinct_lead_count=len(seller_keys),calls=len(attempts),
            spend_usd=str(sum((Decimal(a['values'][3]) for a in attempts if a['status']=='completed'),Decimal(0))),
            unresolved_usd=str(sum((Decimal(a['values'][3]) for a in attempts if a['status']!='completed'),Decimal(0))))
        if 'brave' in channels:
            result['direct_checks'] = [dict(status=check['status']) for check in checked]
        else:
            result['seller_keys'] = sorted(seller_keys)
        result=json.loads(json.dumps(result,default=str))
        state['channel_calibration'][job_id]=result
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series',required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--channels',nargs='+',choices=CHANNELS,default=CHANNELS)
    args=parser.parse_args()
    if not args.series.isascii() or not args.series.replace('-','').isalnum() or len(args.series)>40:
        raise ValueError('Use a short public series identifier')
    if len(set(args.channels)) != len(args.channels):
        parser.error('select each discovery channel once')
    load_env_file(names=DISCOVERY_ENV)
    storage,ledger=Storage(),Ledger.configured()
    try:
        registry=ActivationRegistry(storage)
        gate=SourceGate(registry)
        directory=Directory(storage,registry_path=registry)
        defaults=json.loads((Path(__file__).resolve().parents[1]/'config/maintenance.json').read_bytes())['manufacturer_pages']
        discovery=DomesticDiscovery.from_env(gate,directory,directory_defaults=defaults)
        reader=DomesticBuyerSources(storage,gate,directory)
        args.output.mkdir(parents=True,exist_ok=True)
        # Spread paired objectives over the shared source windows, even on a paid-only rerun.
        per_minute = 2 if 'manufacturer' in args.channels else CHANNEL_LIMITS.requests_per_minute
        delay_seconds = 60 / per_minute + 1
        for category, *_ in PAIRS:
            for lang in ('en', 'ar'):
                path = args.output / f'{category}-{lang}.json'
                if path.exists() and json.loads(path.read_bytes()).get('job_id') != measurement_id(
                        args.series, category, lang, args.channels):
                    raise ValueError('Use a separate output directory for each channel series')
        for category,model,city,arabic_city in PAIRS:
            for lang,query in [('en',f'{model} retailer {city} Lebanon'),('ar',f'{model} متجر {arabic_city} لبنان')]:
                out=args.output/(category+'-'+lang+'.json')
                result=measure(ledger,discovery,reader,category,model,city,query,lang,args.series,args.channels)
                out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
                print(category,lang,[(a['channel'],a['status']) for a in result['channels']],flush=True)
                if (category,lang) != (PAIRS[-1][0], 'ar'):
                    time.sleep(delay_seconds)
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__=='__main__':
    main()
