import { useRef, useState, useEffect } from 'react';
import type { BuyerApi, HistoryList, HistoricalWorkspace, ShoppingRequest } from './api';
import Comparison from './Comparison';
import { categories } from './request';

export type HistoryTransport = Pick<BuyerApi, 'history' | 'reopen' | 'deleteHistory' | 'exportHistory'>;
function download(text: string) {
  const url = URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'techscout-comparison.txt';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function History({api, onRecheck, onDeleted}: {
  api: HistoryTransport; onRecheck: (request: ShoppingRequest) => void; onDeleted: (requestId: string) => void;
}) {
  const [open,setOpen]=useState(false), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const [list,setList]=useState<HistoryList>(), [saved,setSaved]=useState<HistoricalWorkspace>();
  const [confirmDelete,setConfirmDelete]=useState(false), [includeEvidence,setIncludeEvidence]=useState(true);
  const generation=useRef(0), locked=useRef(false);
  useEffect(()=>()=>{generation.current++;},[]);
  async function perform(action:()=>Promise<void>) {
    if(locked.current)return;
    locked.current=true; setBusy(true); setError('');
    try { await action(); }
    catch(e) { setError(e instanceof Error ? e.message : 'History request failed'); }
    finally { locked.current=false; setBusy(false); }
  }
  async function refresh() {
    const epoch=++generation.current;
    await perform(async()=>{const result=await api.history(); if(epoch===generation.current)setList(result);});
  }
  async function reopen(runId:string) {
    const epoch=++generation.current;
    setConfirmDelete(false);
    await perform(async()=>{const result=await api.reopen(runId); if(epoch===generation.current)setSaved(result);});
  }
  return <section className="history panel" aria-label="Recent searches">
    <div className="section-heading"><div><span className="eyebrow">YOUR SESSION</span><h2>Recent searches</h2></div>
      <button disabled={busy} onClick={()=>{setOpen(!open);if(!open)void refresh();else {generation.current++;setSaved(undefined);}}}>{open?'Hide history':'Open history'}</button></div>
    <p className="muted">Private to this browser session. Saved results need a fresh check before you rely on them.</p>
    {open && <><div className="history-toolbar"><button disabled={busy} onClick={()=>void refresh()}>Refresh history</button>{list&&<span className="muted">Expires {new Date(list.expires_at).toLocaleString()}</span>}</div>
      {busy&&<p role="status">Loading history…</p>}{error&&<p role="alert">{error}. This action was not retried. Cancel any active search before deleting it.</p>}
      {list&&!list.items.length&&<p>No saved searches in this session.</p>}
      <ul className="history-list">{list?.items.map(item=><li key={item.run_id}><div><strong>{categories[item.category]} · revision {item.revision}</strong><small>{item.status} · {new Date(item.updated_at).toLocaleString()}</small></div><button disabled={busy} onClick={()=>void reopen(item.run_id)}>View saved search</button></li>)}</ul>
      {saved&&<section className="saved-search" aria-label="Saved search"><h3>Historical snapshot · revision {saved.workspace.current.request.revision}</h3><p>Prices and availability belong to the saved search. Recheck to start a new search with these requirements.</p>
        <div className="history-toolbar"><button className="primary" disabled={busy} onClick={()=>onRecheck(saved.workspace.current.request)}>Recheck these requirements</button><label><input type="checkbox" checked={includeEvidence} onChange={e=>setIncludeEvidence(e.target.checked)}/> Include evidence links in export</label><button disabled={busy} onClick={()=>void perform(async()=>download(await api.exportHistory(saved.workspace.current.run.run_id,includeEvidence)))}>Export comparison</button></div>
        <p className="muted">The text export omits your request, preferences and session identifiers. Evidence links omit URL query strings and fragments.</p>
        <Comparison workspace={saved.workspace} historical/>
        <button disabled={busy} onClick={()=>setConfirmDelete(true)}>Delete saved search</button>
        {confirmDelete&&<div className="delete-confirm"><p>Delete this search and all its revisions from this session? This cannot be undone.</p><button disabled={busy} onClick={()=>void perform(async()=>{
          const requestId=saved.workspace.current.request.request_id;
          await api.deleteHistory(saved.workspace.current.run.run_id);
          onDeleted(requestId);setSaved(undefined);setConfirmDelete(false);
          setList(await api.history());
        })}>Delete all revisions</button><button disabled={busy} onClick={()=>setConfirmDelete(false)}>Keep search</button></div>}
      </section>}
    </>}
  </section>;
}
