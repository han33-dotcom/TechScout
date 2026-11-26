import { compareDecimal } from './money';
import type { RunSnapshot, ShoppingRequest } from './api';
export const domesticScope = { include_international: false as const, international_markets: [] };
type EditableRequest = ShoppingRequest | RunSnapshot['request'];
export type Category = ShoppingRequest['requirements']['category'];
export const categories: Record<Category, string> = { phone: 'Phones', laptop: 'Laptops', tablet: 'Tablets', monitor: 'Monitors', audio: 'Headphones & earbuds' };
export interface Draft { implementation: 'control' | 'single_agent'; text: string; kind: 'needs' | 'exact_model'; category: Category; target: string; maximum: string; currency: string; city: string; branch: string; condition: 'new' | 'used' | 'refurbished'; mode: 'any' | 'delivery' | 'pickup'; payment: '' | 'cash' | 'card'; spec: string }
export const initialDraft: Draft = { implementation: 'control', text: '', kind: 'needs', category: 'laptop', target: '', maximum: '', currency: 'USD', city: '', branch: '', condition: 'new', mode: 'any', payment: '', spec: '' };
export const specFields = { phone: 'storage_gb', laptop: 'min_ram_gb', tablet: 'min_storage_gb', monitor: 'min_refresh_hz', audio: '' } as const;
export const specLabels = { phone: 'Storage (GB)', laptop: 'Minimum RAM (GB)', tablet: 'Minimum storage (GB)', monitor: 'Minimum refresh rate (Hz)', audio: '' };
export const examples: { label: string; draft: Draft }[] = [
  { label: 'Study & everyday work', draft: { ...initialDraft, text: 'A lightweight laptop for university, around $1,000', target: '1000', spec: '16' } },
  { label: 'مثال بالعربية', draft: { ...initialDraft, text: 'بدي لابتوب للدراسة بحدود ١٠٠٠ دولار', target: '1000' } },
  { label: 'Arabizi example', draft: { ...initialDraft, text: 'baddi laptop lal jem3a bi 7doud 1000 dollar', target: '1000' } },
];
function money(value: string, currency: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value) || !/[1-9]/.test(value) || value.length > 14) throw new Error('Enter a positive budget with at most two decimal places.');
  if (!/^[A-Z]{3}$/.test(currency)) throw new Error('Choose a valid currency.');
  return { amount: value, currency };
}
export function toRequest(draft: Draft, base?: EditableRequest): ShoppingRequest {
  const text = draft.text.trim();
  if (!text) throw new Error('Describe what you need, enter a model, or paste a retailer link.');
  let intent: ShoppingRequest['intent'];
  let colors = base?.allowed_colors ?? [];
  if (/^https?:\/\//i.test(text)) {
    const url = new URL(text);
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Use a public HTTPS product link.');
    intent = { kind: 'product_link', url: url.href };
  } else if (draft.kind === 'exact_model') {
    const selection = /^(.+?)\s+in\s+(black|white|red|blue|green|yellow|purple|pink|silver|gold|gray|grey)$/i.exec(text);
    const model = selection ? selection[1].trim() : text;
    if (selection && (!base || fromRequest(base).text !== text)) colors = [selection[2].toLowerCase()];
    if (base?.intent.kind === 'exact_model' && base.intent.wording === text) intent = base.intent;
    else intent = { kind: 'exact_model', model, manufacturer: base?.intent.kind === 'exact_model' && base.intent.model === model ? base.intent.manufacturer : undefined };
  }
  else intent = { kind: 'needs', description: text, required_software: base?.intent.kind === 'needs' ? base.intent.required_software : [] };
  const target = draft.target ? money(draft.target,draft.currency) : null, maximum = draft.maximum ? money(draft.maximum,draft.currency) : null;
  if (target && maximum && compareDecimal(target.amount,maximum.amount) > 0) throw new Error('Your target must fit your hard maximum.');
  const requirements = base?.requirements.category === draft.category ? { ...base.requirements } : draft.category === 'phone' ? { category: 'phone' as const, regions: [] } : { category: draft.category };
  const key = specFields[draft.category];
  if (key) {
    if (draft.spec && (!/^\d+$/.test(draft.spec) || Number(draft.spec) < 1 || Number(draft.spec) > 100000)) throw new Error('Enter a positive whole number for the specification.');
    Object.assign(requirements, { [key]: draft.spec ? Number(draft.spec) : null });
  }
  return { ...base, implementation: draft.implementation, schema_version: 2, request_id: base?.request_id ?? crypto.randomUUID(), revision: base ? base.revision + 1 : 1,
    unresolved_constraints: base?.unresolved_constraints ?? [],
    buyer_text: intent.kind !== 'product_link' && (!base || text !== fromRequest(base).text || intent.kind !== base.intent.kind || draft.category !== base.requirements.category) ? text : undefined,
    intent, requirements: requirements as ShoppingRequest['requirements'], budget: target || maximum ? { target, maximum } : null,
    allowed_colors: colors, preferences: base?.preferences ?? [], allowed_conditions: base?.allowed_conditions[0] === draft.condition ? base.allowed_conditions : [draft.condition], payment: draft.payment || null,
    market_scope: domesticScope,
    fulfillment: { ...base?.fulfillment, mode: draft.mode, branch_id: draft.mode === 'pickup' ? draft.branch.trim() || null : null,
      destination: { ...base?.fulfillment?.destination, country: 'LB', city: draft.city.trim() || null } } };
}

export function fromRequest(r: EditableRequest): Draft {
  return { implementation: r.implementation ?? 'control', text: r.intent.kind === 'needs' ? r.intent.description : r.intent.kind === 'product_link' ? r.intent.url : r.intent.wording ?? r.intent.model,
    kind: r.intent.kind === 'exact_model' ? 'exact_model' : 'needs', category: r.requirements.category,
    target: String(r.budget?.target?.amount ?? ''), maximum: String(r.budget?.maximum?.amount ?? ''),
    currency: r.budget?.target?.currency ?? r.budget?.maximum?.currency ?? 'USD', city: r.fulfillment?.destination?.city ?? '', mode: r.fulfillment?.mode ?? 'any',
    branch: r.fulfillment?.branch_id ?? '',
    condition: r.allowed_conditions[0] ?? 'new', payment: r.payment ?? '',
    spec: String((r.requirements as unknown as Record<string, unknown>)[specFields[r.requirements.category]] ?? '') };
}
