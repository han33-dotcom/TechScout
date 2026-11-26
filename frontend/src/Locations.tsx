import { useEffect, useRef, useState, type FormEvent } from 'react';
import { explain, type BuyerApi, type DestinationSeller, type LocationList, type SavedLocation } from './api';

export type LocationTransport = Pick<BuyerApi, 'locations' | 'addLocation' | 'deleteLocation'>;
const empty = { label: '', city: '', street: '', details: '' };

export function sellerNames(sellers: DestinationSeller[]): string {
  const names = sellers.map(seller => seller.name);
  return names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** Private saved locations. Only labels and cities are ever shown back; the street stays with the account. */
export default function Locations({ api, choosing, onChoose, onBack }: {
  api: LocationTransport; choosing?: DestinationSeller[];
  onChoose?: (location: SavedLocation) => Promise<void>; onBack: () => void;
}) {
  const [list, setList] = useState<LocationList>(), [draft, setDraft] = useState(empty);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [removing, setRemoving] = useState<string>();
  const locked = useRef(false), live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  async function perform(action: () => Promise<void>, fallback: string) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await action(); }
    catch (e) { if (live.current) setError(`${explain(e, fallback)} Nothing was retried automatically.`); }
    finally { locked.current = false; if (live.current) setBusy(false); }
  }
  const refresh = async () => { const result = await api.locations(); if (live.current) setList(result); };
  useEffect(() => { void perform(refresh, 'Could not load your saved locations.'); }, [api]);
  function add(e: FormEvent) {
    e.preventDefault();
    void perform(async () => {
      await api.addLocation({ label: draft.label, city: draft.city, street: draft.street, details: draft.details.trim() || null });
      setDraft(empty);
      await refresh();
    }, 'Could not save this location.');
  }
  const field = (key: keyof typeof empty, value: string) => setDraft({ ...draft, [key]: value });
  return <section className="panel locations" aria-label="Saved locations">
    <div className="section-heading"><div><span className="eyebrow">YOUR ACCOUNT</span><h2>Saved locations</h2></div>
      <button type="button" disabled={busy} onClick={onBack}>{choosing ? 'Back to your comparison without choosing' : 'Back to your search'}</button></div>
    {choosing && <p className="notice" role="status">Choose the address for your comparison. Only {sellerNames(choosing)} will be allowed to receive it, for this search only.</p>}
    <p className="muted">TechScout shows only each location's label and city. The street address stays private to your account and is never included in history, exports or shared captures.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {list && !list.items.length && <p>No saved locations yet. Add one below.</p>}
    <ul className="history-list">{list?.items.map(location => <li key={location.location_id}>
      <div><strong>{location.label}</strong><small>{location.city} · street address hidden</small></div>
      <div className="location-actions">
        {choosing && onChoose && <button className="primary" disabled={busy} onClick={() => void perform(() => onChoose(location), 'Could not use this address.')}>Use this address</button>}
        {removing === location.location_id
          ? <><button disabled={busy} onClick={() => void perform(async () => { await api.deleteLocation(location.location_id); setRemoving(undefined); await refresh(); }, 'Could not delete this location.')}>Delete {location.label}</button>
            <button disabled={busy} onClick={() => setRemoving(undefined)}>Keep</button></>
          : <button disabled={busy} onClick={() => setRemoving(location.location_id)}>Delete</button>}
      </div></li>)}</ul>
    {removing && <p className="muted">Deleting a location also withdraws it from any search that uses it.</p>}
    {list && list.items.length < list.limit ? <form className="location-form" onSubmit={add} aria-label="Add a location">
      <h3>Add a location</h3>
      <fieldset className="filter-grid" disabled={busy}>
        <label>Label<input required maxLength={40} placeholder="Home, office…" value={draft.label} onChange={e => field('label', e.target.value)} /></label>
        <label>City in Lebanon<input required maxLength={80} value={draft.city} onChange={e => field('city', e.target.value)} /></label>
        <label>Street address<input required maxLength={200} autoComplete="street-address" value={draft.street} onChange={e => field('street', e.target.value)} /></label>
        <label>Building, floor (optional)<input maxLength={200} value={draft.details} onChange={e => field('details', e.target.value)} /></label>
      </fieldset>
      <button className="primary" disabled={busy}>Save location</button>
    </form> : list && <p className="muted">You have saved the maximum of {list.limit} locations. Delete one to add another.</p>}
  </section>;
}
