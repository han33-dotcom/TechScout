import type { components } from './api-schema';

export type ShoppingRequest = components['schemas']['DomesticShoppingRequest'];
export type RunReference = components['schemas']['RunReference'];
export type RunSnapshot = components['schemas']['RunSnapshot'];
export type ComparisonWorkspace = components['schemas']['ComparisonWorkspace'];
export type HistoryList = components['schemas']['HistoryList'];
export type HistoricalWorkspace = components['schemas']['HistoricalWorkspace'];
export type SessionInfo = components['schemas']['SessionInfo'];
export type AccountSummary = components['schemas']['AccountSummary'];
export type SavedLocation = components['schemas']['SavedLocation'];
export type LocationList = components['schemas']['LocationList'];
export type LocationDraft = components['schemas']['LocationDraft'];
export type DestinationState = components['schemas']['DestinationState'];
export type DestinationSeller = components['schemas']['DestinationSeller'];

export class ApiError extends Error {
  /** detail is the server's plain-text explanation, when it sent one. */
  constructor(public status: number, message: string, public detail?: string) { super(message); }
}

/** Same-origin credentials only. No provider key or provider SDK belongs here. */
export class BuyerApi {
  private csrf?: string;
  private pendingSession?: Promise<SessionInfo>;
  async session(): Promise<SessionInfo> {
    if (!this.pendingSession) {
      this.pendingSession = this.send<SessionInfo>('/api/session').then(result => {
        this.csrf = result.csrf_token;
        return result;
      }).finally(() => { this.pendingSession = undefined; });
    }
    return this.pendingSession;
  }
  private async send<T>(path: string, body?: unknown, signal?: AbortSignal, format: 'json' | 'text' = 'json'): Promise<T> {
    if (body !== undefined && !this.csrf) await this.session();
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin', cache: 'no-store', signal,
      headers: body === undefined ? undefined : {
        'Content-Type': 'application/json', 'X-CSRF-Token': this.csrf!,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      if (response.status === 401) this.csrf = undefined;
      const body: unknown = await response.json().catch(() => undefined);
      const detail = body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string'
        ? (body as { detail: string }).detail : undefined;
      // Never automatically repeat a mutation after an ambiguous failure.
      throw new ApiError(response.status, `Request failed (${response.status})`, detail);
    }
    return (format === 'text' ? response.text() : response.json()) as Promise<T>;
  }
  start(request: ShoppingRequest): Promise<RunSnapshot> {
    return this.send('/api/runs', request);
  }
  async history(): Promise<HistoryList> {
    await this.session();
    return this.send('/api/history');
  }
  reopen(runId: string): Promise<HistoricalWorkspace> {
    return this.send(`/api/history/${encodeURIComponent(runId)}`);
  }
  deleteHistory(runId: string): Promise<{ deleted: true }> {
    return this.send(`/api/history/${encodeURIComponent(runId)}/delete`, {});
  }
  exportHistory(runId: string, includeEvidence: boolean): Promise<string> {
    return this.send(`/api/history/${encodeURIComponent(runId)}/export?include_evidence=${includeEvidence}`, undefined, undefined, 'text');
  }
  read(runId: string, signal?: AbortSignal): Promise<ComparisonWorkspace> {
    return this.send(`/api/runs/${encodeURIComponent(runId)}`, undefined, signal);
  }
  cancel(run: RunReference): Promise<RunSnapshot> {
    return this.send(`/api/runs/${encodeURIComponent(run.run_id)}/cancel`, { run });
  }
  clarify(run: RunReference, clarificationId: string, answer: string): Promise<RunSnapshot> {
    return this.send(`/api/runs/${encodeURIComponent(run.run_id)}/clarify`,
      { run, clarification_id: clarificationId, answer });
  }
  refine(run: RunReference, request: ShoppingRequest): Promise<ComparisonWorkspace> {
    return this.send(`/api/runs/${encodeURIComponent(run.run_id)}/refine`, { run, request });
  }
  /** Register, sign in and sign out each issue a new session cookie and CSRF token. */
  private async rotate(path: string, body: object): Promise<SessionInfo> {
    const result = await this.send<SessionInfo>(path, body);
    this.csrf = result.csrf_token;
    return result;
  }
  register(username: string, password: string): Promise<SessionInfo> {
    return this.rotate('/api/account/register', { username, password });
  }
  signIn(username: string, password: string): Promise<SessionInfo> {
    return this.rotate('/api/account/sign-in', { username, password });
  }
  signOut(): Promise<SessionInfo> {
    return this.rotate('/api/account/sign-out', {});
  }
  locations(): Promise<LocationList> {
    return this.send('/api/account/locations');
  }
  addLocation(draft: LocationDraft): Promise<SavedLocation> {
    return this.send('/api/account/locations', draft);
  }
  deleteLocation(locationId: string): Promise<{ deleted: true }> {
    return this.send(`/api/account/locations/${encodeURIComponent(locationId)}/delete`, {});
  }
  destination(runId: string): Promise<DestinationState> {
    return this.send(`/api/runs/${encodeURIComponent(runId)}/destination`);
  }
  selectDestination(runId: string, locationId: string, recipients: string[]): Promise<DestinationState> {
    return this.send(`/api/runs/${encodeURIComponent(runId)}/destination`, { location_id: locationId, recipients });
  }
  declineDestination(runId: string): Promise<DestinationState> {
    return this.send(`/api/runs/${encodeURIComponent(runId)}/destination/decline`, {});
  }
}

/** The server's explanation for an account, location or destination failure, never the submitted values. */
export function explain(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.detail ? error.detail : fallback;
}

/** Drop an older poll after a clarification/refinement changed the visible run. */
export function matchesRun(expected: RunReference, received: RunReference): boolean {
  return expected.run_id === received.run_id && expected.run_revision === received.run_revision
    && expected.request_id === received.request_id && expected.request_revision === received.request_revision;
}
