import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import App, { Progress } from './App';
import { toRequest, fromRequest, domesticScope, initialDraft, examples } from './request';
import { useWorkspace, type Transport } from './useWorkspace';
import type { RunSnapshot, ComparisonWorkspace } from './api';
import fixture from './fixtures/workspace.json';

const request = () => toRequest(examples[0].draft);
function snap(status: RunSnapshot['status'] = 'running'): RunSnapshot {
 const r=request() as RunSnapshot["request"]; return {schema_version:2,request:r,run:{run_id:'run-1',run_revision:1,request_id:r.request_id,request_revision:1},status,phase:'research',started_at:'2026-11-17T10:00:00Z',updated_at:'2026-11-17T10:00:01Z',provisional:[],recommendations:[],uncertain:[],source_outcomes:[]};
}
const workspace=(s:RunSnapshot):ComparisonWorkspace=>({schema_version:2,current:s});
function api(s=snap()): Transport {return {start:vi.fn().mockResolvedValue(s),read:vi.fn().mockResolvedValue(workspace(s)),cancel:vi.fn().mockResolvedValue({...s,status:'cancelled'}),clarify:vi.fn().mockResolvedValue({...s,run:{...s.run,run_revision:2}}),refine:vi.fn().mockResolvedValue(workspace({...s,run:{...s.run,run_id:'run-2',request_revision:2},request:{...s.request,revision:2}}))};}
afterEach(()=>vi.useRealTimers());
describe('request boundaries',()=>{
 it('keeps an explicit color separate from the exact model',()=>{
  const r=toRequest({...initialDraft,text:'Studio 123 in white',kind:'exact_model',category:'audio'});
  expect(r.intent).toMatchObject({kind:'exact_model',model:'Studio 123'});
  expect(r.allowed_colors).toEqual(['white']);
 });
 it.each(examples)('preserves approximate budget and original wording: $label',({draft})=>{
  const r=toRequest(draft); expect(r.budget?.target?.amount).toBe('1000');expect(r.budget?.maximum).toBeNull();expect(r.market_scope?.include_international).toBe(false);expect(r.allowed_conditions).toEqual(['new']);expect(r.intent.kind).toBe('needs');
 });
 it('distinguishes explicit maxima, exact models, links and all five categories',()=>{
  for(const category of ['phone','laptop','tablet','monitor','audio'] as const) expect(toRequest({...initialDraft,text:'Find a device',category}).requirements.category).toBe(category);
  expect(toRequest({...initialDraft,text:'iPhone 17',kind:'exact_model',maximum:'900'}).budget?.maximum?.amount).toBe('900');
  expect(toRequest({...initialDraft,text:'https://retailer.example/device'}).intent.kind).toBe('product_link');
  expect(()=>toRequest({...initialDraft,text:'http://retailer.example/device'})).toThrow();
 });
 it('refines a saved international comparison as a domestic request',()=>{
  const saved = (fixture.current as unknown as RunSnapshot).request;
  expect(saved.market_scope?.include_international).toBe(true);
  const revised = toRequest(fromRequest(saved),saved);
  expect(revised.market_scope).toEqual({ include_international:false, international_markets:[] });
  expect(revised.request_id).toBe(saved.request_id);
  expect(revised.revision).toBe(saved.revision + 1);
 });
 it('preserves unedited requirements through refinement',()=>{
  const base=request();base.requirements={category:'laptop',min_ram_gb:16,cpu:'Required CPU'};base.allowed_colors=['Black'];
  const r=toRequest({...examples[0].draft,maximum:'1100'},base);
  expect(r.revision).toBe(2);expect(r.request_id).toBe(base.request_id);expect(r.allowed_colors).toEqual(['Black']);expect(r.requirements).toMatchObject({cpu:'Required CPU'});
 });
 it('preserves foreign-currency budgets and manufacturer through refinement',()=>{
  const base=toRequest({...initialDraft,text:'Scout Book',kind:'exact_model',maximum:'900',currency:'EUR'});
  base.intent={kind:'exact_model',model:'Scout Book',manufacturer:'Example'};
  const refined=toRequest(fromRequest(base),base);
  expect(refined.budget?.maximum).toEqual({amount:'900',currency:'EUR'});
  expect(refined.intent).toEqual(base.intent);
 });
});
describe('buyer interactions',()=>{
 it('submits a selected branch and removes the constraint when the buyer switches to delivery',async()=>{
  const transport=api();
  vi.mocked(transport.start).mockImplementation(async request=>{
   const queued=snap('queued');
   queued.request.fulfillment=request.fulfillment;
   return queued;
  });
  render(<App transport={transport}/>);
  fireEvent.change(screen.getByLabelText('What are you looking for?'),{target:{value:'Demo phone'}});
  fireEvent.change(screen.getByLabelText('Fulfillment'),{target:{value:'pickup'}});
  fireEvent.change(screen.getByLabelText(/pickup branch code/i),{target:{value:'  hamra  '}});
  fireEvent.click(screen.getByRole('button',{name:/Find my options/}));
  await screen.findByText('Waiting to start');
  const saved=vi.mocked(transport.start).mock.calls[0][0];
  expect(saved.fulfillment).toMatchObject({mode:'pickup',branch_id:'hamra'});
  await waitFor(()=>expect((screen.getByLabelText(/pickup branch code/i) as HTMLInputElement).value).toBe('hamra'));
  expect(toRequest(fromRequest(saved),saved).fulfillment?.branch_id).toBe('hamra');
  fireEvent.change(screen.getByLabelText('Fulfillment'),{target:{value:'delivery'}});
  expect(screen.queryByLabelText(/pickup branch code/i)).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/Update search/}));
  await waitFor(()=>expect(transport.refine).toHaveBeenCalled());
  expect(vi.mocked(transport.refine).mock.calls[0][1].fulfillment).toMatchObject({mode:'delivery',branch_id:null});
 });
 it('submits without a mandatory confirmation and renders a truthful queue',async()=>{
  const transport=api(snap('queued'));render(<App transport={transport}/>);
  expect(screen.queryByLabelText(/include international/i)).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/Study & everyday/}));
  fireEvent.click(screen.getByRole('button',{name:/Find my options/}));
  await screen.findByText('Waiting to start');expect(transport.start).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('progressbar')).toBeNull();expect(screen.getByLabelText('Target budget · USD').getAttribute('value')).toBe('1000');
 });
 it('shows only a material clarification and preserves retailer text as text',()=>{
  const s=snap('clarification');s.clarification={clarification_id:'q',question:'Which software is required?',material_reason:'It changes laptop compatibility.'};
  const clarify=vi.fn();render(<Progress snapshot={s} busy={false} cancel={vi.fn()} clarify={clarify}/>);
  fireEvent.change(screen.getByLabelText('Which software is required?'),{target:{value:'CAD'}});fireEvent.click(screen.getByText('Continue search'));expect(clarify).toHaveBeenCalledWith('CAD');
 });
 it('shows provisional observation time and never labels it verified',()=>{
  const s={...snap(), provisional: structuredClone(fixture.previous.provisional)} as unknown as RunSnapshot;
  s.provisional[0].offer.seller.name='<img src=x onerror=alert(1)>';
  const {container}=render(<Progress snapshot={s} busy={false} cancel={vi.fn()} clarify={vi.fn()}/>);
  expect(screen.getByText('Checking · not verified')).toBeTruthy();expect(screen.getByText(/Originally observed/)).toBeTruthy();expect(container.querySelector('img')).toBeNull();
 });
 it('shows the writer explanation as plain text with its source',()=>{
  const s={...snap('abstained'),finished_at:'2026-11-17T10:00:02Z',stop_reason:'No offer could be verified in this run',explanation:{text:['No offer was verified in this run.','Not verified, shown separately:','- <img src=x onerror=alert(1)>'].join('\n'),evidence_ids:[],source:'fallback'}} as RunSnapshot;
  const {container}=render(<Progress snapshot={s} busy={false} cancel={vi.fn()} clarify={vi.fn()}/>);
  expect(screen.getByLabelText('Explanation of these results').textContent).toContain('No offer was verified in this run.');
  expect(screen.getByText('Standard summary')).toBeTruthy();expect(container.querySelector('img')).toBeNull();
 });
 it('does not retry an ambiguous create failure',async()=>{
  const transport=api();vi.mocked(transport.start).mockRejectedValue(new Error('Connection lost'));render(<App transport={transport}/>);
  fireEvent.change(screen.getByLabelText('What are you looking for?'),{target:{value:'laptop'}});fireEvent.click(screen.getByRole('button',{name:/Find my options/}));
  await screen.findByRole('alert');expect(transport.start).toHaveBeenCalledTimes(1);
 });
});
describe('revision races',()=>{
 it.each(['cancel','refine'] as const)('discards an in-flight poll after %s',async(kind)=>{
  vi.useFakeTimers();const s=snap(),transport=api(s);let resolve!:(w:ComparisonWorkspace)=>void;
  vi.mocked(transport.read).mockImplementation(()=>new Promise(r=>{resolve=r}));
  const {result}=renderHook(()=>useWorkspace(transport));
  await act(async()=>{await result.current.submit({...s.request,market_scope:domesticScope})});
  await act(async()=>{await vi.advanceTimersByTimeAsync(1200)});
  await act(async()=>{if(kind==='cancel') await result.current.cancel();else await result.current.submit({...s.request,market_scope:domesticScope,revision:2})});
  await act(async()=>{resolve(workspace({...s,status:'complete'}))});
  expect(kind==='cancel'?result.current.workspace?.current.status:result.current.workspace?.current.request.revision).toBe(kind==='cancel'?'cancelled':2);
 });
});
