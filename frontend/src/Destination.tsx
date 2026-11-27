import { useEffect, useRef, useState } from 'react';
import { explain, type BuyerApi, type DestinationSeller, type DestinationState } from './api';
import { date, money, safeLink } from './Comparison';
import { sellerNames } from './Locations';

export type DestinationTransport = Pick<BuyerApi, 'destination' | 'declineDestination'>;
type Quote = DestinationState['quotes'][number];
type Revised = NonNullable<DestinationState['revised']>;
type Cost = Revised['offers'][number]['after'];
type QuoteState = NonNullable<DestinationState['selection']>['quote_state'];

/** How often the chat rereads its quotes while a quote is expected, pending or being asked. */
export const QUOTE_POLL_MS = 1500;
/** Rereads before the chat stops looking by itself (about three minutes) and asks for a reload. */
export const MAX_QUOTE_POLLS = 120;
const WAITING = new Set<string>(['pending', 'requested']);

function total(cost?: Cost | null): string {
  if (!cost) return 'Unknown';
  if (cost.status === 'verified') return money(cost.total);
  if (cost.status === 'estimated') return `${money(cost.lower)}–${money(cost.upper)} (estimate)`;
  return cost.known_subtotal ? `${money(cost.known_subtotal)} + unknown fees` : 'Unknown';
}

function place(quote: Quote): string {
  return quote.destination.detail === 'street' ? `${quote.destination.label} · ${quote.destination.city}` : quote.destination.city;
}

function stateText(state: QuoteState, names: string): string {
  return {
    pending: `Waiting to ask ${names} for a delivery quote for this address.`,
    requested: `Asking ${names} for a delivery quote for this address…`,
    quoted: 'Delivery quotes received. The revised totals are below.',
    partial: 'Some sellers gave a usable delivery quote. The others\' totals stay unknown; details are below.',
    unavailable: 'No usable delivery quote came back, so those totals stay unknown. Details are below.',
    not_needed: 'None of these sellers\' totals depends on a street address, so nothing was sent.',
    none: '',
  }[state];
}

function QuoteLine({ quote }: { quote: Quote }) {
  if (quote.status === 'quoted' && quote.fee && quote.item_price && quote.quoted_total) {
    const included = quote.included.length ? ` Already in the price: ${quote.included.join(', ')}.` : '';
    return <li><strong>{quote.seller}</strong> · {place(quote)}: {quote.fee.label} {money(quote.fee.amount)}, added once to the item's {money(quote.item_price)} = {money(quote.quoted_total)}.{included}
      <small className="muted"> Quoted {quote.observed_at ? date(quote.observed_at) : ''} by the seller's own pre-order checkout{quote.capability ? ` (${quote.capability})` : ''}. Nothing was ordered or reserved.</small></li>;
  }
  if (WAITING.has(quote.status)) return <li><strong>{quote.seller}</strong> · {place(quote)}: asking for a delivery quote…</li>;
  return <li><strong>{quote.seller}</strong> · {place(quote)}: {quote.reason}</li>;
}

function RevisedTotals({ revised }: { revised: Revised }) {
  return <div className="revised" aria-label="Totals after delivery quotes">
    <h3>Totals after delivery quotes</h3>
    <p>{revised.advice}</p>
    <div className="table-scroll"><table>
      <thead><tr><th>Seller</th><th>Before</th><th>After</th><th>Rank</th></tr></thead>
      <tbody>{revised.offers.map(offer => {
        const link = safeLink(offer.listing_url);
        return <tr key={offer.offer_id}>
          <td>{link ? <a href={link} target="_blank" rel="noopener noreferrer">{offer.seller} ↗</a> : offer.seller}</td>
          <td>{total(offer.before)}</td>
          <td>{total(offer.after)}</td>
          <td>{offer.rank ?? 'Not ranked'}{offer.doubts.length > 0 && <small className="muted"> · {offer.doubts.join('; ')}</small>}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <small className="muted">Revised {date(revised.revised_at)}. Only verified totals are ranked; equal totals share a rank. You buy on the seller's own site.</small>
  </div>;
}

/**
 * The chat's delivery panel. It asks once for an address only when a seller is waiting for one
 * (its delivery fee depends on the street and it has a reviewed quote route), lets the buyer pick
 * which of those sellers may receive it, records a decline, and shows the delivery quotes and the
 * revised totals as they arrive. The search itself is never resubmitted.
 */
export default function Destination({ api, runId, status, refresh, onChoose, pollMs = QUOTE_POLL_MS }: {
  api: DestinationTransport; runId: string; status: string; refresh: number;
  onChoose: (sellers: DestinationSeller[], recipients: string[]) => void; pollMs?: number;
}) {
  const [state, setState] = useState<DestinationState>(), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [editing, setEditing] = useState(false), [chosen, setChosen] = useState<string[]>([]);
  // tick asks for another read. reads counts finished reads, failed ones included, so a failed
  // read still schedules the next one while quotes are outstanding. polls caps the rereads.
  const [tick, setTick] = useState(0), [reads, setReads] = useState(0), [polls, setPolls] = useState(0);
  const locked = useRef(false);
  useEffect(() => {
    let live = true;
    api.destination(runId).then(next => { if (live) { setState(next); setError(''); } })
      .catch(e => { if (live) setError(explain(e, 'Could not load the delivery address choice for this search.')); })
      .finally(() => { if (live) setReads(value => value + 1); });
    return () => { live = false; };
  }, [api, runId, status, refresh, tick]);
  useEffect(() => { setEditing(false); setChosen([]); setPolls(0); }, [runId, refresh]);
  // Outstanding: a quote the search implies is not planned yet, or one is pending or being asked.
  const outstanding = !!state && (state.awaiting_quotes || WAITING.has(state.selection?.quote_state ?? '')
    || state.quotes.some(quote => WAITING.has(quote.status)));
  const exhausted = polls >= MAX_QUOTE_POLLS;
  useEffect(() => {
    if (!outstanding || exhausted) return;
    const timer = setTimeout(() => { setPolls(value => value + 1); setTick(value => value + 1); }, pollMs);
    return () => clearTimeout(timer);
  }, [outstanding, exhausted, reads, pollMs]);
  async function decline() {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { setState(await api.declineDestination(runId)); setEditing(false); }
    catch (e) { setError(`${explain(e, 'Could not record your choice.')} Nothing was retried automatically.`); }
    finally { locked.current = false; setBusy(false); }
  }
  if (!state) return error ? <p role="alert" className="error">{error}</p> : null;
  const { selection, waiting, quotes, revised } = state;
  if (!selection && !waiting.length && !quotes.length && !revised) return null;
  const toggle = (id: string) => setChosen(chosen.includes(id) ? chosen.filter(item => item !== id) : [...chosen, id]);
  const asking = waiting.length > 0 && (!selection || editing);
  return <section className="panel destination" aria-label="Delivery">
    <span className="eyebrow">DELIVERY · THIS SEARCH ONLY</span>
    {asking ? <>
      <h2>Exact delivery totals may need your address</h2>
      <p><strong>{sellerNames(waiting)}</strong> {waiting.length > 1 ? 'do' : 'does'} not publish a delivery fee for this destination. Their own checkout can quote it for a street address, so their totals stay unknown until then. Choose which of them may receive a saved address for this search. Nothing is sent to a seller you leave unticked.</p>
      <fieldset className="seller-choice"><legend>Sellers allowed to receive your address</legend>
        {waiting.map(seller => <label key={seller.retailer_id}><input type="checkbox" checked={chosen.includes(seller.retailer_id)} onChange={() => toggle(seller.retailer_id)} /> {seller.name}</label>)}
      </fieldset>
      <div className="history-toolbar">
        <button className="primary" disabled={busy || !chosen.length} onClick={() => onChoose(waiting, chosen)}>{state.signed_in ? 'Choose a saved address' : 'Sign in to choose a saved address'}</button>
        <button disabled={busy} onClick={() => void decline()}>Continue without an address</button>
        {editing && <button disabled={busy} onClick={() => setEditing(false)}>Keep current choice</button>}
      </div>
    </> : selection?.status === 'selected' ? <>
      <h2>Using {selection.location?.label} · {selection.location?.city}</h2>
      <p>Allowed to receive this address for this search: <strong>{sellerNames(selection.recipients)}</strong>. No other seller receives it.</p>
      <p className="muted" role="status">{stateText(selection.quote_state, sellerNames(selection.recipients))}</p>
      <div className="history-toolbar">{waiting.length > 0 && <button disabled={busy} onClick={() => setEditing(true)}>Change address or sellers</button>}
        <button disabled={busy} onClick={() => void decline()}>Stop using an address</button></div>
    </> : selection?.status === 'declined' ? <>
      <h2>Continuing without a precise address</h2>
      <p>No seller receives an address in this search. Delivery totals that depend on one remain unknown; every product link still opens the retailer's page.</p>
      {waiting.length > 0 && <button disabled={busy} onClick={() => setEditing(true)}>Use a saved address instead</button>}
    </> : <h2>Delivery quotes for this search</h2>}
    {outstanding && exhausted && <p className="muted" role="status">Still waiting for delivery quotes. Reload the page to check again.</p>}
    {quotes.length > 0 && <div className="quotes"><h3>Delivery quotes</h3><ul>{quotes.map(quote => <QuoteLine key={quote.quote_id} quote={quote} />)}</ul></div>}
    {revised && <RevisedTotals revised={revised} />}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
