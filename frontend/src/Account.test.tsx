import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import App, { type AccountApi } from './App';
import Account from './Account';
import Locations from './Locations';
import Destination from './Destination';
import { ApiError, type ComparisonWorkspace, type DestinationState, type LocationDraft, type SavedLocation, type SessionInfo } from './api';
import type { Transport } from './useWorkspace';
import fixture from './fixtures/workspace.json';
// The same synthetic accounts and invented addresses the backend tests use.
import synthetic from '../../tests/fixtures/accounts.json';

const [buyer] = synthetic.accounts, [home, office] = synthetic.locations;
const sellers = [{ retailer_id: 'retailer-book-local', name: 'Book-Local' }, { retailer_id: 'retailer-book-other', name: 'Book-Other' }];
const workspace = () => structuredClone(fixture) as unknown as ComparisonWorkspace;
const session = (account: SessionInfo['account'] = null): SessionInfo => ({ csrf_token: 'csrf', expires_at: '2026-11-27T10:00:00Z', account });

function accounts(signedIn = false, saved: SavedLocation[] = []) {
  const items = [...saved];
  // Both sellers wait for a street address (#186), so the chat asks once.
  let state: DestinationState = { request_id: 'needs-1', signed_in: signedIn, sellers, selection: null, waiting: sellers, quotes: [] };
  const mock = {
    session: vi.fn(async () => session(signedIn ? { username: buyer.username } : null)),
    register: vi.fn(async (username: string) => { state = { ...state, signed_in: true }; return session({ username }); }),
    signIn: vi.fn(async (username: string) => { state = { ...state, signed_in: true }; return session({ username }); }),
    signOut: vi.fn(async () => session()),
    locations: vi.fn(async () => ({ items: [...items], limit: 10 })),
    addLocation: vi.fn(async (draft: LocationDraft) => {
      const location = { location_id: `loc-${items.length + 1}`, label: draft.label, city: draft.city, created_at: '2026-11-26T10:00:00Z' };
      items.push(location); return location;
    }),
    deleteLocation: vi.fn(async (id: string) => { items.splice(items.findIndex(item => item.location_id === id), 1); return { deleted: true as const }; }),
    destination: vi.fn(async () => state),
    selectDestination: vi.fn(async (_run: string, locationId: string, recipients: string[]) => {
      const location = items.find(item => item.location_id === locationId)!;
      state = { ...state, selection: { status: 'selected', location, quote_state: 'pending', decided_at: '2026-11-26T10:01:00Z',
        recipients: sellers.filter(seller => recipients.includes(seller.retailer_id)) } };
      return state;
    }),
    declineDestination: vi.fn(async () => {
      state = { ...state, selection: { status: 'declined', location: null, recipients: [], quote_state: 'none', decided_at: '2026-11-26T10:01:00Z' } };
      return state;
    }),
  };
  return mock satisfies AccountApi;
}
function chats(): Transport {
  const w = workspace();
  return { start: vi.fn(), read: vi.fn().mockResolvedValue(w), cancel: vi.fn(), clarify: vi.fn(), refine: vi.fn() };
}
const saved = (label = home.label, id = 'loc-1'): SavedLocation => ({ location_id: id, label, city: home.city, created_at: '2026-11-26T09:00:00Z' });
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }

describe('TechScout account', () => {
  it('creates an account only when both passwords match and never shows the password back', async () => {
    const api = accounts(), signedIn = vi.fn();
    render(<Account api={api} account={null} onSignedIn={signedIn} onSignedOut={vi.fn()} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    fill('Username', buyer.username); fill('Password', buyer.password); fill('Confirm password', 'different-password-1');
    fireEvent.click(screen.getAllByRole('button', { name: 'Create account' })[1]);
    expect((await screen.findByRole('alert')).textContent).toBe('The two passwords do not match.');
    expect(api.register).not.toHaveBeenCalled();
    vi.mocked(api.register).mockRejectedValueOnce(new ApiError(409, 'Request failed (409)', 'That username is not available. Choose another.'));
    fill('Confirm password', buyer.password);
    fireEvent.click(screen.getAllByRole('button', { name: 'Create account' })[1]);
    const alert = await screen.findByText(/That username is not available/);
    expect(alert.textContent).toContain('Nothing was retried automatically.');
    expect(document.body.textContent).not.toContain(buyer.password);
    fireEvent.click(screen.getAllByRole('button', { name: 'Create account' })[1]);
    await waitFor(() => expect(signedIn).toHaveBeenCalledWith({ username: buyer.username }));
    expect(api.register).toHaveBeenCalledTimes(2);
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
    expect(screen.getByLabelText('Password').getAttribute('type')).toBe('password');
  });
  it('shows the same failure for any wrong sign-in and asks before signing out', async () => {
    const api = accounts();
    vi.mocked(api.signIn).mockRejectedValueOnce(new ApiError(401, 'Request failed (401)', 'The username or password is incorrect'));
    const { rerender } = render(<Account api={api} account={null} onSignedIn={vi.fn()} onSignedOut={vi.fn()} onBack={vi.fn()} />);
    fill('Username', 'test-nobody'); fill('Password', 'not-the-password');
    fireEvent.click(screen.getAllByRole('button', { name: 'Sign in' })[1]);
    expect((await screen.findByRole('alert')).textContent).toContain('The username or password is incorrect');
    expect(document.body.textContent).not.toContain('not-the-password');
    const signedOut = vi.fn();
    rerender(<Account api={api} account={{ username: buyer.username }} onSignedIn={vi.fn()} onSignedOut={signedOut} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(api.signOut).not.toHaveBeenCalled();
    expect(screen.getByText(/deletes its searches/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out and clear searches' }));
    await waitFor(() => expect(signedOut).toHaveBeenCalled());
  });
});

describe('saved locations', () => {
  it('shows only labels and cities, adds without keeping the street and deletes after confirmation', async () => {
    const api = accounts(true, [saved()]);
    render(<Locations api={api} onBack={vi.fn()} />);
    expect(await screen.findByText(home.label)).toBeTruthy();
    expect(screen.getByText(`${home.city} · street address hidden`)).toBeTruthy();
    fill('Label', office.label); fill('City in Lebanon', office.city); fill('Street address', office.street);
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    await screen.findByText(office.label);
    expect(api.addLocation).toHaveBeenCalledWith({ label: office.label, city: office.city, street: office.street, details: null });
    expect((screen.getByLabelText('Street address') as HTMLInputElement).value).toBe('');
    expect(document.body.textContent).not.toContain(office.street);
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    expect(api.deleteLocation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: `Delete ${home.label}` }));
    await waitFor(() => expect(screen.queryByText(home.label)).toBeNull());
    expect(api.deleteLocation).toHaveBeenCalledWith('loc-1');
  });
  it('names the chosen sellers when choosing for a comparison and reports a refusal', async () => {
    const choose = vi.fn().mockRejectedValueOnce(new ApiError(404, 'Request failed (404)', 'Location not found')).mockResolvedValue(undefined);
    render(<Locations api={accounts(true, [saved()])} choosing={[sellers[1]]} onChoose={choose} onBack={vi.fn()} />);
    expect(screen.getByRole('status').textContent).toContain('Only Book-Other will be allowed to receive it, for this search only.');
    fireEvent.click(await screen.findByRole('button', { name: 'Use this address' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Location not found');
    fireEvent.click(screen.getByRole('button', { name: 'Use this address' }));
    await waitFor(() => expect(choose).toHaveBeenCalledTimes(2));
  });
});

describe('address prompt in the chat', () => {
  it('names the sellers once, needs an explicit recipient and records a decline', async () => {
    const api = accounts(), choose = vi.fn();
    render(<Destination api={api} runId="run-new" status="complete" refresh={0} onChoose={choose} />);
    expect(await screen.findByText('Book-Local and Book-Other')).toBeTruthy();
    const pick = screen.getByRole('button', { name: 'Sign in to choose a saved address' }) as HTMLButtonElement;
    expect(pick.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('Book-Other'));
    fireEvent.click(pick);
    expect(choose).toHaveBeenCalledWith(sellers, ['retailer-book-other']);
    fireEvent.click(screen.getByRole('button', { name: 'Continue without an address' }));
    expect(await screen.findByText('Continuing without a precise address')).toBeTruthy();
    expect(screen.getByText(/Delivery totals that depend on one remain unknown/)).toBeTruthy();
    expect(api.declineDestination).toHaveBeenCalledWith('run-new');
  });
  it('stays out of the way when the chat has no sellers or choice yet', async () => {
    const api = accounts();
    vi.mocked(api.destination).mockResolvedValue({ request_id: 'r', signed_in: false, sellers: [], selection: null, waiting: [], quotes: [] });
    const { container } = render(<Destination api={api} runId="run-1" status="queued" refresh={0} onChoose={vi.fn()} />);
    await waitFor(() => expect(api.destination).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });
});

describe('same-chat round trip', () => {
  it('signs in from the chat, adds and selects a location, and returns to the very same chat', async () => {
    window.history.replaceState(null, '', '/?chat=run-new');
    const transport = chats(), api = accounts();
    render(<App transport={transport} accountTransport={api} />);
    await screen.findByText('Book-Local and Book-Other');
    expect(screen.getByText(/Previous search · revision 1/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Book-Other'));
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to choose a saved address' }));
    expect(await screen.findByText(/You will come back to this same comparison/)).toBeTruthy();
    expect(window.location.search).toBe('?chat=run-new&view=account');
    fill('Username', buyer.username); fill('Password', buyer.password);
    fireEvent.click(screen.getAllByRole('button', { name: 'Sign in' })[1]);
    expect((await screen.findByRole('status')).textContent).toContain('Only Book-Other will be allowed');
    expect(screen.getByText('Signed in as')).toBeTruthy();
    await screen.findByLabelText('Label');
    fill('Label', home.label); fill('City in Lebanon', home.city); fill('Street address', home.street); fill('Building, floor (optional)', home.details!);
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    await screen.findByText(home.label);
    fill('Label', office.label); fill('City in Lebanon', office.city); fill('Street address', office.street);
    fireEvent.click(screen.getByRole('button', { name: 'Save location' }));
    await screen.findByText(office.label);
    const row = screen.getByText(home.label).closest('li')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Use this address' }));
    expect(await screen.findByText(`Using ${home.label} · ${home.city}`)).toBeTruthy();
    expect(api.selectDestination).toHaveBeenCalledWith('run-new', 'loc-1', ['retailer-book-other']);
    expect(window.location.search).toBe('?chat=run-new');
    // The same chat: its earlier revision, current offers and links, without a new search.
    expect(screen.getByText(/Previous search · revision 1/)).toBeTruthy();
    expect(screen.getAllByRole('link', { name: /View at Book-Local/ }).length).toBeGreaterThan(0);
    expect(screen.getByText('Waiting to ask Book-Other for a delivery quote for this address.')).toBeTruthy();
    expect(transport.start).not.toHaveBeenCalled();
    expect(transport.refine).not.toHaveBeenCalled();
    expect(transport.read).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toContain(home.street);
  });
  it('declines and keeps shopping with the same offers and working links', async () => {
    window.history.replaceState(null, '', '/?chat=run-new');
    const api = accounts();
    render(<App transport={chats()} accountTransport={api} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Continue without an address' }));
    expect(await screen.findByText('Continuing without a precise address')).toBeTruthy();
    const link = screen.getAllByRole('link', { name: /View at Book-Local/ })[0] as HTMLAnchorElement;
    expect(link.href.startsWith('https://')).toBe(true);
    expect(api.selectDestination).not.toHaveBeenCalled();
  });
  it('lets a later signed-in search reuse a saved location with a new choice', async () => {
    window.history.replaceState(null, '', '/?chat=run-new');
    const api = accounts(true, [saved(home.label, 'loc-7')]);
    render(<App transport={chats()} accountTransport={api} />);
    fireEvent.click(await screen.findByLabelText('Book-Local'));
    fireEvent.click(screen.getByRole('button', { name: 'Choose a saved address' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use this address' }));
    expect(await screen.findByText(`Using ${home.label} · ${home.city}`)).toBeTruthy();
    expect(api.selectDestination).toHaveBeenCalledWith('run-new', 'loc-7', ['retailer-book-local']);
    expect(api.signIn).not.toHaveBeenCalled();
  });
  it('clears the chat after sign-out and forgets a chat this session no longer owns', async () => {
    window.history.replaceState(null, '', '/?chat=run-new');
    const api = accounts(true);
    render(<App transport={chats()} accountTransport={api} />);
    await screen.findByText('Book-Local and Book-Other');
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sign out and clear searches' }));
    expect(await screen.findByRole('button', { name: /Find my options/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign in or create account' })).toBeTruthy();
    expect(window.location.search).toBe('');
    window.history.replaceState(null, '', '/?chat=gone');
    const missing = chats();
    vi.mocked(missing.read).mockRejectedValue(new ApiError(404, 'Request failed (404)'));
    render(<App transport={missing} accountTransport={accounts()} />);
    await waitFor(() => expect(window.location.search).toBe(''));
  });
});
