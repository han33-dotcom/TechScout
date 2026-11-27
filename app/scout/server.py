"""Local demo page for the scout agent: python -m app.scout.server  (http://127.0.0.1:8010)

One page: the buyer types a request, watches the agent's turns live (what it searched,
which products it opened) and gets checked offer cards with prices, delivered totals when
the shop publishes its fee, and every open question. It binds to 127.0.0.1, runs one search
at a time, accepts only same-origin JSON, and the page renders all retailer text as plain
text (never as HTML).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.scout.__main__ import RUNS, data_allowance, ledger_budget, make_provider
from app.scout.agent import RunConfig, ScoutAgent

HOST, PORT = '127.0.0.1', 8010


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=3, max_length=1500)
    mode: Literal['delivery', 'pickup'] = 'delivery'
    destination: str | None = Field(default=None, max_length=60)


def create_app(*, provider=None, budget_factory=ledger_budget, agent_factory=None) -> FastAPI:
    app = FastAPI(title='TechScout scout', docs_url=None, redoc_url=None, openapi_url=None)
    runs: dict[str, dict] = {}
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=1)
    state = {'provider': provider}

    def make_agent():
        if agent_factory is not None:
            return agent_factory()
        if state['provider'] is None:
            state['provider'] = make_provider()
        agent = ScoutAgent(state['provider'], budget_factory=budget_factory, config=RunConfig())
        agent.data_bytes = data_allowance(state['provider'])
        return agent

    def work(run_id: str, request: dict):
        record = runs[run_id]
        try:
            agent = make_agent()
            agent.on_event = lambda e: record['events'].append(e)
            record['status'] = 'running'
            result = agent.run(dict(request, run_id=run_id))
            record['result'] = result.public()
            RUNS.mkdir(parents=True, exist_ok=True)
            (RUNS / f'{run_id}.json').write_text(json.dumps(record['result'], ensure_ascii=False, indent=1,
                                                            default=str), encoding='utf-8')
            record['status'] = 'done'
        except Exception as exc:  # noqa: BLE001 - the buyer sees a fixed message, the log the type
            record['status'] = 'error'
            record['error'] = f'The search could not run ({type(exc).__name__}). Check the worker setup.'

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        if request.method == 'POST':
            origin = request.headers.get('origin')
            if origin and origin not in (f'http://{HOST}:{PORT}', f'http://localhost:{PORT}',
                                         str(request.base_url).rstrip('/')):
                return JSONResponse({'detail': 'cross-origin request refused'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'unsafe-inline'; "
                                                       "style-src 'unsafe-inline'; img-src 'self' data:")
        return response

    @app.get('/', response_class=HTMLResponse)
    def page():
        return PAGE

    @app.post('/api/runs')
    def start(body: RunRequest):
        with lock:
            if any(r['status'] in ('queued', 'running') for r in runs.values()):
                raise HTTPException(429, 'A search is already running; wait for it to finish.')
            run_id = f'scout-{time.strftime("%Y%m%dT%H%M%S", time.gmtime())}-{uuid.uuid4().hex[:6]}'
            runs[run_id] = dict(status='queued', events=[], result=None, error=None)
        pool.submit(work, run_id, body.model_dump())
        return {'run_id': run_id}

    @app.get('/api/runs/{run_id}')
    def poll(run_id: str):
        record = runs.get(run_id)
        if record is None:
            raise HTTPException(404, 'unknown run')
        return dict(status=record['status'], events=record['events'][-60:], result=record['result'],
                    error=record['error'])

    return app


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TechScout</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--muted:#5b6472;--line:#e3e6eb;--ok:#0f7b3f;--warn:#9a5b00;--bad:#b42318;--accent:#2457d6}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:980px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 2px}.sub{color:var(--muted);margin:0 0 16px}
form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;display:grid;gap:10px}
textarea{width:100%;min-height:70px;font:inherit;padding:10px;border:1px solid var(--line);border-radius:8px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}select,input{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px}
button{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:8px 16px;cursor:pointer}button:disabled{opacity:.5}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:14px}
.ev{font-size:13px;color:var(--muted);margin:4px 0}.ev b{color:var(--ink)}.msg{white-space:pre-wrap}
h2{font-size:17px;margin:18px 0 8px}.cards{display:grid;gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.shop{font-weight:600}.title{color:var(--muted);font-size:14px}
.price{font-size:20px;font-weight:700;text-align:right}.total{font-size:13px;color:var(--muted);text-align:right}
.badges{margin:8px 0 4px;display:flex;gap:6px;flex-wrap:wrap}.b{font-size:12px;border-radius:99px;padding:2px 8px;border:1px solid var(--line)}
.b.ok{color:var(--ok);border-color:#bfe3cf}.b.warn{color:var(--warn);border-color:#f0d9b5}.b.bad{color:var(--bad);border-color:#f3c5c0}
ul.gaps{margin:6px 0 0 18px;padding:0;font-size:13px;color:var(--warn)}.meta{font-size:12px;color:var(--muted);margin-top:6px}
a{color:var(--accent)}.foot{font-size:12px;color:var(--muted);margin-top:14px}
</style></head><body><main>
<h1>TechScout</h1><p class="sub">Finds current offers at Lebanese shops, checks them, and tells you what is confirmed and what is not. You buy on the shop's own site.</p>
<form id="f"><textarea id="text" placeholder="e.g. New black Sony WH-CH520 headphones, cheapest delivered to Batroun" required maxlength="1500"></textarea>
<div class="row"><label>Get it by <select id="mode"><option value="delivery">Delivery</option><option value="pickup">Pickup</option></select></label>
<label>City <input id="dest" maxlength="60" placeholder="Beirut"></label><button id="go">Search</button><span id="state" class="ev"></span></div></form>
<div id="progress" class="panel" hidden></div><div id="answer"></div>
<script>
const $=id=>document.getElementById(id);
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined&&text!==null)e.textContent=String(text);return e}
function link(url,label){if(!/^https:\/\//.test(url||''))return el('span','',label);const a=el('a','',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a}
function badge(text,kind){return el('span','b '+kind,text)}
let timer=null,seen=0;
$('f').addEventListener('submit',async ev=>{ev.preventDefault();$('go').disabled=true;$('answer').textContent='';$('progress').hidden=false;$('progress').textContent='';seen=0;$('state').textContent='Starting...';
 const r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('text').value,mode:$('mode').value,destination:$('dest').value||null})});
 const j=await r.json();if(!r.ok){$('state').textContent=j.detail||'Could not start';$('go').disabled=false;return}
 timer=setInterval(()=>poll(j.run_id),1500)});
async function poll(id){const r=await fetch('/api/runs/'+id);const j=await r.json();
 for(const e of j.events.slice(seen)){const d=el('div','ev');
  if(e.kind==='turn'){d.appendChild(el('b','','Step '+e.turn+' ('+e.at_s+'s): '));d.appendChild(document.createTextNode(e.notes||''));}
  else if(e.kind==='results'){d.textContent=e.results.map(x=>x.error?(x.tool+' '+(x.target||'')+': '+x.error):x.tool==='search'?('search "'+x.target+'": '+x.hits+' leads from '+x.shops+' shops'):x.tool==='open'?('opened '+(x.shop||'')+': '+(x.target||'')+(x.price?' ($'+x.price+')':'')):(x.tool+' '+(x.target||''))).join(' | ')}
  $('progress').appendChild(d)} seen=j.events.length;
 $('state').textContent=j.status==='running'?'Searching...':j.status;
 if(j.status==='done'||j.status==='error'){clearInterval(timer);$('go').disabled=false;if(j.error){$('answer').textContent=j.error}else{render(j.result)}}}
function render(r){const box=$('answer');const p=el('div','panel');p.appendChild(el('div','msg',r.message));if(r.question)p.appendChild(el('p','', 'Question: '+r.question));
 for(const w of r.warnings||[])p.appendChild(el('p','ev','Note: '+w));box.appendChild(p);
 const groups=[['best','Best matches'],['alternative','Close alternatives'],['unavailable','Checked but out of stock'],['rejected','Checked and excluded']];
 for(const [tier,label] of groups){const items=r.offers.filter(o=>o.tier===tier);if(!items.length)continue;box.appendChild(el('h2','',label));const cards=el('div','cards');
  for(const o of items){const c=el('div','card');const top=el('div','top');const left=el('div');left.appendChild(el('div','shop',o.shop));left.appendChild(el('div','title',o.title+(o.variant&&o.variant!=='Default Title'?' - '+o.variant:'')));
   const right=el('div');right.appendChild(el('div','price',o.price?(o.currency||'')+' '+o.price:'No price'));right.appendChild(el('div','total',o.total?('USD '+o.total+' delivered'):(r.request.mode==='pickup'?'pickup':'delivery fee not confirmed')));
   top.appendChild(left);top.appendChild(right);c.appendChild(top);const bs=el('div','badges');
   if(o.complete)bs.appendChild(badge('Fully checked','ok'));bs.appendChild(badge(o.in_stock===true?'In stock online':o.in_stock===false?'Out of stock':'Stock not published',o.in_stock===true?'ok':o.in_stock===false?'bad':'warn'));
   bs.appendChild(badge(o.condition==='new'?'New (stated by shop)':o.condition==='unknown'?'Condition not stated':o.condition,o.condition==='new'?'ok':o.condition==='unknown'?'warn':'bad'));
   bs.appendChild(badge(o.identity==='exact'?'Exact match':o.identity==='near'?'Close alternative':'Different product',o.identity==='exact'?'ok':'warn'));c.appendChild(bs);
   if(o.identity_reason)c.appendChild(el('div','title',o.identity_reason));
   if(o.gaps.length){const ul=el('ul','gaps');for(const g of o.gaps)ul.appendChild(el('li','',g));c.appendChild(ul)}
   if(o.downgrades.length)c.appendChild(el('div','meta','Checks: '+o.downgrades.join('; ')));
   const m=el('div','meta');m.appendChild(link(o.url,'Open at '+o.shop));m.appendChild(document.createTextNode(' - '+o.seller_basis+(o.total_basis?' - '+o.total_basis:'')));
   for(const ev of o.evidence){m.appendChild(document.createTextNode(' - read '+ev.observed_at+' ('+ev.source+')'))}c.appendChild(m);cards.appendChild(c)}
  box.appendChild(cards)}
 box.appendChild(el('div','foot',r.seconds+' s, '+r.model_calls.length+' model calls, '+r.retailer_requests+' shop requests, USD '+r.spend.settled_usd+' model cost. Run '+r.run_id))}
</script></main></body></html>"""


def main():
    import uvicorn
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level='warning')


if __name__ == '__main__':
    main()
