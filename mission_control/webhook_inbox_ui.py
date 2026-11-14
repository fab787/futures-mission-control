"""TradingView signal inbox and explicit account-to-risk-review workspace."""

from __future__ import annotations


def build_webhook_dashboard(html: str) -> str:
    """Add a controlled Signals workspace without changing the dashboard template."""
    nav_marker = '<button data-tab="accounts">Accounts</button>'
    section_marker = '<section class="section" id="integrations">'
    section = r'''
<section class="section" id="signals">
 <div class="panel">
  <div class="panel-head">
   <div><div class="eyebrow">TradingView · inbound alerts</div><div class="panel-title">Strategy signal inbox</div></div>
   <div class="toolbar"><span class="pill off" id="tv-signal-count">NO SIGNALS LOADED</span><button class="btn" id="tv-signal-refresh" type="button">Refresh inbox</button></div>
  </div>
  <p class="muted small">Alerts are signals, not orders or fills. Select one registered account and provide its current account snapshot to submit an entry signal to the Master Risk Engine.</p>
 </div>
 <div class="panel">
  <div class="panel-head"><div><div class="eyebrow">Explicit account selection</div><div class="panel-title">Risk-review snapshot</div></div><span class="pill warn">NO BROKER SYNC</span></div>
  <div class="cards2">
   <label>Registered active account<select class="control" id="tv-route-account" required style="display:block;width:100%;margin-top:5px"><option value="">Loading active accounts…</option></select></label>
   <label>Snapshot timestamp · actual time of account data<input class="control" id="tv-snapshot-at" type="datetime-local" required style="display:block;width:100%;margin-top:5px"></label>
   <label>Current session<select class="control" id="tv-current-session" required style="display:block;width:100%;margin-top:5px"><option value="NY">NY</option><option value="GLOBEX">GLOBEX</option></select></label>
   <label>Daily P&amp;L ($)<input class="control" id="tv-daily-pnl" type="number" step="0.01" required style="display:block;width:100%;margin-top:5px"></label>
   <label>Session P&amp;L ($)<input class="control" id="tv-session-pnl" type="number" step="0.01" required style="display:block;width:100%;margin-top:5px"></label>
   <label>Drawdown from high-water ($)<input class="control" id="tv-drawdown" type="number" min="0" step="0.01" required style="display:block;width:100%;margin-top:5px"></label>
   <label>Open contracts<input class="control" id="tv-open-contracts" type="number" min="0" step="1" required style="display:block;width:100%;margin-top:5px"></label>
   <label>Position state<select class="control" id="tv-position-state" required style="display:block;width:100%;margin-top:5px"><option value="flat">Flat</option><option value="open">Open</option><option value="unknown">Unknown</option></select></label>
  </div>
  <p class="muted small" style="margin-top:9px">Enter the real timestamp of the account snapshot, not the current time unless the values were just checked. Snapshots older than 120 seconds are rejected. Unknown positions fail closed.</p>
  <div class="muted small" id="tv-route-message" aria-live="polite" style="margin-top:8px"></div>
 </div>
 <div class="panel">
  <div class="panel-head"><div><div class="eyebrow">Inbound strategy alerts</div><div class="panel-title">Review eligible entry signals</div></div></div>
  <div id="tv-signal-list"><div class="placeholder"><div><strong>Loading signal inbox</strong>No broker connection or order submission occurs.</div></div></div>
  <div class="status-row"><span class="muted">Tradovate / NinjaTrader execution</span><b>Not connected · Disabled</b></div>
  <div class="status-row"><span class="muted">Human approval</span><b>Required after risk review</b></div>
 </div>
</section>
'''
    if nav_marker in html and 'id="signals"' not in html:
        html = html.replace(nav_marker, nav_marker + '<button data-tab="signals">Signals</button>', 1)
    if section_marker in html and 'id="signals"' not in html:
        html = html.replace(section_marker, section + "\n" + section_marker, 1)

    script = r'''
<script>
(function(){
"use strict";
const button=document.getElementById("tv-signal-refresh");
const list=document.getElementById("tv-signal-list");
const count=document.getElementById("tv-signal-count");
const accountSelect=document.getElementById("tv-route-account");
const message=document.getElementById("tv-route-message");
if(!button||!list||!count||!accountSelect||!message)return;
const $=id=>document.getElementById(id);
const money=value=>value==null?"—":Number(value).toLocaleString(undefined,{maximumFractionDigits:4});
let signals=[];
async function loadAccounts(){
 try{
  const response=await fetch("/api/accounts?include_inactive=false");
  const data=await response.json();if(!response.ok)throw new Error(data.detail||"Account registry unavailable");
  accountSelect.replaceChildren(new Option("Select the account for this alert",""));
  (data.accounts||[]).filter(a=>a.status==="active").forEach(a=>{
   const option=new Option(a.firm+" · "+a.nickname+" · "+a.account_mode,a.account_id);
   accountSelect.add(option);
  });
  if(!accountSelect.options.length||accountSelect.options.length===1)message.textContent="No active registered accounts. Add an account and its dedicated risk profile in Accounts first.";
 }catch(error){accountSelect.replaceChildren(new Option("Account registry unavailable",""));message.textContent=error.message;}
}
function accountSnapshot(){
 const timestamp=$("tv-snapshot-at").value;
 if(!timestamp)throw new Error("Enter the actual timestamp of the account snapshot.");
 const parsed=new Date(timestamp);
 if(Number.isNaN(parsed.getTime()))throw new Error("Snapshot timestamp is invalid.");
 const number=id=>{const value=$(id).value;if(value==="")throw new Error("Complete every account snapshot field.");const parsedNumber=Number(value);if(!Number.isFinite(parsedNumber))throw new Error("Account snapshot contains an invalid number.");return parsedNumber;};
 return {
  snapshot_at:parsed.toISOString(),
  current_session:$("tv-current-session").value,
  daily_pnl:number("tv-daily-pnl"),
  session_pnl:number("tv-session-pnl"),
  drawdown_from_high_water:number("tv-drawdown"),
  open_contracts:number("tv-open-contracts"),
  position_state:$("tv-position-state").value
 };
}
async function submitSignal(signal,actionButton){
 if(!accountSelect.value){message.textContent="Select the exact registered account first.";return;}
 let account_state;
 try{account_state=accountSnapshot();}catch(error){message.textContent=error.message;return;}
 if(signal.action!=="buy"&&signal.action!=="sell"){message.textContent="CLOSE/FLATTEN alerts remain review-only; they cannot create new entry proposals.";return;}
 actionButton.disabled=true;message.textContent="Submitting alert to the independent Master Risk Engine…";
 try{
  const response=await fetch("/api/approvals/proposals/from-webhook/"+encodeURIComponent(signal.alert_id),{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({account_id:accountSelect.value,account_state,submitted_by:"local_operator"})
  });
  const data=await response.json();if(!response.ok)throw new Error(data.detail||"Risk review request rejected");
  const risk=data.risk_review||{};
  message.textContent="Proposal "+data.proposal_id+" · "+String(data.status).toUpperCase()+" · Master Risk: "+String(risk.status||"unknown")+
   (risk.blockers&&risk.blockers.length?" · "+risk.blockers.join(" | "):"")+
   " · No order submitted. Review it under Risk & Approvals.";
  await loadSignals();
 }catch(error){message.textContent="Proposal not created: "+error.message;actionButton.disabled=false;}
}
async function loadSignals(){
 button.disabled=true;count.textContent="LOADING";list.textContent="Loading recent alerts…";
 try{
  const response=await fetch("/api/webhooks/tradingview/inbox?limit=100");
  const data=await response.json();if(!response.ok)throw new Error(data.detail||"Inbox unavailable");
  signals=data.signals||[];count.textContent=signals.length+" RECENT ALERTS";count.className=signals.length?"pill ok":"pill off";
  list.replaceChildren();
  if(!signals.length){const empty=document.createElement("div");empty.className="placeholder";const inner=document.createElement("div");const strong=document.createElement("strong");strong.textContent="No TradingView alerts received";const note=document.createElement("span");note.textContent="The isolated receiver must be running and securely reachable before TradingView can send alerts.";inner.append(strong,note);empty.append(inner);list.append(empty);return;}
  signals.forEach(signal=>{
   const card=document.createElement("article");card.className="tile";card.style.marginBottom="9px";
   const head=document.createElement("div");head.style.display="flex";head.style.justifyContent="space-between";head.style.gap="10px";head.style.flexWrap="wrap";
   const title=document.createElement("b");title.textContent=signal.action.toUpperCase()+" · "+signal.symbol+" · "+(signal.quantity??"—")+" contract(s)";
   const status=document.createElement("span");status.className="pill off";status.textContent=String(signal.status||"received").toUpperCase();head.append(title,status);
   const details=document.createElement("div");details.className="muted small";details.style.marginTop="7px";
   details.textContent="Entry "+money(signal.entry_price)+" · Stop "+money(signal.stop_loss)+" · Target "+money(signal.take_profit)+" · "+signal.order_type;
   const meta=document.createElement("div");meta.className="muted small";meta.style.marginTop="5px";
   meta.textContent="Strategy "+signal.strategy_id+" · Received "+new Date(signal.received_at).toLocaleString()+" · Alert "+signal.alert_id;
   const route=document.createElement("div");route.className="muted small";route.style.marginTop="4px";
   route.textContent="Route alias: "+(signal.route_key||"Not assigned")+" · Alias is informational only; account is selected above.";
   card.append(head,details,meta,route);
   if(signal.status==="received"&&(signal.action==="buy"||signal.action==="sell")){
    const action=document.createElement("button");action.type="button";action.className="btn";action.style.marginTop="10px";action.textContent="Submit to Master Risk Engine";
    action.addEventListener("click",()=>submitSignal(signal,action));card.append(action);
   }else if(signal.action==="close"||signal.action==="flatten"){
    const note=document.createElement("div");note.className="muted small";note.style.marginTop="9px";note.textContent="Review-only alert. No entry proposal can be created from CLOSE/FLATTEN.";card.append(note);
   }
   list.append(card);
  });
 }catch(error){count.textContent="INBOX UNAVAILABLE";count.className="pill warn";list.textContent="Could not load webhook inbox: "+error.message;}
 finally{button.disabled=false;}
}
button.addEventListener("click",loadSignals);
loadAccounts();loadSignals();
})();
</script>
'''
    if "</body>" in html and 'id="tv-signal-refresh"' not in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    return html
