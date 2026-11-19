from __future__ import annotations

"""Safely repair FAB787 QUANTUM news rendering and GEX price/visuals.

Run from the futures-mission-control repository root with the project .venv.
This script changes only main.py, dashboard.html, gex.py, and test_gex.py.
It creates timestamped backups and rolls back those exact files on any failure.
No Git commands are executed.
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
MC = ROOT / "mission_control"
TESTS = ROOT / "tests"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def require_once(text: str, needle: str, label: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise RuntimeError(f"Expected exactly one {label} anchor; found {count}. No files changed.")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    require_once(text, old, label)
    return text.replace(old, new, 1)


def preserve_newline(path: Path, text: str) -> None:
    raw = path.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    path.write_text(text, encoding="utf-8", newline=nl)


GEX_ROUTE = '''@app.get("/api/gex")
async def gex(
    futures_price: float | None = Query(default=None, gt=0),
    provider: str = Query(default="tradingview", pattern="^(tradingview|webull)$"),
) -> dict[str, Any]:
    """Return Cboe NDX GEX mapped only from a plausible MNQ/NQ price."""
    price_source = "manual input"
    resolved_price = futures_price

    if resolved_price is None:
        preferred = provider
        managers = {
            "tradingview": market_data_manager,
            "webull": globals().get("webull_market_data_manager"),
        }
        order = [preferred, "webull" if preferred == "tradingview" else "tradingview"]
        for provider_name in order:
            manager = managers.get(provider_name)
            if manager is None:
                continue
            cached = manager.peek_snapshot("1m")
            cache_meta = (cached.get("cache") or {}) if cached else {}
            if cached is None or cache_meta.get("status") != "hit":
                continue
            mnq = next(
                (item for item in cached.get("instruments", []) if item.get("symbol") == "MNQ"),
                None,
            )
            latest = mnq.get("latest") if isinstance(mnq, dict) else None
            close = latest.get("c") if isinstance(latest, dict) else None
            try:
                candidate = float(close)
            except (TypeError, ValueError):
                candidate = 0.0
            if candidate > 0:
                resolved_price = candidate
                price_source = (
                    f"cached MNQ {provider_name.title()} snapshot "
                    f"({cache_meta.get('age_seconds', 'unknown')}s old)"
                )
                break

    if resolved_price is None or resolved_price <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No fresh MNQ/NQ price is cached. This endpoint will not trigger a TradingView request. "
                "Select Webull or TradingView in Overview and refresh market data, or enter the actual "
                "MNQ/NQ index price. Do not enter a micro-dollar value or another instrument's price."
            ),
        )

    try:
        return await gex_service.get_levels(resolved_price, price_source=price_source)
    except GexUnavailable as exc:
        status_code = 400 if "implausible ratio" in str(exc) else 503
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


'''

GEX_SNAPSHOT_ROUTE = '''@app.get("/api/gex/snapshot")
def gex_snapshot(
    provider: str = Query(default="tradingview", pattern="^(tradingview|webull)$"),
) -> dict[str, Any]:
    """Return a cache-only GEX snapshot using the selected provider's cached MNQ quote."""
    managers = {
        "tradingview": market_data_manager,
        "webull": globals().get("webull_market_data_manager"),
    }
    order = [provider, "webull" if provider == "tradingview" else "tradingview"]
    for provider_name in order:
        manager = managers.get(provider_name)
        if manager is None:
            continue
        cached = manager.peek_snapshot("1m")
        cache_meta = (cached.get("cache") or {}) if cached else {}
        if cached is None or cache_meta.get("status") != "hit":
            continue
        mnq = next((item for item in cached.get("instruments", []) if item.get("symbol") == "MNQ"), None)
        latest = mnq.get("latest") if isinstance(mnq, dict) else None
        raw_price = latest.get("c") if isinstance(latest, dict) else None
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        try:
            result = gex_service.peek_levels(
                price,
                price_source=f"cached MNQ {provider_name.title()} market-data snapshot",
            )
        except GexUnavailable as exc:
            return {"status": "unavailable", "source": "Cboe delayed options", "message": str(exc)}
        if result is not None:
            return result
    return {
        "status": "not_loaded",
        "source": "Cboe delayed options",
        "message": "No cached Cboe options chain and fresh MNQ price are both available. Open GEX and load it once.",
    }


'''

NEWS_LOADER = r'''async function fetchPanelJson(url){
  const response=await fetch(url,{cache:"no-store",signal:AbortSignal.timeout(45000)});
  let payload={};
  try{payload=await response.json();}catch(_error){throw new Error("Server returned invalid JSON (HTTP "+response.status+")");}
  if(!response.ok){const detail=payload.detail?.message||payload.detail||payload.error||("HTTP "+response.status);throw new Error(String(detail));}
  return payload;
}
function showPanelLoadError(pillId,feedId,label,error){
  setPill(pillId,"unavailable",label+" UNAVAILABLE");
  const feed=document.getElementById(feedId);
  if(feed){feed.replaceChildren();const empty=document.createElement("div");empty.className="placeholder";empty.textContent=String(error?.message||error||"Request failed");feed.appendChild(empty);}
}
async function loadNews(){
  setPill("news-status","checking","CHECKING YAHOO");
  setPill("webull-news-status","checking","CHECKING WEBULL");
  setPill("calendar-status","checking","CHECKING CALENDAR");
  // Render every source independently; a slow provider must not hold the other columns hostage.
  const yahooTask=fetchPanelJson("/api/news").then(renderYahooNews).catch(error=>{
    showPanelLoadError("news-status","news-feed","YAHOO",error);
    const meta=document.getElementById("news-meta");if(meta)meta.textContent="Yahoo request failed: "+error.message;
  });
  const webullTask=fetchPanelJson("/api/news/webull").then(renderWebullNews).catch(error=>{
    showPanelLoadError("webull-news-status","webull-news-feed","WEBULL",error);
    const meta=document.getElementById("webull-news-meta");if(meta)meta.textContent="Webull request failed: "+error.message;
  });
  const calendarTask=fetchPanelJson("/api/calendar/events").then(renderCalendar).catch(error=>{
    showPanelLoadError("calendar-status","calendar-feed","CALENDAR",error);
    const meta=document.getElementById("calendar-meta");if(meta)meta.textContent="Calendar request failed: "+error.message;
  });
  const homeTask=loadHomeIntelligence().catch(error=>{
    const status=document.getElementById("home-sentiment-status");if(status){status.textContent="INTELLIGENCE ERROR";status.className="pill warn";}
    console.error("Home intelligence failed",error);
  });
  await Promise.allSettled([yahooTask,webullTask,calendarTask,homeTask]);
}
'''

GEX_CHART_JS = r'''function renderGexChart(data){
 const svg=document.getElementById("gex-chart");if(!svg)return;
 const rows=(data.all_strikes||[]).filter(x=>Number.isFinite(Number(x.futures_level))&&Number.isFinite(Number(x.net_gex))).sort((a,b)=>Number(a.futures_level)-Number(b.futures_level));
 if(!rows.length){svg.innerHTML='<text x="500" y="180" text-anchor="middle" fill="#91a7c2" font-size="16">No strike exposure rows available</text>';return;}
 const min=Math.min(...rows.map(x=>Number(x.futures_level))),max=Math.max(...rows.map(x=>Number(x.futures_level))),span=max-min||1;
 const maxAbs=Math.max(1,...rows.map(x=>Math.abs(Number(x.net_gex))));
 const left=76,right=962,zero=190,barMax=115,step=(right-left)/Math.max(1,rows.length),barW=Math.max(2,Math.min(13,step*.72));
 const price=v=>Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 let parts=['<line x1="'+left+'" y1="'+zero+'" x2="'+right+'" y2="'+zero+'" stroke="#53657b" stroke-width="1"/>'];
 rows.forEach(item=>{const x=left+(Number(item.futures_level)-min)/span*(right-left)-barW/2;const value=Number(item.net_gex);const h=Math.max(1,Math.abs(value)/maxAbs*barMax);const y=value>=0?zero-h:zero;const color=value>=0?'#46d6a0':'#f07883';parts.push('<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+barW.toFixed(1)+'" height="'+h.toFixed(1)+'" rx="1.5" fill="'+color+'" opacity=".82"><title>MNQ '+price(item.futures_level)+' · Net GEX '+value.toLocaleString()+'</title></rect>');});
 const markers=[
  {key:'spot',value:Number(data.futures_price),label:'SPOT',color:'#62d9f5'},
  {key:'call',value:Number(data.call_wall_futures),label:'CALL WALL',color:'#46d6a0'},
  {key:'put',value:Number(data.put_wall_futures),label:'PUT WALL',color:'#f07883'},
  {key:'flip',value:data.gamma_flip_futures==null?NaN:Number(data.gamma_flip_futures),label:'GAMMA FLIP',color:'#edbd69'}
 ];
 markers.forEach((m,index)=>{
  if(!Number.isFinite(m.value)||m.value<min||m.value>max)return;
  const x=left+(m.value-min)/span*(right-left),labelY=18+index*15;
  parts.push('<line x1="'+x.toFixed(1)+'" y1="74" x2="'+x.toFixed(1)+'" y2="315" stroke="'+m.color+'" stroke-width="2.4" stroke-dasharray="'+(index===0?'0':'6 4')+'" opacity=".98"><title>'+m.label+' '+price(m.value)+'</title></line>');
  parts.push('<text x="'+Math.max(left,Math.min(right-8,x)).toFixed(1)+'" y="'+labelY+'" text-anchor="middle" fill="'+m.color+'" font-size="11" font-weight="800">'+m.label+' '+price(m.value)+'</text>');
 });
 parts.push('<text x="'+left+'" y="340" fill="#91a7c2" font-size="11">'+price(min)+'</text>');
 parts.push('<text x="'+right+'" y="340" text-anchor="end" fill="#91a7c2" font-size="11">'+price(max)+'</text>');
 parts.push('<text x="'+left+'" y="91" fill="#91a7c2" font-size="10">POSITIVE GEX</text><text x="'+left+'" y="312" fill="#91a7c2" font-size="10">NEGATIVE GEX</text>');
 svg.innerHTML=parts.join('');
 const caption=document.getElementById("gex-chart-caption");if(caption)caption.textContent=rows.length+" strikes · mapped MNQ/NQ prices · marker labels include exact levels";
}
'''

GEX_TABLE_RENDER = r'''    const body=document.querySelector("#gex-table tbody");
    body.replaceChildren();
    const markerDefs=[
      {label:"SPOT",value:Number(data.futures_price),color:"#62d9f5"},
      {label:"CALL WALL",value:Number(data.call_wall_futures),color:"#46d6a0"},
      {label:"PUT WALL",value:Number(data.put_wall_futures),color:"#f07883"},
      {label:"GAMMA FLIP",value:data.gamma_flip_futures==null?NaN:Number(data.gamma_flip_futures),color:"#edbd69"}
    ];
    const allRows=(data.all_strikes||[]).filter(x=>Number.isFinite(Number(x.futures_level)));
    const markerTargets=markerDefs.filter(m=>Number.isFinite(m.value)).map(m=>({ ...m, row: allRows.reduce((best,row)=>!best||Math.abs(Number(row.futures_level)-m.value)<Math.abs(Number(best.futures_level)-m.value)?row:best,null) })).filter(m=>m.row);
    const displayRows=[...(data.top_strikes||[])];
    markerTargets.forEach(target=>{if(!displayRows.some(row=>Math.abs(Number(row.futures_level)-Number(target.row.futures_level))<0.01))displayRows.push(target.row);});
    displayRows.forEach(item=>{
      const tr=document.createElement("tr");tr.style.borderBottom="1px solid #1e2d40";
      const tags=markerTargets.filter(target=>Math.abs(Number(target.row.futures_level)-Number(item.futures_level))<0.01);
      if(tags.length){tr.style.background="rgba(98,217,245,.055)";tr.style.boxShadow="inset 3px 0 "+tags[0].color;tr.title=tags.map(x=>x.label+" "+fmt(x.value)).join(" · ");}
      [fmt(item.futures_level),gexMoney(item.net_gex),gexMoney(item.call_gex),gexMoney(item.put_gex),fmt(item.call_oi),fmt(item.put_oi)].forEach((value,index)=>{
        const td=document.createElement("td");td.style.padding="9px";td.textContent=value;
        if(index===0){td.style.fontWeight="800";if(tags.length){td.style.color=tags[0].color;const tag=document.createElement("div");tag.style.fontSize="9px";tag.style.fontWeight="800";tag.style.letterSpacing=".05em";tag.style.marginTop="3px";tag.textContent=tags.map(x=>x.label).join(" / ");td.appendChild(tag);}}
        if(index===1)td.style.color=Number(item.net_gex)>=0?"var(--green)":"var(--red)";
        if(index===2)td.style.color="var(--green)";
        if(index===3)td.style.color="var(--red)";
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    renderGexChart(data);
    document.getElementById("gex-assumptions").textContent=data.assumptions.join(" ");'''


def main() -> int:
    if not MC.is_dir() or not PYTHON.is_file():
        print("Run from the futures-mission-control project root; .venv Python is required.", file=sys.stderr)
        return 2
    paths = [MC / "main.py", MC / "dashboard.html", MC / "gex.py", TESTS / "test_gex.py"]
    if any(not p.is_file() for p in paths):
        print("Required project files are missing. Nothing changed.", file=sys.stderr)
        return 2

    originals = {p: p.read_text(encoding="utf-8") for p in paths}
    main_text = originals[paths[0]]
    dash_text = originals[paths[1]]
    gex_text = originals[paths[2]]
    test_text = originals[paths[3]]

    # Refuse mixed/partial layouts instead of risking an overwrite.
    for anchor, label in [
        ('@app.get("/api/gex")', "GEX endpoint"),
        ('@app.get("/api/gex/snapshot")', "Home GEX snapshot endpoint"),
        ('@app.get("/api/integrations")', "integrations endpoint"),
        ('@app.get("/api/news/webull")', "Webull news endpoint"),
        ('async function loadNews(){', "News loader"),
        ('async function loadHomeIntelligence(){', "Home intelligence loader"),
        ('function renderGexChart(data){', "GEX chart renderer"),
        ('    const body=document.querySelector("#gex-table tbody");', "GEX table renderer"),
        ('const query=rawPrice?"?futures_price="+encodeURIComponent(rawPrice):"";', "GEX price query"),
        ('const data=await response.json();\n   marketCache[provider][timeframe]=data;', "market data cache update"),
        ('const scale = futures_price / spot', "GEX futures/spot scale"),
        ('will not trigger a TradingView request', "existing stale-price safety message"),
    ]:
        if anchor not in (main_text + dash_text + gex_text):
            raise RuntimeError(f"Could not find {label} anchor. No files changed.")

    if 'id="gex-price-source"' in dash_text or 'id="gex-auto-price"' in dash_text:
        raise RuntimeError("This display repair appears to have already been applied. No files changed.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: dict[Path, Path] = {}
    created: list[Path] = []
    try:
        for path in paths:
            backup = path.with_name(path.name + f".before_display_gex_repair_{stamp}.bak")
            shutil.copy2(path, backup)
            backups[path] = backup

        # Replace only the two GEX route functions; keep the following integration route intact.
        gex_route_start = main_text.index('@app.get("/api/gex")')
        gex_snap_start = main_text.index('@app.get("/api/gex/snapshot")', gex_route_start)
        gex_integrations_start = main_text.index('@app.get("/api/integrations")', gex_snap_start)
        main_text = main_text[:gex_route_start] + GEX_ROUTE + GEX_SNAPSHOT_ROUTE + main_text[gex_integrations_start:]

        # Reject wildly mismatched mappings such as NDX 27,943 mapped from MNQ 2,700.
        scale_anchor = "    scale = futures_price / spot"
        guard = '''    scale = futures_price / spot
    if not 0.80 <= scale <= 1.20:
        raise GexUnavailable(
            f"Refusing MNQ/NQ mapping: futures price {futures_price:,.2f} and NDX spot {spot:,.2f} "
            f"produce an implausible ratio of {scale:.3f}. Use the actual MNQ/NQ index quote, "
            "not a micro-dollar value or another instrument's price."
        )'''
        gex_text = replace_once(gex_text, scale_anchor, guard, "GEX ratio validation")

        # A bad manual mapping is a user-input error, not a Cboe outage: don't trigger cooldown.
        cooldown_anchor = '''            except Exception as exc:
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if cached:
'''
        cooldown_new = '''            except Exception as exc:
                if isinstance(exc, GexUnavailable) and "implausible ratio" in str(exc):
                    raise
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if cached:
'''
        gex_text = replace_once(gex_text, cooldown_anchor, cooldown_new, "GEX input-error cooldown guard")

        # Safer default price field; manual entry remains available.
        old_input = '''    <label for="gex-futures-price">MNQ / NQ futures price (optional)</label>
    <input id="gex-futures-price" type="number" min="0.01" step="0.25" placeholder="Use fresh cached MNQ price" style="width:210px;background:#0a1421;color:var(--text);border:1px solid #30445c;border-radius:7px;padding:8px 10px">
    <button class="btn" id="load-gex">Load / Update GEX</button>'''
        new_input = '''    <label for="gex-futures-price">MNQ / NQ futures price · auto from selected market feed</label>
    <input id="gex-futures-price" type="number" min="0.01" step="0.25" placeholder="Waiting for fresh MNQ quote" style="width:230px;background:#0a1421;color:var(--text);border:1px solid #30445c;border-radius:7px;padding:8px 10px">
    <button class="btn" id="gex-auto-price" type="button">Use market price</button>
    <span class="muted small" id="gex-price-source">Select a provider and refresh MNQ market data to populate automatically.</span>
    <button class="btn" id="load-gex">Load / Update GEX</button>'''
        dash_text = replace_once(dash_text, old_input, new_input, "GEX price input markup")

        # Add price-source state and auto-fill helpers beside the GEX formatter.
        money_anchor = 'function gexMoney(value){return (Number(value)/1000000).toFixed(2)+"M";}'
        helper = r'''function gexMoney(value){return (Number(value)/1000000).toFixed(2)+"M";}
let gexManualPriceOverride=false;
function updateGexAutoPrice(data,provider){
  if(gexManualPriceOverride)return;
  let chosen=data,chosenProvider=provider;
  let mnq=(chosen?.instruments||[]).find(item=>item.symbol==="MNQ");
  let price=Number(mnq?.latest?.c);
  if(!Number.isFinite(price)||price<=0){
    const fallbackProvider=provider==="webull"?"tradingview":"webull";
    const fallback=marketCache[fallbackProvider]?.["1m"];
    const fallbackRow=(fallback?.instruments||[]).find(item=>item.symbol==="MNQ");
    const fallbackPrice=Number(fallbackRow?.latest?.c);
    if(Number.isFinite(fallbackPrice)&&fallbackPrice>0){chosen=fallback;chosenProvider=fallbackProvider;mnq=fallbackRow;price=fallbackPrice;}
  }
  if(!Number.isFinite(price)||price<=0)return;
  const input=document.getElementById("gex-futures-price");if(input)input.value=price.toFixed(2);
  const source=document.getElementById("gex-price-source");
  if(source)source.textContent="Auto: "+providerLabel(chosenProvider)+" MNQ close "+fmt(price)+(mnq?.latest?.t?" · "+new Date(Number(mnq.latest.t)*1000).toLocaleTimeString():"");
}
'''
        dash_text = replace_once(dash_text, money_anchor, helper, "GEX auto-price helper")

        # Add provider to the GEX request and auto/manual controls.
        old_query = 'const query=rawPrice?"?futures_price="+encodeURIComponent(rawPrice):"";'
        new_query = 'const query="?provider="+encodeURIComponent(activeProvider)+(rawPrice?"&futures_price="+encodeURIComponent(rawPrice):"");'
        dash_text = replace_once(dash_text, old_query, new_query, "GEX provider query")

        # Replace the GEX chart renderer with numeric marker labels.
        chart_start = dash_text.index('function renderGexChart(data){')
        chart_end = dash_text.index('async function loadGex(){', chart_start)
        dash_text = dash_text[:chart_start] + GEX_CHART_JS + dash_text[chart_end:]

        # Render top exposures plus nearest strike rows for all four markers, with matching colors.
        table_start = dash_text.index('    const body=document.querySelector("#gex-table tbody");')
        table_end_anchor = '    document.getElementById("gex-assumptions").textContent=data.assumptions.join(" ");'
        table_end = dash_text.index(table_end_anchor, table_start) + len(table_end_anchor)
        dash_text = dash_text[:table_start] + GEX_TABLE_RENDER + dash_text[table_end:]

        # Trigger cache-only Home refresh after a successful GEX calculation.
        assumptions_anchor = '    document.getElementById("gex-assumptions").textContent=data.assumptions.join(" ");'
        dash_text = replace_once(dash_text, assumptions_anchor, assumptions_anchor + '\n    loadHomeIntelligence().catch(error=>console.error("Home snapshot refresh failed",error));', "Home refresh after GEX load")

        # Wire manual override and automatic-price reset.
        load_listener = 'document.getElementById("load-gex").addEventListener("click",loadGex);'
        listener_new = '''document.getElementById("load-gex").addEventListener("click",loadGex);
const gexPriceInput=document.getElementById("gex-futures-price");
if(gexPriceInput)gexPriceInput.addEventListener("input",()=>{gexManualPriceOverride=gexPriceInput.value.trim().length>0;const note=document.getElementById("gex-price-source");if(note&&gexManualPriceOverride)note.textContent="Manual price override · verify this is the actual MNQ/NQ index quote.";});
const autoGexButton=document.getElementById("gex-auto-price");
if(autoGexButton)autoGexButton.addEventListener("click",()=>{gexManualPriceOverride=false;if(gexPriceInput)gexPriceInput.value="";const cached=marketCache[activeProvider]?.["1m"];if(cached)updateGexAutoPrice(cached,activeProvider);else fetchMarketData("1m",false,activeProvider);});'''
        dash_text = replace_once(dash_text, load_listener, listener_new, "GEX input listeners")

        # Populate the GEX price whenever a fresh 1m market snapshot arrives.
        market_cache_anchor = 'const data=await response.json();\n   marketCache[provider][timeframe]=data;'
        market_cache_new = 'const data=await response.json();\n   marketCache[provider][timeframe]=data;\n   if(timeframe==="1m")updateGexAutoPrice(data,provider);'
        dash_text = replace_once(dash_text, market_cache_anchor, market_cache_new, "auto-fill from market data")

        # Use the currently selected feed for Home's cache-only snapshot.
        home_gex_fetch = 'fetch("/api/gex/snapshot").then(r=>{if(!r.ok)throw new Error("GEX snapshot unavailable");return r.json()}),'
        home_gex_new = 'fetch("/api/gex/snapshot?provider="+encodeURIComponent(activeProvider)).then(r=>{if(!r.ok)throw new Error("GEX snapshot unavailable");return r.json()}),'
        dash_text = replace_once(dash_text, home_gex_fetch, home_gex_new, "Home GEX selected provider")

        # Request and render news/calendar separately so one slow request never blocks the other columns.
        loader_start = dash_text.index('async function loadNews(){')
        loader_end = dash_text.index('document.getElementById("refresh-news").addEventListener("click",loadNews);', loader_start)
        loader_end += len('document.getElementById("refresh-news").addEventListener("click",loadNews);')
        dash_text = dash_text[:loader_start] + NEWS_LOADER + '\ndocument.getElementById("refresh-news").addEventListener("click",loadNews);' + dash_text[loader_end:]

        # Test the ratio guard with the exact bad mapping observed in the user's UI.
        test_anchor = 'def test_gex_service_caches_chain_and_recalculates_for_new_futures_price() -> None:'
        new_test = '''def test_calculate_gex_rejects_implausible_futures_to_ndx_mapping() -> None:
    from mission_control.gex import GexUnavailable
    import pytest

    with pytest.raises(GexUnavailable, match="implausible ratio"):
        calculate_gex(
            options_payload(),
            2700,
            price_source="manual input",
            cache_status="fresh",
            cache_age_seconds=0,
            retrieved_at="2026-11-15T01:00:00-05:00",
        )


def test_bad_manual_gex_mapping_does_not_trigger_provider_cooldown() -> None:
    from mission_control.gex import GexUnavailable
    import pytest

    calls = []
    def fetcher(symbol: str) -> dict:
        calls.append(symbol)
        return options_payload()

    service = GexService(fetcher=fetcher)

    async def run() -> None:
        with pytest.raises(GexUnavailable, match="implausible ratio"):
            await service.get_levels(2700, price_source="manual input")
        valid = await service.get_levels(21000, price_source="manual input")
        assert valid["futures_price"] == 21000
        assert valid["status"] == "fresh"

    asyncio.run(run())
    assert calls == ["_NDX", "_NDX"]


'''
        test_text = replace_once(test_text, test_anchor, new_test + test_anchor, "GEX regression test insertion")

        # Write the four target files only.
        preserve_newline(paths[0], main_text)
        preserve_newline(paths[1], dash_text)
        preserve_newline(paths[2], gex_text)
        preserve_newline(paths[3], test_text)

        # Python syntax + full regression suite.
        syntax = subprocess.run(
            [str(PYTHON), "-m", "py_compile", str(paths[0]), str(paths[2])],
            cwd=ROOT, capture_output=True, text=True,
        )
        if syntax.returncode:
            raise RuntimeError("Python syntax check failed:\n" + syntax.stdout + syntax.stderr)

        targeted = subprocess.run(
            [str(PYTHON), "-m", "pytest", "-q", "tests/test_gex.py", "tests/test_api.py", "tests/test_intelligence_api.py", "tests/test_webull_news.py", "tests/test_news.py", "tests/test_news_catalyst.py"],
            cwd=ROOT, capture_output=True, text=True,
        )
        print(targeted.stdout)
        if targeted.returncode:
            raise RuntimeError("Targeted tests failed:\n" + targeted.stderr)

        full = subprocess.run([str(PYTHON), "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True)
        print(full.stdout)
        if full.returncode:
            raise RuntimeError("Full regression suite failed:\n" + full.stderr)

        # Structural verification: important code is present exactly once.
        for needle, label in [
            ('/api/gex/snapshot?provider=', "provider-aware Home GEX fetch"),
            ('AbortSignal.timeout(45000)', "bounded independent news fetch"),
            ('GAMMA FLIP', "GEX gamma-flip marker"),
            ('gex-auto-price', "automatic GEX price control"),
        ]:
            if needle not in dash_text:
                raise RuntimeError(f"Post-patch verification failed: {label} missing.")

        print("\nDISPLAY / GEX REPAIR APPLIED; FULL TEST SUITE PASSED.")
        print("Backups:")
        for path, backup in backups.items():
            print(f"  {backup.relative_to(ROOT)}")
        print("Only main.py, dashboard.html, gex.py, and tests/test_gex.py were modified. No Git commands were run.")
        return 0

    except Exception as exc:
        for path, backup in backups.items():
            if backup.exists():
                shutil.copy2(backup, path)
        print(f"PATCH ROLLED BACK: {exc}", file=sys.stderr)
        print("Original files restored from timestamped backups. No Git commands were run.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
