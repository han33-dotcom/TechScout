import type { components } from './api-schema';

export type ShoppingRequest = components['schemas']['ShoppingRequest-Input'];
export type RunReference = components['schemas']['RunReference'];
export type RunSnapshot = components['schemas']['RunSnapshot'];
export type ComparisonWorkspace = components['schemas']['ComparisonWorkspace'];
type SessionInfo = components['schemas']['SessionInfo'];

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
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
  private async send<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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
      // Never automatically repeat a mutation after an ambiguous failure.
      throw new ApiError(response.status, `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
  }
  start(request: ShoppingRequest): Promise<RunSnapshot> {
    return this.send('/api/runs', request);
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
}

/** Drop an older poll after a clarification/refinement changed the visible run. */
export function matchesRun(expected: RunReference, received: RunReference): boolean {
  return expected.run_id === received.run_id && expected.run_revision === received.run_revision
    && expected.request_id === received.request_id && expected.request_revision === received.request_revision;
}
