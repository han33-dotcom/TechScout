import { describe,it,expect,vi } from 'vitest';
import { render,screen,fireEvent,within } from '@testing-library/react';
import Comparison,{safeLink,costText,currentRecommendations,compareDecimal} from './Comparison';
import App from './App';
import type { ComparisonWorkspace } from './api';
import fixture from './fixtures/workspace.json';
const data=()=>structuredClone(fixture) as ComparisonWorkspace;
describe('comparison evidence boundaries',()=>{
 it('sorts exact decimals including exponent notation without rounding',()=>{expect(compareDecimal('1E+3','1000.00')).toBe(0);expect(compareDecimal('1000.00000000000001','1000')).toBe(1);expect(compareDecimal('0.01','0.1')).toBe(-1)});
 it('keeps one verified result, uncertainty and previous provisional separate',()=>{
  const w=data();render(<Comparison workspace={w}/>);
  expect(screen.getByText('1 verified model option')).toBeTruthy();
  expect(screen.getByText(/Uncertain \/ non-qualifying offers \(1\)/)).toBeTruthy();
  expect(screen.getAllByText('Verified in this search')).toHaveLength(1);
  expect(screen.getByText(/This is not a complete payable total/)).toBeTruthy();
 });
 it('drops an old verification receipt and never pads zero results',()=>{
  const w=data();w.current.recommendations[0].primary.verification.run.run_revision++;
  expect(currentRecommendations(w.current)).toHaveLength(0);render(<Comparison workspace={w}/>);
  expect(screen.getByText('No verified options yet')).toBeTruthy();
 });
 it('groups sellers, compares terms and retains selection/expansion across refreshes',()=>{
  const w=data(),rec=w.current.recommendations[0],extra=structuredClone(rec.primary);
  extra.offer.offer_id='seller-two';extra.offer.seller={...extra.offer.seller,seller_id:'two',name:'Second seller'};
  rec.additional_sellers=[extra];const {rerender}=render(<Comparison workspace={w}/>);
  const checks=screen.getAllByRole('checkbox');fireEvent.click(checks[0]);fireEvent.click(checks[1]);
  expect(screen.getByText(/Confirmed canonical configuration match/)).toBeTruthy();
  const region=screen.getByRole('region',{name:'Side-by-side comparison'});expect(within(region).getByText('Second seller')).toBeTruthy();
  fireEvent.click(screen.getAllByText('Costs, terms & evidence')[0]);
  rerender(<Comparison workspace={structuredClone(w)}/>);
  expect((screen.getAllByRole('checkbox')[0] as HTMLInputElement).checked).toBe(true);
  expect(screen.getAllByText('Costs, terms & evidence')[0].parentElement?.hasAttribute('open')).toBe(true);
 });
 it('renders failed/unchecked sources without treating discovered inventory as checked',()=>{
  const w=data();w.current.source_outcomes=[{source_id:'x',name:'Failed shop',status:'failed',reason:'Rate limited'},{source_id:'y',name:'Queued shop',status:'unchecked'}];
  render(<Comparison workspace={w}/>);expect(screen.getByText('Rate limited')).toBeTruthy();expect(screen.getByText('unchecked')).toBeTruthy();
 });
 it('includes the third selection when explaining configuration differences',()=>{
  const w=data(),rec=w.current.recommendations[0];
  const same=structuredClone(rec.primary);same.offer.offer_id='same-config';
  rec.additional_sellers=[same];
  const other=structuredClone(rec);other.additional_sellers=[];
  other.primary.offer.offer_id='other-config';other.primary.offer.variant.variant_id='other-variant';
  w.current.recommendations.push(other);
  render(<Comparison workspace={w}/>);
  screen.getAllByRole('checkbox').forEach(check=>fireEvent.click(check));
  expect(screen.getByText(/Different configurations of the same model/)).toBeTruthy();
  expect(screen.queryByText(/Confirmed canonical configuration match/)).toBeNull();
 });
 it('preserves unknown and estimated costs and rejects unsafe outbound links',()=>{
  expect(costText({status:'incomplete',missing:['duty']})).toContain('Subtotal unknown');
  expect(costText({status:'estimated',lower:{amount:'10',currency:'USD'},upper:{amount:'20',currency:'USD'},assumptions:['weight estimate']})).toContain('estimated');
  expect(safeLink('javascript:alert(1)')).toBeUndefined();expect(safeLink('https://name:secret@shop.example')).toBeUndefined();
 });
 it('sends conversational refinement without replacing the original intent or hard maximum',async()=>{
  const w=data();w.current.request.budget={maximum:{amount:'1200',currency:'USD'}};
  const transport={start:vi.fn().mockResolvedValue(w.current),read:vi.fn().mockResolvedValue(w),cancel:vi.fn(),clarify:vi.fn(),refine:vi.fn().mockResolvedValue(w)};
  render(<App transport={transport}/>);fireEvent.click(screen.getByText(/Study & everyday/));fireEvent.click(screen.getByRole('button',{name:/Find my options/}));await screen.findByText('1 verified model option');
  fireEvent.change(screen.getByLabelText('What would you like to change?'),{target:{value:'Prefer a longer warranty'}});fireEvent.click(screen.getByRole('button',{name:'Refine comparison'}));
  expect(transport.refine).toHaveBeenCalledWith(w.current.run,expect.objectContaining({revision:w.current.request.revision+1,intent:w.current.request.intent,budget:{target:null,maximum:{amount:'1200',currency:'USD'}},refinement_note:'Prefer a longer warranty'}));
 });
});
