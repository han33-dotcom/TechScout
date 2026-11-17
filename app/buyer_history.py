"""Owned history response contracts and allowlisted, plain-text comparison export."""
from html.parser import HTMLParser
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from app.contracts import Model, Text, UtcTime
from app.contracts_v2 import Category, ComparisonWorkspace


class HistoryEntry(Model):
    run_id: Text
    revision: int
    category: Category
    status: Literal['queued','running','clarification','complete','partial','abstained','cancelled','error']
    updated_at: UtcTime


class HistoryList(Model):
    items: tuple[HistoryEntry, ...]
    expires_at: UtcTime


class HistoricalWorkspace(Model):
    historical: Literal[True] = True
    workspace: ComparisonWorkspace


class DeletionResult(Model):
    deleted: Literal[True] = True


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:
            self.hidden += 1

    def handle_endtag(self,tag):
        if tag in {'script','style'}:
            self.hidden = max(0,self.hidden-1)

    def handle_data(self,data):
        if not self.hidden:
            self.parts.append(data)


def plain(value,limit=300):
    parser=_Plain()
    parser.feed(str(value))
    # Angle brackets also disappear after decoding escaped markup. Controls,
    # bidi overrides and newlines cannot forge additional report fields.
    return re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069<>]',' ',
                  ' '.join(parser.parts))[:limit].strip()


def source_url(value):
    url=urlsplit(str(value))
    if url.scheme!='https' or not url.hostname or url.username or url.password:
        return 'Link omitted'
    # Do not share signed query strings or fragments. The unmodified link stays
    # inside the private workspace, where it can be opened deliberately.
    return urlunsplit(('https',url.netloc,url.path,'',''))[:2048]


def export_comparison(snapshot, *, include_evidence=True):
    """No request text/preferences, internal IDs, sessions, raw HTML or prompts."""
    lines=['TechScout saved comparison','HISTORICAL — recheck prices, stock and eligibility before buying.',
           'Saved: '+snapshot.updated_at.isoformat(),'Category: '+snapshot.request.requirements.category,
           'Outcome: '+snapshot.status,'']

    def fact(value):
        if value is None or value.state!='known':
            return 'Conflicting evidence' if value and value.state=='conflict' else 'Unknown'
        return plain(value.value)

    def money(value):
        return f'{value.currency} {value.amount}'

    def details(offer,cost,label):
        lines.extend([label,plain(offer.variant.product.manufacturer)+' '+plain(offer.variant.product.model),
                      'Seller: '+plain(offer.seller.name),'Product: '+source_url(offer.listing_url)])
        for key,value in offer.variant.specs:
            if key!='category':
                lines.append(plain(key)+': '+fact(value))
        lines.extend(['Color: '+fact(offer.variant.color),'Condition: '+fact(offer.condition),
                      'Payment: '+fact(offer.payment),'Activation: '+fact(offer.activation)])
        if offer.warranty:
            for name in ('provider','months','territory'):
                lines.append('Warranty '+name+': '+fact(getattr(offer.warranty,name)))
        lines.append('Route: '+offer.route.kind+' to Lebanon')
        if offer.arrival and offer.arrival.state=='known':
            arrival=offer.arrival.value
            lines.append(f'Arrival ({arrival.basis}): {arrival.earliest.isoformat()} to {arrival.latest.isoformat()}')
        else:
            lines.append('Arrival: Unknown')
        if cost is None:
            lines.append('Total: Unknown; no payable total established')
        elif cost.status=='verified':
            lines.append('Payable total when checked: '+money(cost.total))
        elif cost.status=='estimated':
            lines.append('Estimated total: '+money(cost.lower)+' to '+money(cost.upper))
            lines.append('Assumptions: '+plain('; '.join(cost.assumptions)))
        else:
            lines.append('Incomplete total; known subtotal: '+(money(cost.known_subtotal) if cost.known_subtotal else 'Unknown'))
            lines.append('Missing charges: '+plain('; '.join(cost.missing)))
        lines.append('Device price: '+(money(offer.price.value) if offer.price.state=='known' else 'Unknown'))
        for charge in offer.costs[:30]:
            lines.append(f'Charge {charge.leg}/{charge.kind}: '+(money(charge.amount) if charge.state=='additional' and charge.amount else charge.state))
        if include_evidence:
            lines.append('Evidence (original observations; URLs omit query strings/fragments):')
            for receipt in offer.evidence[:100]:
                lines.append(plain(receipt.field_path)+' | '+receipt.observed_at.isoformat()+' | '
                             +receipt.scope+' | '+source_url(receipt.origin_url))
        lines.append('')

    for number,rec in enumerate(snapshot.recommendations,1):
        for item in rec.offers()[:20]:
            details(item.offer,item.cost,f'Option {number} — verified for the saved search only')
    # Exclusion reasons can repeat private request constraints; omit them from
    # the shared report. Uncertainty itself and missing costs remain explicit.
    for item in snapshot.uncertain[:20]:
        details(item.offer,item.cost,'Uncertain alternative — not a verified recommendation')
    if not snapshot.recommendations:
        lines.append('No verified recommendation was recorded.')
    return '\n'.join(lines)+'\n'
