import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import History, { type HistoryTransport } from './History';
import App from './App';
import type { Transport } from './useWorkspace';
import type { ComparisonWorkspace, ShoppingRequest } from './api';
import fixture from './fixtures/workspace.json';

const workspace = () => structuredClone(fixture) as unknown as ComparisonWorkspace;
function history(): HistoryTransport {
  const w=workspace();
  return {
    history: vi.fn().mockResolvedValue({expires_at:'2026-11-18T10:00:00Z',items:[{run_id:w.current.run.run_id,revision:w.current.request.revision,category:'laptop',status:'complete',updated_at:w.current.updated_at}]}),
    reopen: vi.fn().mockResolvedValue({historical:true,workspace:w}),
    deleteHistory: vi.fn().mockResolvedValue({deleted:true}),
    exportHistory: vi.fn().mockResolvedValue('HISTORICAL comparison'),
  };
}
async function open() {
  fireEvent.click(screen.getByRole('button',{name:'Open history'}));
  fireEvent.click(await screen.findByRole('button',{name:'View saved search'}));
  await screen.findByRole('heading',{name:/Historical snapshot/});
}
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
describe('private session history',()=>{
  it('loads on demand and never presents saved verification as current',async()=>{
    const api=history(),recheck=vi.fn();render(<History api={api} onRecheck={recheck} onDeleted={vi.fn()}/>);
    expect(api.history).not.toHaveBeenCalled();await open();
    expect(screen.getAllByText('Verified for earlier request').length).toBeGreaterThan(0);
    expect(screen.queryByText('Verified in this search')).toBeNull();expect(recheck).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'Recheck these requirements'}));
    expect(recheck).toHaveBeenCalledWith(workspace().current.request);
  });
  it('rechecks domestic offers with a new identity and the saved product requirements',async()=>{
    const api=history(),w=workspace();
    const transport:Transport={start:vi.fn().mockImplementation(async(request:ShoppingRequest)=>({...w.current,request,run:{...w.current.run,request_id:request.request_id,request_revision:1,run_id:'fresh-run'},recommendations:[],uncertain:[]})),read:vi.fn(),cancel:vi.fn(),clarify:vi.fn(),refine:vi.fn()};
    render(<App transport={transport} historyTransport={api}/>);await open();
    fireEvent.click(screen.getByRole('button',{name:'Recheck these requirements'}));
    await waitFor(()=>expect(transport.start).toHaveBeenCalledTimes(1));
    const request=vi.mocked(transport.start).mock.calls[0][0];
    expect(request.request_id).not.toBe(w.current.request.request_id);
    expect(w.current.request.market_scope?.include_international).toBe(true);
    expect(request).toEqual({...w.current.request,market_scope:{include_international:false,international_markets:[]},request_id:request.request_id,revision:1});
    expect(transport.refine).not.toHaveBeenCalled();
  });
  it('requires explicit deletion confirmation and clears the matching workspace',async()=>{
    const api=history(),deleted=vi.fn();render(<History api={api} onRecheck={vi.fn()} onDeleted={deleted}/>);await open();
    fireEvent.click(screen.getByRole('button',{name:'Delete saved search'}));
    expect(api.deleteHistory).not.toHaveBeenCalled();
    vi.mocked(api.history).mockResolvedValue({items:[],expires_at:'2026-11-18T10:00:00Z'});
    fireEvent.click(screen.getByRole('button',{name:'Delete all revisions'}));
    await screen.findByText('No saved searches in this session.');
    expect(api.deleteHistory).toHaveBeenCalledTimes(1);
    expect(deleted).toHaveBeenCalledWith(workspace().current.request.request_id);
    expect(screen.queryByRole('region',{name:'Saved search'})).toBeNull();
  });
  it('does not retry or hide a saved search after an ambiguous deletion failure',async()=>{
    const api=history();vi.mocked(api.deleteHistory).mockRejectedValue(new Error('Connection lost'));
    render(<History api={api} onRecheck={vi.fn()} onDeleted={vi.fn()}/>);await open();
    fireEvent.click(screen.getByRole('button',{name:'Delete saved search'}));fireEvent.click(screen.getByRole('button',{name:'Delete all revisions'}));
    expect((await screen.findByRole('alert')).textContent).toContain('not retried');
    expect(api.deleteHistory).toHaveBeenCalledTimes(1);expect(screen.getByRole('region',{name:'Saved search'})).toBeTruthy();
  });
  it('downloads an explicit export with the selected evidence setting',async()=>{
    const api=history(),create=vi.fn().mockReturnValue('blob:report');
    vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:create,revokeObjectURL:vi.fn()}));
    const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
    render(<History api={api} onRecheck={vi.fn()} onDeleted={vi.fn()}/>);await open();
    expect(api.exportHistory).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Include evidence links in export'));
    fireEvent.click(screen.getByRole('button',{name:'Export comparison'}));
    await waitFor(()=>expect(click).toHaveBeenCalledTimes(1));
    expect(api.exportHistory).toHaveBeenCalledWith(workspace().current.run.run_id,false);
    expect(create.mock.calls[0][0].type).toBe('text/plain;charset=utf-8');
  });
});
