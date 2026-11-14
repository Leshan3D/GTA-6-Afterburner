"""Local-only, read-only dashboard. Distinct process, port, files from JAFAR."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from .storage import utcnow

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AFTERBURNER | Meme Radar</title>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0a0b12;color:#e5e8f1}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#151927 0%,#0a0b12 50%)}
main{max-width:1250px;margin:auto;padding:34px 20px}header{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}
h1{letter-spacing:.045em;margin:.15em 0;font-size:2.05rem}small,p,.muted{color:#9ba3b4}p{line-height:1.55}
.label{font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;color:#a5b4fc}.pill{border:1px solid #3d4456;border-radius:30px;padding:7px 12px;color:#cbd5e1;font-size:.77rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin-top:20px}
.card{border:1px solid #30384b;border-radius:14px;padding:18px;background:#131724;min-height:230px}
.card h2{margin:0;font-size:1.23rem}.price{font-size:1.45rem;font-weight:750;margin:12px 0}.score{display:flex;gap:8px;flex-wrap:wrap}
.metric{background:#202637;padding:7px 10px;border-radius:7px;font-size:.8rem}.positive{color:#72e2a8}.negative{color:#f9949c}.warning{color:#f9c37a}
a{color:#a7bafa}section{margin-top:28px}table{border-collapse:collapse;width:100%;font-size:.86rem}td,th{text-align:left;padding:10px;border-bottom:1px solid #30384b;vertical-align:top}th{color:#aab4c6;font-weight:600}
pre{white-space:pre-wrap;word-break:break-word}.notice{padding:12px 16px;border:1px solid #505064;background:#1c1c2b;border-radius:10px}
@media(max-width:600px){main{padding:20px 12px}h1{font-size:1.65rem}table{font-size:.78rem}}
</style></head><body><main>
<header><div><div class="label">Standalone intelligence research</div><h1>🔥 AFTERBURNER</h1><small>GTA VI narrative · BingX futures + Solana DEX</small></div>
<span class="pill">ALERT-ONLY · NO ORDER EXECUTION</span></header>
<section class="notice"><b>Important:</b> A watchlist signal is not a buy/sell order. Unverified Solana mint identity, weak exchange order-book depth, or stale feeds block verified setup status. Gaming headlines measure attention, not guaranteed direction.</section>
<section><h2>Markets</h2><div class="grid" id="cards"><p>Collecting data…</p></div></section>
<section><h2>News & catalyst radar</h2><small>Discovery feeds; not official Rockstar confirmation. Verify every announcement.</small><div id="news"><p>Fetching articles…</p></div></section>
<section><h2>Feed health</h2><div id="health"><p>Awaiting feed checks…</p></div></section>
<footer><p><small>Updated automatically every 60 seconds · All prices and scores are observational, not investment instructions.</small></p></footer>
<script>
const $=(id)=>document.getElementById(id);
const text=(v,empty='N/A')=>v===null||v===undefined||v===''?empty:String(v);
const money=(x,d=2)=>typeof x==='number'&&Number.isFinite(x)?'$'+x.toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}):'N/A';
const el=(tag,cls,body)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(body!==undefined)node.textContent=body;return node};
const append=(parent,tag,cls,body)=>{const node=el(tag,cls,body);parent.appendChild(node);return node};
async function plot(symbol,canvas){try{const response=await fetch('/api/history?symbol='+encodeURIComponent(symbol)+'&limit=100');const d=await response.json();const prices=d.history.map(x=>x.price).filter(x=>typeof x==='number'&&Number.isFinite(x));if(prices.length<2)return;const ctx=canvas.getContext('2d');const lo=Math.min(...prices),hi=Math.max(...prices),spread=hi-lo||1;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.beginPath();ctx.lineWidth=2;ctx.strokeStyle=prices.at(-1)>=prices[0]?'#72e2a8':'#f9949c';prices.forEach((p,i)=>{const x=i/(prices.length-1)*canvas.width;const y=(1-(p-lo)/spread)*(canvas.height-10)+5;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke()}catch(e){}}
async function refresh(){
  try{
    const r=await fetch('/api/assets',{cache:'no-store'});if(!r.ok)throw Error('market endpoint '+r.status);const body=await r.json();const root=$('cards');root.replaceChildren();
    for(const asset of body.assets){
      const card=append(root,'article','card');append(card,'h2','',asset.label+' ('+asset.symbol+')');
      const snapshot=asset.snapshot;
      if(!snapshot){append(card,'p','warning','Waiting for first market run');continue;}
      const m=snapshot.market||{},sig=snapshot.signal||{},dex=snapshot.dex||{};
      append(card,'div','price',money(m.price,8));
      append(card,'div','muted','24h: '+(m.change_24h_pct===null?'N/A':Number(m.change_24h_pct).toFixed(2)+'%')+' · '+money(m.volume_24h_usd,0)+' volume');
      const chart=append(card,'canvas');chart.width=340;chart.height=65;chart.style.width='100%';chart.style.maxHeight='65px';
      plot(asset.symbol,chart);
      const row=append(card,'div','score');append(row,'span','metric','Long '+text(sig.long_score)+'/100');append(row,'span','metric','Short '+text(sig.short_score)+'/100');
      append(card,'p',sig.state==='REVIEW_CANDIDATE'?'positive':'warning',text(sig.state));
      append(card,'div','muted','DEX pool: '+money((dex.primary||{}).liquidity_usd,0)+' · spread: '+text(m.spread_pct===undefined?null:m.spread_pct?.toFixed(3))+'%');
      append(card,'div','muted','Flags: '+text((sig.blocking_flags||[]).join(', '),'none'));
      if(asset.trade_url){const link=append(card,'a','', 'Open BingX chart');link.href=asset.trade_url;link.target='_blank';link.rel='noopener noreferrer';}
      if(asset.dex_url){const link=append(card,'a','', ' · DEX Screener');link.href=asset.dex_url;link.target='_blank';link.rel='noopener noreferrer';}
      append(card,'div','muted','Observed '+text(snapshot.collected_at));
    }
  }catch(e){$('cards').textContent='Market feed unavailable: '+e.message}
  try{
    const r=await fetch('/api/news',{cache:'no-store'});const body=await r.json();const root=$('news');root.replaceChildren();
    if(!body.articles.length){append(root,'p','muted','No matching articles yet; source health below.')}else{
      const t=append(root,'table'),thead=append(t,'thead'),h=append(thead,'tr');['Headline','Feed','First seen (UTC)','Relevance'].forEach(s=>append(h,'th','',s));const tb=append(t,'tbody');
      for(const article of body.articles.slice(0,18)) {const tr=append(tb,'tr');const td=append(tr,'td');const a=append(td,'a','',article.headline);if(typeof article.url==='string'&&article.url.startsWith('https://')){a.href=article.url;a.rel='noopener noreferrer';a.target='_blank'}append(tr,'td','',article.source_id);append(tr,'td','',article.first_seen);append(tr,'td','',article.asset_symbols.join(', '));}
    }
  }catch(e){$('news').textContent='News unavailable'}
  try{
    const r=await fetch('/api/health',{cache:'no-store'});const body=await r.json();const root=$('health');root.replaceChildren();const t=append(root,'table');const h=append(append(t,'thead'),'tr');['Source','Status','Last check (UTC)','Message'].forEach(s=>append(h,'th','',s));const tb=append(t,'tbody');for(const row of body.sources){const tr=append(tb,'tr');append(tr,'td','',row.source);append(tr,'td',row.state==='ok'?'positive':'warning',row.state);append(tr,'td','',row.updated_at);append(tr,'td','',row.message||'—');}
  }catch(e){$('health').textContent='Health checks unavailable'}
}
refresh();setInterval(refresh,60000);
</script></main></body></html>'''


def build_server(storage, assets, config):
    """A small no-dependency HTTP server bound to loopback only."""
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, payload, content_type="application/json"):
            data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/":
                return self.respond(200, HTML, "text/html")
            if route == "/healthz":
                return self.respond(200, {"ok": True, "service": "afterburner", "execution": "disabled"})
            if route == "/api/assets":
                latest = storage.latest([a.symbol for a in assets])
                items = []
                for a in assets:
                    snap = latest[a.symbol]
                    if snap:
                        age = (utcnow()-datetime.fromisoformat(snap["collected_at"])).total_seconds()
                        snap["age_seconds"] = int(age)
                        snap["stale"] = age > config["schedule"]["max_market_age_seconds"]
                        if snap["stale"]:
                            snap["signal"]["state"] = "STALE"
                    items.append({"symbol": a.symbol, "label": a.label,
                                  "trade_url": f"https://bingx.com/en/perpetual/{a.symbol}",
                                  "dex_url": f"https://dexscreener.com/{a.dex_chain}/{a.candidate_mint}" if a.candidate_mint else None,
                                  "snapshot": snap})
                return self.respond(200, {"assets": items, "generated_at": utcnow().isoformat(),
                                          "order_execution_enabled": False})
            if route == "/api/history":
                params = parse_qs(urlparse(self.path).query)
                symbol = (params.get("symbol") or [""])[0]
                if symbol not in {a.symbol for a in assets}:
                    return self.respond(400, {"error":"invalid asset"})
                try:
                    limit = int((params.get("limit") or ["120"])[0])
                except ValueError:
                    limit = 120
                return self.respond(200, {"symbol":symbol, "history": storage.history(symbol, limit)})
            if route == "/api/news":
                return self.respond(200, {"articles": storage.articles(hours=config["narrative"]["lookback_hours"], limit=60)})
            if route == "/api/health":
                return self.respond(200, {"sources": storage.health(), "checked_at": utcnow().isoformat()})
            return self.respond(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            import logging
            logging.getLogger("afterburner.dashboard").info(fmt, *args)

    host, port = config["server"]["host"], int(config["server"]["port"])
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Dashboard must bind to loopback only")
    return ThreadingHTTPServer((host, port), Handler)
