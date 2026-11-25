import { compareDecimal } from './money';
export { compareDecimal } from './money';
import { useState, type ReactNode } from 'react';
import { matchesRun, type RunSnapshot, type ComparisonWorkspace } from './api';
import type { components } from './api-schema';
type Offer = components['schemas']['Offer'];
type Verified = components['schemas']['VerifiedOffer'];
type Cost = components['schemas']['UncertainAlternative']['cost'];
type Fact = { state: string; value?: unknown; reason?: string | null; evidence_ids?: string[] };
export function safeLink(value: string): string | undefined {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined; } catch { return undefined; }
}
export const date = (value: string) => new Date(value).toLocaleString();
export function fact(value?: Fact | null): string {
  if (!value || value.state !== 'known') return value?.state === 'conflict' ? 'Conflicting evidence' : 'Unknown';
  if (typeof value.value === 'boolean') return value.value ? 'Yes' : 'No';
  if (typeof value.value === 'object') return JSON.stringify(value.value);
  return String(value.value);
}
export const money = (value: { amount: string; currency: string }) => `${value.currency} ${value.amount}`;
export function costText(cost?: Cost) {
  if (!cost) return 'Total unknown';
  if (cost.status === 'verified') return `${money(cost.total)} · verified total`;
  if (cost.status === 'estimated') return `${money(cost.lower)}–${money(cost.upper)} · estimated`;
  return `${cost.known_subtotal ? money(cost.known_subtotal) + ' known subtotal' : 'Subtotal unknown'} · incomplete`;
}
function route(offer: Offer) {
  const r = offer.route;
  if (r.kind === 'pickup') return `Pickup · ${r.destination.city ?? 'Lebanon'} · ${r.branch_id}`;
  if (r.kind === 'forwarded') return `Forwarded via ${r.forwarder_id} · ${r.retailer_leg.origin.country} → ${r.forwarding_leg.origin.country} → Lebanon`;
  if (r.item_eligible.value === true && offer.seller.market === 'LB' &&
      ['seller.name','seller.market'].every(path => offer.evidence.some(e => e.field_path === path &&
        e.origin_url === offer.listing_url && e.variant_id === offer.source_variant_id && !e.conflicts?.length))) {
    return `Lebanese seller · Delivers to ${r.destination.city ?? 'Lebanon'}`;
  }
  if (r.origin_country === 'ZZ') return `Delivery requested to ${r.destination.city ?? 'Lebanon'} · Dispatch origin unknown`;
  return `${r.origin_country === 'LB' ? 'Domestic' : 'International'} delivery · ${r.origin_country} → ${r.destination.city ?? 'Lebanon'}`;
}
function specs(offer: Offer): [string, Fact][] {
  return Object.entries(offer.variant.specs).filter(([key]) => key !== 'category') as [string, Fact][];
}
const labels: Record<string,string> = { storage_gb:'Storage (GB)',ram_gb:'RAM (GB)',cpu:'CPU',gpu:'GPU',operating_system:'Operating system',sim:'SIM',region:'Region',connectivity:'Connectivity',stylus_support:'Stylus support',width_px:'Width (px)',height_px:'Height (px)',refresh_hz:'Refresh rate (Hz)',diagonal_inches:'Size (inches)',panel:'Panel',form_factor:'Form factor',active_noise_cancellation:'Noise cancellation' };
function fieldTime(offer: Offer, field: string) {
  const records = offer.evidence.filter(e => e.field_path === field);
  return records.length ? records.map(e => date(e.observed_at)).join('; ') : 'No observation supplied';
}
function Arrival({ offer }: { offer: Offer }) {
  const arrival = offer.arrival;
  const timing = offer.route.kind === 'pickup' ? 'Pickup timing' : 'Delivery timing';
  return <span>{arrival?.state === 'known' && arrival.value ? `${arrival.value.basis === 'guaranteed' ? 'Guaranteed window' : 'Estimated window'}: ${date(arrival.value.earliest)} – ${date(arrival.value.latest)}` : `${timing}: ${fact(arrival)}`}</span>;
}
function OfferDetails({ offer, cost }: { offer: Offer; cost?: Cost }) {
  return <div className="offer-details"><h4>Configuration & seller terms</h4><dl className="attributes">{specs(offer).map(([key,value]) => <div key={key}><dt>{labels[key] ?? key.replaceAll('_',' ')}</dt><dd>{fact(value)}</dd></div>)}{[['Color',offer.variant.color],['Condition',offer.condition],['Activation',offer.activation],['Payment',offer.payment],['Warranty provider',offer.warranty?.provider],['Warranty months',offer.warranty?.months],['Warranty territory',offer.warranty?.territory]].map(([key,value]) => <div key={String(key)}><dt>{String(key)}</dt><dd>{fact(value as Fact)}</dd></div>)}</dl>
    <h4>Price, charges & freshness</h4><p className="cost-total">{costText(cost)}</p><ul className="charges"><li><span>Device price · {offer.price.state}</span><strong>{offer.price.state === 'known' && offer.price.value ? money(offer.price.value) : 'Unknown'}</strong></li>{offer.costs.map(charge => <li key={`${charge.leg}:${charge.kind}`}><span>{charge.leg} / {charge.kind}<small>{charge.conditions.join('; ')}{charge.reason ? ` · ${charge.reason}` : ''}</small></span><strong>{charge.state === 'additional' && charge.amount ? money(charge.amount) : charge.state.replaceAll('_',' ')}</strong></li>)}</ul>
    {cost?.status === 'incomplete' && <p className="warning">Missing: {cost.missing.join(', ')}. This is not a complete payable total.</p>}
    {cost?.status === 'estimated' && <p>Assumptions: {cost.assumptions.join('; ')}</p>}
    <p className="muted">Price observed: {fieldTime(offer,'price')}<br/>Stock observed: {fieldTime(offer,'stock')}</p><p>{route(offer)}<br/><Arrival offer={offer}/></p>
    <h4>Evidence</h4><div className="evidence-list">{offer.evidence.map(item => <div key={item.evidence_id}><strong>{item.field_path}</strong><span>{date(item.observed_at)} · {item.scope}</span>{safeLink(item.origin_url) ? <a href={safeLink(item.origin_url)} target="_blank" rel="noopener noreferrer">View source ↗</a> : <span>Source link unavailable</span>}{item.excerpt && <p>{item.excerpt}</p>}</div>)}</div>
  </div>;
}
function Fit({ offer, request }: { offer: Offer; request: RunSnapshot['request'] }) {
  const mapping: Record<string,string> = { min_ram_gb:'ram_gb',min_storage_gb:'storage_gb',min_refresh_hz:'refresh_hz',min_width_px:'width_px',min_height_px:'height_px',min_diagonal_inches:'diagonal_inches' };
  const values = offer.variant.specs as unknown as Record<string,Fact>;
  const requested = Object.entries(request.requirements).filter(([key,value]) => key !== 'category' && value != null && !(Array.isArray(value) && !value.length));
  const gaps = [['Warranty',offer.warranty?.months],...('region' in values ? [['Region',values.region]] : []),...(offer.variant.product.category === 'phone' ? [['Activation',offer.activation]] : [])].filter(([,value]) => !value || (value as Fact).state !== 'known');
  return <div className="fit"><h4>{request.intent.kind === 'needs' ? 'Configuration against your needs' : 'Configuration & match'}</h4>{requested.length ? <ul>{requested.map(([key,value]) => <li key={key}>{labels[mapping[key] ?? key] ?? key.replaceAll('_',' ')}: {fact(values[mapping[key] ?? key])} <span className="muted">· requested {Array.isArray(value) ? value.join(', ') : String(value)}</span></li>)}</ul> : <p>See the configuration and its supporting evidence below. A subjective fit explanation has not been supplied.</p>}<p className="muted">Tradeoffs to consider: {gaps.length ? gaps.map(([name]) => `${name} unresolved`).join('; ') : 'Review the seller terms, route and configuration differences below.'}</p></div>;
}

export function currentRecommendations(snapshot: RunSnapshot) {
  return snapshot.recommendations.filter(r => matchesRun(snapshot.run,r.primary.verification.run)).slice(0,3);
}
function UncertainOffer({ item, snapshot, historical }: {
  item: RunSnapshot['uncertain'][number]; snapshot: RunSnapshot; historical: boolean;
}) {
  const { offer, cost, reasons } = item;
  const link = safeLink(offer.listing_url);
  const current = (field: string, value: Fact) => {
    const ids = value.evidence_ids ?? [];
    return !historical && ids.length > 0 && ids.every(id => offer.evidence.some(e =>
      e.evidence_id === id && e.field_path === field && e.run && matchesRun(snapshot.run, e.run)));
  };
  const color = offer.variant.color;
  const variant = specs(offer).filter(([, value]) => value?.state === 'known')
    .map(([key, value]) => `${labels[key] ?? key}: ${fact(value)}`);
  if (color?.state === 'known') variant.unshift(fact(color));
  return <article>
    <h3>{offer.variant.product.manufacturer} {offer.variant.product.model} · {offer.seller.name}</h3>
    {variant.length > 0 && <p className="configuration">{variant.join(' · ')}</p>}
    {offer.price.state === 'known' && offer.price.value &&
      <p><strong>{money(offer.price.value)}</strong> · {current('price', offer.price) ? 'Current listed price' : 'Previously observed listed price'}</p>}
    {offer.stock.state === 'known' && current('stock', offer.stock) &&
      <p>{offer.stock.value ? 'In stock' : 'Out of stock'} · {offer.route.kind === 'pickup' ? 'Selected pickup branch' : 'Online listing'}</p>}
    {link && <a className="retailer-link" href={link} target="_blank" rel="noopener noreferrer">View at {offer.seller.name} ↗</a>}
    <ul>{reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
    <p>{cost?.status === 'incomplete' && !cost.known_subtotal ? 'Payable total incomplete' : costText(cost)}</p>
    <details><summary>Inspect configuration, costs & evidence</summary><OfferDetails offer={offer} cost={cost}/></details>
  </article>;
}

function Identity({ offers }: { offers: Offer[] }) {
  if (offers.length < 2) return <p className="muted">Select at least two offers to inspect their differences.</p>;
  const [first] = offers;
  const sameModel = offers.every(offer => offer.variant.product.product_id === first.variant.product.product_id);
  const same = sameModel && offers.every(offer => offer.variant.variant_id === first.variant.variant_id);
  return <p className="identity">{same ? 'Confirmed canonical configuration match. Seller terms may differ.' : sameModel ? 'Different configurations of the same model.' : 'Different models. Compare their configuration and fit.'} Unresolved fields stay labelled Unknown.</p>;
}
export default function Comparison({ workspace, historical=false }: { workspace: ComparisonWorkspace; historical?: boolean }) {
  const snapshot=workspace.current;
  const [sort,setSort]=useState('recommended'),[selected,setSelected]=useState<string[]>([]),[expanded,setExpanded]=useState<string[]>([]);
  const recommendations=currentRecommendations(snapshot).sort((a,b)=>sort==='price' ? a.primary.cost.total.currency.localeCompare(b.primary.cost.total.currency) || compareDecimal(a.primary.cost.total.amount,b.primary.cost.total.amount) : 0);
  const all=recommendations.flatMap(r=>[r.primary,...r.additional_sellers.filter(v=>matchesRun(snapshot.run,v.verification.run))]);
  const comparison=all.filter(v=>selected.includes(v.offer.offer_id));
  const toggle=(id:string)=>setSelected(old=>{const visible=old.filter(key=>all.some(v=>v.offer.offer_id===key));return visible.includes(id)?visible.filter(x=>x!==id):visible.length<3?[...visible,id]:visible});
  return <section className="comparison" aria-label="Offer comparison"><div className="section-heading"><div><span className="eyebrow">YOUR COMPARISON · REVISION {snapshot.request.revision}</span><h2>{recommendations.length} verified {snapshot.request.intent.kind === 'needs' ? 'model' : 'matching'} {recommendations.length === 1 ? 'option' : 'options'}</h2></div><label className="sort-label">Sort <select value={sort} onChange={e=>setSort(e.target.value)}><option value="recommended">Recommended order</option><option value="price">Verified total (within currency)</option></select></label></div>
    <p className="muted">{historical ? "Historical results for earlier requirements." : "Only offers verified for these requirements appear here."} Fewer than three is a valid result. Costs in different currencies are not ranked as savings.</p>
    {!recommendations.length && <div className="panel empty"><h3>No verified options yet</h3><p>{activeStatus(snapshot) ? 'Research is still underway. Early candidates remain in the checking section.' : 'The available evidence did not establish a verified option. You can adjust your requirements or inspect uncertain offers below.'}</p></div>}
    <div className="recommendations">{recommendations.map((rec,index)=>{
      const item=rec.primary,offer=item.offer;
      const sellers=[item,...rec.additional_sellers.filter(v=>matchesRun(snapshot.run,v.verification.run))];
      return <article className="panel recommendation" key={offer.variant.variant_id}><div className="card-top"><span className="option-index">{String(index+1).padStart(2,'0')}</span><span className="verified-badge">{historical ? "Verified for earlier request" : "Verified in this search"}</span></div><h3>{offer.variant.product.manufacturer} {offer.variant.product.model}</h3><p className="configuration">{specs(offer).slice(0,4).map(([key,value])=>`${labels[key]??key}: ${fact(value)}`).join(' · ')}</p><Fit offer={offer} request={snapshot.request}/><div className="price-line"><strong>{money(item.cost.total)}</strong><span>Verified payable total</span></div><p>{offer.seller.name} · {route(offer)}<br/><Arrival offer={offer}/></p><p className="muted">Price and stock verified {date(item.verification.checked_at)}</p>
        {safeLink(offer.listing_url) && <a className="retailer-link" href={safeLink(offer.listing_url)} target="_blank" rel="noopener noreferrer">View at {offer.seller.name} ↗</a>}
        <div className="seller-list"><h4>{sellers.length} qualifying {sellers.length===1?'seller':'sellers'} for this configuration</h4>{sellers.map(seller=><div key={seller.offer.offer_id} className="seller-row"><div><strong>{seller.offer.seller.name}</strong><span>{money(seller.cost.total)} · {fact(seller.offer.condition)}</span></div><label><input type="checkbox" checked={selected.includes(seller.offer.offer_id)} disabled={!selected.includes(seller.offer.offer_id)&&comparison.length>=3} onChange={()=>toggle(seller.offer.offer_id)}/> Compare</label><details open={expanded.includes(seller.offer.offer_id)} onToggle={e=>{const open=e.currentTarget.open;setExpanded(old=>open?[...new Set([...old,seller.offer.offer_id])]:old.filter(x=>x!==seller.offer.offer_id))}}><summary>Costs, terms & evidence</summary><OfferDetails offer={seller.offer} cost={seller.cost}/>{safeLink(seller.offer.listing_url)&&<a href={safeLink(seller.offer.listing_url)} target="_blank" rel="noopener noreferrer">Open product page ↗</a>}</details></div>)}</div>
      </article>;
    })}</div>
    {comparison.length>0 && <section className="panel side-by-side" aria-label="Side-by-side comparison"><h3>Side by side <span className="badge">Up to 3 offers</span></h3><Identity offers={comparison.map(v=>v.offer)}/><div className="table-scroll"><table><thead><tr><th>Attribute</th>{comparison.map(v=><th key={v.offer.offer_id}>{v.offer.variant.product.model}<small>{v.offer.seller.name}</small></th>)}</tr></thead><tbody>{comparisonRows(comparison).map(([label,values])=><tr key={label}><th>{label}</th>{values.map((value,index)=><td key={comparison[index].offer.offer_id}>{value}</td>)}</tr>)}</tbody></table></div></section>}
    <details className="panel uncertain" open={snapshot.uncertain.length>0}>
      <summary>Uncertain / non-qualifying offers ({snapshot.uncertain.length})</summary>
      {snapshot.uncertain.map(item => <UncertainOffer key={item.offer.offer_id} item={item} snapshot={snapshot} historical={historical}/>)}
    </details>
    <section className="panel source-coverage"><h3>Where the evidence came from</h3><p className="muted">Inventory and discovery counts do not mean every retailer was checked.</p>{snapshot.source_outcomes?.length ? <ul>{snapshot.source_outcomes.map(source=><li key={source.source_id}><strong>{source.name}</strong><span className={`source-${source.status}`}>{source.status}</span>{source.reason&&<p>{source.reason}</p>}</li>)}</ul> : <p>Per-retailer check details are not available yet. The progress counts above show reported coverage; no missing source is assumed checked.</p>}<p className="muted">{new Set(all.map(v=>v.offer.seller.seller_id)).size} qualifying sellers across {recommendations.length} displayed options. Multiple sellers are grouped under the same canonical configuration.</p></section>
  </section>;
}
function activeStatus(s:RunSnapshot){return ['queued','running','clarification'].includes(s.status)}
function comparisonRows(items:Verified[]): [string,ReactNode[]][] {
  const rows:[string,((v:Verified)=>ReactNode)][]=[['Verified total',v=>money(v.cost.total)],['Route',v=>route(v.offer)],['Condition',v=>fact(v.offer.condition)],['Activation',v=>fact(v.offer.activation)],['Color',v=>fact(v.offer.variant.color)],['Warranty',v=>`${fact(v.offer.warranty?.provider)} · ${fact(v.offer.warranty?.months)} months · ${fact(v.offer.warranty?.territory)}`],['Verification',v=>date(v.verification.checked_at)]];
  const keys=[...new Set(items.flatMap(v=>specs(v.offer).map(([key])=>key)))];
  for(const key of keys)rows.push([labels[key]??key,v=>fact((v.offer.variant.specs as unknown as Record<string,Fact>)[key])]);
  return rows.map(([label,read])=>[label,items.map(read)]);
}
