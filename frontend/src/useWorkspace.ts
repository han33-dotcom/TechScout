import { useCallback, useEffect, useRef, useState } from 'react';
import { BuyerApi, matchesRun, type ComparisonWorkspace, type RunSnapshot, type ShoppingRequest } from './api';
export type Transport = Pick<BuyerApi, 'start' | 'read' | 'cancel' | 'clarify' | 'refine'>;
export const active = (s?: RunSnapshot) => !!s && ['queued', 'running', 'clarification'].includes(s.status);
const envelope = (current: RunSnapshot, previous?: RunSnapshot | null): ComparisonWorkspace => ({ schema_version: 2, current, previous });
export function useWorkspace(api: Transport) {
  const [workspace, setWorkspace] = useState<ComparisonWorkspace>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const current = useRef<ComparisonWorkspace | undefined>(undefined), generation = useRef(0), lock = useRef(false);
  const poll = useRef<AbortController | undefined>(undefined);
  const commit = useCallback((next: ComparisonWorkspace) => { current.current = next; setWorkspace(next); }, []);
  useEffect(() => () => { generation.current++; poll.current?.abort(); }, []);
  async function mutate(action: () => Promise<ComparisonWorkspace>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); poll.current?.abort();
    const epoch = ++generation.current;
    try { const next = await action(); if (generation.current === epoch) commit(next); }
    catch (e) { if (generation.current === epoch) setError(`${e instanceof Error ? e.message : 'Connection lost'}. The action was not retried. Check the latest status before trying again.`); }
    finally { if (generation.current === epoch) { lock.current = false; setBusy(false); } }
  }
  useEffect(() => {
    if (!workspace || busy || error || !active(workspace.current)) return;
    const reference = workspace.current.run, epoch = generation.current;
    const controller = new AbortController(); poll.current = controller;
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const next = await api.read(reference.run_id, controller.signal);
        if (controller.signal.aborted || epoch !== generation.current) return;
        if (matchesRun(reference, next.current.run) && Date.parse(next.current.updated_at) >= Date.parse(workspace!.current.updated_at)) commit(next);
        else timer = setTimeout(read, 1200);
      } catch (e) { if (!controller.signal.aborted && epoch === generation.current) setError(e instanceof Error ? e.message : 'Could not read progress.'); }
    }
    timer = setTimeout(read, 1200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [workspace, busy, error, api, commit]);
  return { workspace, busy, error,
    submit: (request: ShoppingRequest) => mutate(async () => current.current ? api.refine(current.current.current.run, request) : envelope(await api.start(request))),
    cancel: () => mutate(async () => envelope(await api.cancel(current.current!.current.run), current.current?.previous)),
    clarify: (answer: string) => mutate(async () => envelope(await api.clarify(current.current!.current.run, current.current!.current.clarification!.clarification_id, answer), current.current?.previous)),
    recover: () => mutate(async () => api.read(current.current!.current.run.run_id)),
  };
}
