import { describe,it,expect,vi } from 'vitest';
import { render,screen,fireEvent,within } from '@testing-library/react';
import Comparison,{safeLink,costText,currentRecommendations,compareDecimal} from './Comparison';
import App from './App';
import type { ComparisonWorkspace } from './api';
import fixture from './fixtures/workspace.json';
const data=()=>structuredClone(fixture) as ComparisonWorkspace;
describe('comparison evidence boundaries',()=>{
 it('changes reading advice with the stated use while retaining the same listing',()=>{
  const w=data(), item=w.current.uncertain[0];
  w.current.recommendations=[];
  w.current.request.intent={kind:'needs',description:'A tablet for reading textbooks',required_software:[]};
  const unknown={state:'unknown' as const,evidence_ids:[],reason:'Unconfirmed'};
  item.offer.variant.specs={category:'tablet',ram_gb:unknown,storage_gb:{state:'known',value:128,evidence_ids:['storage']},connectivity:unknown,stylus_support:unknown};
  const {rerender}=render(<Comparison workspace={w}/>);
  expect(screen.getByText(/For reading, check that the screen shows a full page/).textContent).toContain('128 GB');
  w.current.request.intent={kind:'needs',description:'A tablet for video calls',required_software:[]};
  rerender(<Comparison workspace={w}/>);
  expect(screen.queryByText(/For reading, check/)).toBeNull();
 });
 it('labels an unknown pickup time at the selected branch',()=>{
  const workspace=data(),offer=workspace.current.recommendations[0].primary.offer;
  offer.route={kind:'pickup',route_id:'pickup-east-hamra',branch_id:'hamra',destination:{country:'LB',city:'Beirut'}};
  offer.arrival={state:'unknown',reason:'No pickup window published',evidence_ids:[]};
  render(<Comparison workspace={workspace}/>);
  expect(screen.getAllByText('Pickup timing: Unknown').length).toBeGreaterThan(0);
 });
 it('shows an unconfirmed dispatch origin without classifying it as international',()=>{
  const workspace=data(),offer=workspace.current.uncertain[0].offer;
  offer.route={kind:'direct',route_id:'unknown-origin',origin_country:'ZZ',destination:{country:'LB',city:'Beirut'},
               item_eligible:{state:'unknown',reason:'Destination unconfirmed',evidence_ids:[]}};
  render(<Comparison workspace={workspace}/>);
  expect(screen.getByText(/Delivery requested to Beirut · Dispatch origin unknown/)).toBeTruthy();
  expect(screen.queryByText(/International delivery · ZZ/)).toBeNull();
 });
 it('sorts exact decimals including exponent notation without rounding',()=>{expect(compareDecimal('1E+3','1000.00')).toBe(0);expect(compareDecimal('1000.00000000000001','1000')).toBe(1);expect(compareDecimal('0.01','0.1')).toBe(-1)});
 it('keeps one verified result, uncertainty and previous provisional separate',()=>{
  const w=data();render(<Comparison workspace={w}/>);
  expect(screen.getByText('1 verified model option')).toBeTruthy();
  expect(screen.getByText(/Uncertain \/ non-qualifying offers \(1\)/)).toBeTruthy();
  expect(screen.getAllByText('Verified in this search')).toHaveLength(1);
  expect(screen.getByText(/This is not a complete payable total/)).toBeTruthy();
 });
 it('shows current listing facts and the exact link beside missing charges',()=>{
  const workspace=data(), item=workspace.current.uncertain[0];
  item.offer=structuredClone(workspace.current.recommendations[0].primary.offer);
  item.offer.listing_url='https://example.com/product/scout-book/?variant=black';
  item.reasons=['The delivery fee for Beirut is unknown.'];
  for (const receipt of item.offer.evidence) receipt.run=workspace.current.run;
  workspace.current.recommendations=[];
  const {rerender}=render(<Comparison workspace={workspace}/>);
  const price=screen.getByText(/Current listed price/);
  const card=within(price.closest('article')!);
  expect(card.getByRole('link', {name:/View at/}).getAttribute('href')).toBe(item.offer.listing_url);
  expect(card.getByText(/In stock · Online listing/)).toBeTruthy();
  expect(card.getByText(item.reasons[0])).toBeTruthy();
  rerender(<Comparison workspace={workspace} historical/>);
  expect(screen.getByText(/Previously observed listed price/)).toBeTruthy();
  expect(screen.queryByText(/In stock · Online listing/)).toBeNull();
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
  expect(screen.getByText(/tie for the lowest verified total/)).toBeTruthy();
  const checks=screen.getAllByRole('checkbox');fireEvent.click(checks[0]);fireEvent.click(checks[1]);
  expect(screen.getByText(/Confirmed canonical configuration match/)).toBeTruthy();
  const region=screen.getByRole('region',{name:'Side-by-side comparison'});expect(within(region).getByText('Second seller')).toBeTruthy();
  fireEvent.click(screen.getAllByText('Costs, terms & evidence')[0]);
  rerender(<Comparison workspace={structuredClone(w)}/>);
  expect((screen.getAllByRole('checkbox')[0] as HTMLInputElement).checked).toBe(true);
  expect(screen.getAllByText('Costs, terms & evidence')[0].parentElement?.hasAttribute('open')).toBe(true);
 });
 it('identifies the lowest comparable total even if the primary seller costs more',()=>{
  const w=data(),rec=w.current.recommendations[0],other=structuredClone(rec.primary);
  other.offer.offer_id='second';other.offer.seller.seller_id='second';other.offer.seller.name='Local shop';
  other.cost.total.amount='1.02';rec.additional_sellers=[other];
  const {rerender}=render(<Comparison workspace={w}/>);
  expect(screen.getByText(/Local shop has the lowest verified total/)).toBeTruthy();
  other.cost.total.currency='LBP';rerender(<Comparison workspace={structuredClone(w)}/>);
  expect(screen.queryByText(/lowest verified total/)).toBeNull();
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
