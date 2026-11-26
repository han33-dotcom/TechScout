import { useEffect, useRef, useState } from 'react';
import { explain, type BuyerApi, type DestinationSeller, type DestinationState } from './api';
import { sellerNames } from './Locations';

export type DestinationTransport = Pick<BuyerApi, 'destination' | 'declineDestination'>;

/**
 * The chat's single address prompt. It names the sellers once, lets the buyer pick which may
 * receive an address, and records a decline. Retailer quotes for a chosen address belong to #186.
 */
export default function Destination({ api, runId, status, refresh, onChoose }: {
  api: DestinationTransport; runId: string; status: string; refresh: number;
  onChoose: (sellers: DestinationSeller[], recipients: string[]) => void;
}) {
  const [state, setState] = useState<DestinationState>(), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [editing, setEditing] = useState(false), [chosen, setChosen] = useState<string[]>([]);
  const locked = useRef(false);
  useEffect(() => {
    let live = true;
    api.destination(runId).then(next => { if (live) { setState(next); setError(''); } })
      .catch(e => { if (live) setError(explain(e, 'Could not load the delivery address choice for this search.')); });
    return () => { live = false; };
  }, [api, runId, status, refresh]);
  useEffect(() => { setEditing(false); setChosen([]); }, [runId, refresh]);
  async function decline() {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { setState(await api.declineDestination(runId)); setEditing(false); }
    catch (e) { setError(`${explain(e, 'Could not record your choice.')} Nothing was retried automatically.`); }
    finally { locked.current = false; setBusy(false); }
  }
  if (!state) return error ? <p role="alert" className="error">{error}</p> : null;
  const selection = state.selection;
  if (!selection && !state.sellers.length) return null;
  const toggle = (id: string) => setChosen(chosen.includes(id) ? chosen.filter(item => item !== id) : [...chosen, id]);
  return <section className="panel destination" aria-label="Delivery address">
    <span className="eyebrow">DELIVERY ADDRESS · THIS SEARCH ONLY</span>
    {!selection || editing ? <>
      <h2>Exact delivery totals may need your address</h2>
      {state.sellers.length ? <>
        <p>A street address can change what these sellers charge for delivery: <strong>{sellerNames(state.sellers)}</strong>. Choose which of them may receive a saved address for this search. Nothing is sent to a seller you leave unticked.</p>
        <fieldset className="seller-choice"><legend>Sellers allowed to receive your address</legend>
          {state.sellers.map(seller => <label key={seller.retailer_id}><input type="checkbox" checked={chosen.includes(seller.retailer_id)} onChange={() => toggle(seller.retailer_id)} /> {seller.name}</label>)}
        </fieldset>
        <div className="history-toolbar">
          <button className="primary" disabled={busy || !chosen.length} onClick={() => onChoose(state.sellers, chosen)}>{state.signed_in ? 'Choose a saved address' : 'Sign in to choose a saved address'}</button>
          <button disabled={busy} onClick={() => void decline()}>Continue without an address</button>
          {editing && <button disabled={busy} onClick={() => setEditing(false)}>Keep current choice</button>}
        </div>
      </> : <p>No sellers in the latest results can use an address yet.</p>}
    </> : selection.status === 'selected' ? <>
      <h2>Using {selection.location?.label} · {selection.location?.city}</h2>
      <p>Allowed to receive this address for this search: <strong>{sellerNames(selection.recipients)}</strong>. No other seller receives it.</p>
      <p className="muted">Seller delivery quotes for this address have not been requested yet, so the totals and advice above are unchanged. Unknown fees stay unknown.</p>
      <div className="history-toolbar"><button disabled={busy} onClick={() => setEditing(true)}>Change address or sellers</button>
        <button disabled={busy} onClick={() => void decline()}>Stop using an address</button></div>
    </> : <>
      <h2>Continuing without a precise address</h2>
      <p>No seller receives an address in this search. Delivery totals that depend on one remain unknown; every product link still opens the retailer's page.</p>
      <button disabled={busy} onClick={() => setEditing(true)}>Use a saved address instead</button>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
