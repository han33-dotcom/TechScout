import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import Destination, { MAX_QUOTE_POLLS } from './Destination';
import type { DestinationState } from './api';

// Synthetic chat for #186: City-Shop is quoted for the search's city at once; Street-A and Street-B
// wait for a street address. Amounts mirror tests/test_delivery_quotes.py.
type Quote = DestinationState['quotes'][number];
type Revised = NonNullable<DestinationState['revised']>;
const run = { run_id: 'run-q', run_revision: 1, request_id: 'needs-q', request_revision: 2 };
const streetA = { retailer_id: 'retailer-street-a', name: 'Street-A' }, streetB = { retailer_id: 'retailer-street-b', name: 'Street-B' };
const usd = (amount: string) => ({ amount, currency: 'USD' });
const verified = (amount: string) => ({ status: 'verified' as const, total: usd(amount), budget_conversion: null });
const open = (amount: string) => ({ status: 'incomplete' as const, known_subtotal: usd(amount), missing: ['costs.order.shipping'] });
const home = { location_id: 'loc-1', label: 'Test home', city: 'Beirut', created_at: '2026-11-26T09:00:00Z' };

function quote(seller: string, status: Quote['status'], extra: Partial<Quote> = {}): Quote {
  const slug = seller.toLowerCase();
  return { quote_id: `q-${slug}`, run, offer_id: slug, retailer_id: `retailer-${slug}`, seller, source_id: `source-${slug}`,
    source_variant_id: '4101', listing_url: `https://${slug}.example/${slug}`, quantity: 1, included: [],
    destination: { detail: 'city', city: 'Beirut', location_id: null, label: null }, status,
    requested_at: '2026-11-27T10:00:00Z', ...extra };
}
function quoted(seller: string, price: string, fee: string, totalAmount: string, extra: Partial<Quote> = {}): Quote {
  return quote(seller, 'quoted', { observed_at: '2026-11-27T10:00:02Z', capability: `source-${seller.toLowerCase()}-quote@1`,
    endpoint: `https://${seller.toLowerCase()}.example/api/2025-01/graphql.json`, response_sha256: 'a'.repeat(64),
    item_price: usd(price), fee: { label: 'Standard', amount: usd(fee) }, quoted_total: usd(totalAmount), included: ['tax'], ...extra });
}
const street = { detail: 'street' as const, city: 'Beirut', location_id: 'loc-1', label: 'Test home' };
function revision(offers: Revised['offers'], advice: string): Revised {
  return { destination: 'Beirut', revised_at: '2026-11-27T10:00:05Z', offers, advice };
}
const row = (seller: string, before: Revised['offers'][number]['before'], after: Revised['offers'][number]['after'],
  rank: number | null, doubts: string[] = []) =>
  ({ offer_id: seller.toLowerCase(), retailer_id: `retailer-${seller.toLowerCase()}`, seller,
    listing_url: `https://${seller.toLowerCase()}.example/${seller.toLowerCase()}`, before, after, rank, doubts });

function chat(states: DestinationState[]) {
  const sequence = [...states];
  return { destination: vi.fn(async () => sequence.length > 1 ? sequence.shift()! : sequence[0]), declineDestination: vi.fn() };
}
const base = (extra: Partial<DestinationState>): DestinationState =>
  ({ request_id: 'needs-q', signed_in: true, sellers: [streetA, streetB], selection: null, waiting: [], quotes: [],
    awaiting_quotes: false, ...extra });

describe('delivery quotes in the chat (#186)', () => {
  it('hides the address question when no seller needs a street address but still shows a city quote', async () => {
    const api = chat([base({ quotes: [quoted('City-Shop', '915', '5.00', '920.00')], revised: revision(
      [row('Book-Local', verified('930'), verified('930'), 2), row('City-Shop', open('915'), verified('920.00'), 1)],
      'For delivery to Beirut, City-Shop has the lowest verified total: USD 920.')})]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} />);
    expect(await screen.findByText('Delivery quotes for this search')).toBeTruthy();
    expect(screen.queryByText('Exact delivery totals may need your address')).toBeNull();
    expect(screen.queryByRole('button', { name: /saved address/ })).toBeNull();
    expect(screen.getByText(/Standard USD 5.00, added once to the item's USD 915 = USD 920.00/)).toBeTruthy();
    expect(screen.getByText(/Already in the price: tax/)).toBeTruthy();
    expect(screen.getByText(/Nothing was ordered or reserved/)).toBeTruthy();
    const table = screen.getByRole('table');
    const cityRow = within(table).getByText('City-Shop ↗').closest('tr')!;
    expect(within(cityRow).getByText('USD 915 + unknown fees')).toBeTruthy();
    expect(within(cityRow).getByText('USD 920.00')).toBeTruthy();
    expect(within(cityRow).getByText('1')).toBeTruthy();
    expect(screen.getByText('For delivery to Beirut, City-Shop has the lowest verified total: USD 920.')).toBeTruthy();
  });

  it('names only the waiting sellers once and lets the buyer pick among them', async () => {
    const api = chat([base({ waiting: [streetA, streetB] })]), choose = vi.fn();
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={choose} />);
    expect(await screen.findByText('Street-A and Street-B')).toBeTruthy();
    expect(screen.getByText(/do not publish a delivery fee for this destination/)).toBeTruthy();
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    fireEvent.click(screen.getByLabelText('Street-A'));
    fireEvent.click(screen.getByRole('button', { name: 'Choose a saved address' }));
    expect(choose).toHaveBeenCalledWith([streetA, streetB], ['retailer-street-a']);
  });

  it('resumes by itself after the choice: it rereads while asking, then shows the revised totals and stops', async () => {
    const selection = (quote_state: NonNullable<DestinationState['selection']>['quote_state']) =>
      ({ status: 'selected' as const, location: home, recipients: [streetA], quote_state, decided_at: '2026-11-27T10:00:00Z' });
    const final = base({ waiting: [streetA, streetB], selection: selection('quoted'),
      quotes: [quoted('Street-A', '905', '5.00', '910.00', { destination: street })],
      revised: revision([row('Street-A', open('905'), verified('910.00'), 1), row('Book-Local', verified('930'), verified('930'), 2),
        row('Street-B', open('900'), open('900'), null, ['payable total is incomplete'])],
        'For delivery to Test home · Beirut, Street-A has the lowest verified total: USD 910. Before the delivery quotes, Book-Local was the cheapest verified offer at USD 930.') });
    const api = chat([
      base({ waiting: [streetA, streetB], selection: selection('pending') }),
      base({ waiting: [streetA, streetB], selection: selection('requested'), quotes: [quote('Street-A', 'requested', { destination: street })] }),
      final,
    ]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} pollMs={5} />);
    expect(await screen.findByText('Waiting to ask Street-A for a delivery quote for this address.')).toBeTruthy();
    expect(await screen.findByText('Delivery quotes received. The revised totals are below.')).toBeTruthy();
    expect(screen.getByText(/Before the delivery quotes, Book-Local was the cheapest verified offer at USD 930/)).toBeTruthy();
    expect(screen.getByText(/Test home · Beirut: Standard USD 5.00/)).toBeTruthy();
    const rowB = within(screen.getByRole('table')).getByText('Street-B ↗').closest('tr')!;
    expect(within(rowB).getByText('Not ranked')).toBeTruthy();
    expect(screen.queryByText('Exact delivery totals may need your address')).toBeNull();
    const reads = api.destination.mock.calls.length;
    await new Promise(done => setTimeout(done, 40));
    expect(api.destination.mock.calls.length).toBe(reads);
  });

  it('keeps unusable and conflicting quotes visible with their reasons, unknown totals and links', async () => {
    const selection = { status: 'selected' as const, location: home, recipients: [streetA, streetB], quote_state: 'unavailable' as const,
      decided_at: '2026-11-27T10:00:00Z' };
    const conflict = 'The pre-order step charged USD 950 for the item, but the listing shows USD 900. Its total stays unknown.';
    const api = chat([base({ waiting: [streetA, streetB], selection, quotes: [
      quote('Street-A', 'unavailable', { destination: street, reason: 'The seller could not deliver to this destination.' }),
      quote('Street-B', 'conflicting', { destination: street, reason: conflict, item_price: usd('950'), fee: { label: 'Standard', amount: usd('5') } })],
      revised: revision([row('Street-A', open('905'), open('905'), null, ['The seller could not deliver to this destination.']),
        row('Street-B', open('900'), open('900'), null, [conflict])],
        'No offer has a verified total for delivery to Test home · Beirut yet. Street-A gave no usable delivery quote, so its total stays unknown.') })]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} />);
    expect(await screen.findByText('No usable delivery quote came back, so those totals stay unknown. Details are below.')).toBeTruthy();
    expect(screen.getAllByText(/could not deliver to this destination/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(new RegExp('charged USD 950 for the item')).length).toBeGreaterThan(0);
    const links = screen.getAllByRole('link') as HTMLAnchorElement[];
    expect(links.map(link => link.href)).toEqual(['https://street-a.example/street-a', 'https://street-b.example/street-b']);
    expect(screen.getAllByText('USD 905 + unknown fees')).toHaveLength(2);
    expect(screen.queryByText(/USD 955/)).toBeNull();
  });

  it('records a decline and offers the address again only to sellers still waiting', async () => {
    const declined = base({ waiting: [streetA], selection: { status: 'declined', location: null, recipients: [], quote_state: 'none',
      decided_at: '2026-11-27T10:00:00Z' } });
    const api = chat([declined]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} />);
    expect(await screen.findByText('Continuing without a precise address')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Use a saved address instead' }));
    expect(screen.getByLabelText('Street-A')).toBeTruthy();
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
  });

  it('looks again while a city quote is expected but not planned yet, then shows it without a reload', async () => {
    const quotedState = base({ quotes: [quoted('City-Shop', '915', '5.00', '920.00')], revised: revision(
      [row('City-Shop', open('915'), verified('920.00'), 1)], 'For delivery to Beirut, City-Shop has the lowest verified total: USD 920.') });
    const api = chat([base({ awaiting_quotes: true }), base({ awaiting_quotes: true }), quotedState]);
    const { container } = render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} pollMs={5} />);
    await waitFor(() => expect(api.destination).toHaveBeenCalledTimes(1));
    expect(container.textContent).toBe('');
    expect(await screen.findByText(/City-Shop has the lowest verified total/)).toBeTruthy();
    const reads = api.destination.mock.calls.length;
    await new Promise(done => setTimeout(done, 40));
    expect(api.destination.mock.calls.length).toBe(reads);
  });

  it('keeps looking after a failed read while a quote is being asked', async () => {
    const selection = (quote_state: NonNullable<DestinationState['selection']>['quote_state']) =>
      ({ status: 'selected' as const, location: home, recipients: [streetA], quote_state, decided_at: '2026-11-27T10:00:00Z' });
    const api = { declineDestination: vi.fn(), destination: vi.fn()
      .mockResolvedValueOnce(base({ waiting: [streetA], selection: selection('requested') }))
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue(base({ waiting: [streetA], selection: selection('quoted'),
        quotes: [quoted('Street-A', '905', '5.00', '910.00', { destination: street })] })) };
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} pollMs={5} />);
    expect(await screen.findByText('Asking Street-A for a delivery quote for this address…')).toBeTruthy();
    expect(await screen.findByText('Delivery quotes received. The revised totals are below.')).toBeTruthy();
    expect(api.destination.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('stops looking by itself after the polling limit and asks for a reload', async () => {
    const api = chat([base({ waiting: [streetA], selection: { status: 'selected', location: home, recipients: [streetA],
      quote_state: 'pending', decided_at: '2026-11-27T10:00:00Z' } })]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} pollMs={1} />);
    expect(await screen.findByText('Still waiting for delivery quotes. Reload the page to check again.', {}, { timeout: 4000 })).toBeTruthy();
    expect(api.destination).toHaveBeenCalledTimes(MAX_QUOTE_POLLS + 1);
    await new Promise(done => setTimeout(done, 30));
    expect(api.destination).toHaveBeenCalledTimes(MAX_QUOTE_POLLS + 1);
  });

  it('shows a choice that needed no quote without offering to change sellers that are not waiting', async () => {
    const api = chat([base({ selection: { status: 'selected', location: home, recipients: [streetA], quote_state: 'not_needed',
      decided_at: '2026-11-27T10:00:00Z' } })]);
    render(<Destination api={api} runId="run-q" status="complete" refresh={0} onChoose={vi.fn()} />);
    expect(await screen.findByText("None of these sellers' totals depends on a street address, so nothing was sent.")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Change address or sellers' })).toBeNull();
    await waitFor(() => expect(api.destination).toHaveBeenCalledTimes(1));
  });
});
