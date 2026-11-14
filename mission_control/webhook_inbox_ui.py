"""TradingView signal inbox workspace injection for Mission Control."""

from __future__ import annotations


def build_webhook_dashboard(html: str) -> str:
    """Add a compact read-only Signals workspace without changing dashboard.html."""
    nav_marker = '<button data-tab="accounts">Accounts</button>'
    nav_item = nav_marker + '<button data-tab="signals">Signals</button>'
    section_marker = '<section class="section" id="integrations">'
    section = r'''
<section class="section" id="signals">
 <div class="panel">
  <div class="panel-head">
   <div><div class="eyebrow">TradingView · inbound alerts</div><div class="panel-title">Strategy signal inbox</div></div>
   <div class="toolbar"><span class="pill off" id="tv-signal-count">NO SIGNALS LOADED</span><button class="btn" id="tv-signal-refresh" type="button">Refresh inbox</button></div>
  </div>
  <p class="muted small">Read-only view of alerts received by the isolated webhook receiver. Signals are not orders, fills, or approvals.</p>
  <div id="tv-signal-list"><div class="placeholder"><div><strong>Loading signal inbox</strong>No broker connection or order submission occurs.</div></div></div>
  <div class="status-row"><span class="muted">Tradovate / NinjaTrader execution</span><b>Not connected · Disabled</b></div>
  <div class="status-row"><span class="muted">Human approval</span><b>Required</b></div>
 </div>
</section>
'''
    if nav_marker in html and 'id="signals"' not in html:
        html = html.replace(nav_marker, nav_item, 1)
    if section_marker in html and 'id="signals"' not in html:
        html = html.replace(section_marker, section + "\n" + section_marker, 1)

    script = r'''
<script>
(function(){
"use strict";
const button=document.getElementById("tv-signal-refresh");
const list=document.getElementById("tv-signal-list");
const count=document.getElementById("tv-signal-count");
if(!button||!list||!count)return;
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
function money(value){return value==null?"—":Number(value).toLocaleString(undefined,{maximumFractionDigits:4});}
async function loadSignals(){
 button.disabled=true;count.textContent="LOADING";list.textContent="Loading recent alerts…";
 try{
  const response=await fetch("/api/webhooks/tradingview/inbox?limit=100");
  const data=await response.json();if(!response.ok)throw new Error(data.detail||"Inbox unavailable");
  const rows=data.signals||[];count.textContent=rows.length+" RECENT ALERTS";count.className=rows.length?"pill ok":"pill off";
  list.replaceChildren();
  if(!rows.length){const empty=document.createElement("div");empty.className="placeholder";const inner=document.createElement("div");const strong=document.createElement("strong");strong.textContent="No TradingView alerts received";const note=document.createElement("span");note.textContent="The receiver is separate and must be running with a configured secret and secure inbound HTTPS before TradingView can reach it.";inner.append(strong,note);empty.append(inner);list.append(empty);return;}
  rows.forEach(signal=>{
   const card=document.createElement("article");card.className="tile";card.style.marginBottom="9px";
   const head=document.createElement("div");head.style.display="flex";head.style.justifyContent="space-between";head.style.gap="10px";head.style.flexWrap="wrap";
   const title=document.createElement("b");title.textContent=signal.action.toUpperCase()+" · "+signal.symbol+" · "+(signal.quantity??"—")+" contract(s)";
   const status=document.createElement("span");status.className="pill off";status.textContent=String(signal.status||"received").toUpperCase();
   head.append(title,status);
   const details=document.createElement("div");details.className="muted small";details.style.marginTop="7px";
   details.textContent="Entry "+money(signal.entry_price)+" · Stop "+money(signal.stop_loss)+" · Target "+money(signal.take_profit)+" · "+signal.order_type;
   const meta=document.createElement("div");meta.className="muted small";meta.style.marginTop="5px";
   meta.textContent="Strategy "+signal.strategy_id+" · Received "+new Date(signal.received_at).toLocaleString()+" · Alert "+signal.alert_id;
   const route=document.createElement("div");route.className="muted small";route.style.marginTop="4px";route.textContent="Account route alias: "+(signal.route_key||"Not assigned")+" (not verified or authorized)";
   card.append(head,details,meta,route);list.append(card);
  });
 }catch(error){count.textContent="INBOX UNAVAILABLE";count.className="pill warn";list.textContent="Could not load webhook inbox: "+error.message;}
 finally{button.disabled=false;}
}
button.addEventListener("click",loadSignals);
loadSignals();
})();
</script>
'''
    if "</body>" in html and 'id="tv-signal-refresh"' not in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    return html
