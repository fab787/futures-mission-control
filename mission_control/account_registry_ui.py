"""Account registry workspace injection for the existing Mission Control dashboard."""

from __future__ import annotations


def build_dashboard(html: str) -> str:
    """Add a registry workspace without rewriting the existing dashboard template."""
    nav_marker = '<button data-tab="risk">Risk & Approvals</button>'
    nav_item = nav_marker + '<button data-tab="accounts">Accounts</button>'
    section_marker = '<section class="section" id="integrations">'
    section = r'''
<section class="section" id="accounts">
 <div class="panel">
  <div class="panel-head">
   <div><div class="eyebrow">Local account registry</div><div class="panel-title">Prop-firm account manager</div></div>
   <div class="toolbar"><span class="pill off" id="registry-count">LOADING ACCOUNTS</span><button class="btn" id="registry-refresh" type="button">Refresh registry</button></div>
  </div>
  <div class="grid3">
   <div class="metric"><small>Registered accounts</small><b id="registry-total">—</b></div>
   <div class="metric"><small>Active accounts</small><b id="registry-active">—</b></div>
   <div class="metric"><small>Linked risk profiles</small><b id="registry-linked">—</b></div>
  </div>
  <p class="muted small" style="margin:12px 0 0">Each account is kept separate, including multiple evaluation or funded accounts at the same firm. Every account must have its own matching risk profile. Registry data is local and operator supplied.</p>
 </div>
 <div class="grid2">
  <div class="panel">
   <div class="panel-head"><div><div class="eyebrow">Explicit operator action</div><div class="panel-title">Register an account</div></div><span class="pill warn">NO LOGIN / NO BROKER LINK</span></div>
   <form id="registry-create-form">
    <div class="cards2">
     <label>Prop firm<select class="control" id="registry-firm" required style="display:block;width:100%;margin-top:5px"><option value="Phidias">Phidias</option><option value="Lucid">Lucid</option><option value="BluSky">BluSky</option><option value="Legends">Legends</option></select></label>
     <label>Account type<select class="control" id="registry-mode" required style="display:block;width:100%;margin-top:5px"><option value="evaluation">Evaluation</option><option value="funded">Funded</option></select></label>
     <label>Nickname<input class="control" id="registry-nickname" required maxlength="100" placeholder="e.g. Lucid Eval A" style="display:block;width:100%;margin-top:5px"></label>
     <label>Account size ($)<input class="control" id="registry-size" required type="number" min="0.01" step="0.01" placeholder="e.g. 50000" style="display:block;width:100%;margin-top:5px"></label>
     <label>Current balance ($) · optional<input class="control" id="registry-balance" type="number" min="0.01" step="0.01" placeholder="Operator supplied" style="display:block;width:100%;margin-top:5px"></label>
     <label>Initial status<select class="control" id="registry-status" style="display:block;width:100%;margin-top:5px"><option value="active">Active</option><option value="inactive">Inactive</option><option value="completed">Completed</option></select></label>
     <label style="grid-column:1/-1">Dedicated matching risk profile<select class="control" id="registry-profile" required style="display:block;width:100%;margin-top:5px"><option value="">Load profiles…</option></select></label>
    </div>
    <div class="muted small" id="registry-profile-help" style="margin-top:8px">Only unused profiles with the same firm and account type can be selected.</div>
    <div class="toolbar" style="margin-top:13px"><button class="btn" type="submit">Register account</button><button class="btn" id="registry-open-risk" type="button">Create / review risk profile ↗</button></div>
    <div class="muted small" id="registry-create-message" aria-live="polite" style="margin-top:9px"></div>
   </form>
  </div>
  <div class="panel">
   <div class="panel-head"><div><div class="eyebrow">Account isolation</div><div class="panel-title">Registry rules</div></div><span class="pill ok">LOCAL ONLY</span></div>
   <div class="status-row"><span class="muted">Account identity</span><b>Separate record per account</b></div>
   <div class="status-row"><span class="muted">Risk profile</span><b>One unique profile per account</b></div>
   <div class="status-row"><span class="muted">Firm / mode / size / linkage</span><b>Fixed after registration</b></div>
   <div class="status-row"><span class="muted">Editable fields</span><b>Nickname, status, balance</b></div>
   <div class="status-row"><span class="muted">Provider requests</span><b>0 · registry only</b></div>
   <div class="status-row"><span class="muted">Order execution</span><b style="color:var(--green)">Disabled</b></div>
   <p class="notice">Account balances and status are not synchronized with a prop firm or broker. Do not treat manually entered values as authenticated account state.</p>
  </div>
 </div>
 <div class="panel" style="margin-top:12px">
  <div class="panel-head"><div><div class="eyebrow">Separate account records</div><div class="panel-title">Registered accounts</div></div><label class="toolbar small"><input type="checkbox" id="registry-show-inactive" checked> Include inactive / completed</label></div>
  <div id="registry-account-list"><div class="placeholder"><div><strong>Loading account registry</strong>No provider or broker connection is made.</div></div></div>
 </div>
 <div class="panel" style="margin-top:12px">
  <div class="panel-head"><div><div class="eyebrow">Local account audit</div><div class="panel-title">Selected account events</div></div><span class="muted small" id="registry-event-account">Select an account to inspect its events.</span></div>
  <div id="registry-events" class="muted small">No account selected.</div>
 </div>
</section>
'''
    if nav_marker in html and 'id="accounts"' not in html:
        html = html.replace(nav_marker, nav_item, 1)
    if section_marker in html and 'id="accounts"' not in html:
        html = html.replace(section_marker, section + "\n" + section_marker, 1)

    script = r'''
<script>
(function(){
"use strict";
const registryState={profiles:[],accounts:[],selected:null};
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
function registryMoney(value){return value==null?"Not entered":Number(value).toLocaleString(undefined,{style:"currency",currency:"USD",maximumFractionDigits:2});}
function registryProfileOptions(){
 const firm=$("registry-firm").value,mode=$("registry-mode").value;
 const used=new Set(registryState.accounts.map(a=>a.risk_profile_id));
 const matches=registryState.profiles.filter(p=>p.profile.prop_firm===firm&&p.profile.account_mode===mode&&!used.has(p.profile_id));
 const select=$("registry-profile"),keep=select.value;
 select.replaceChildren(new Option("Select a dedicated risk profile",""));
 matches.forEach(p=>select.add(new Option(p.profile.profile_name+" · "+p.profile.prop_firm+" · "+p.profile.account_mode+" · v"+p.version,p.profile_id)));
 if(matches.some(p=>p.profile_id===keep))select.value=keep;
 $("registry-profile-help").textContent=matches.length
  ?matches.length+" unused matching profile(s) available. A profile already linked to another account cannot be reused."
  :"No unused matching profile. Create one in Risk & Approvals first; use the same firm and account type.";
}
function registryRenderAccounts(){
 const box=$("registry-account-list"),show=$("registry-show-inactive").checked;
 const rows=registryState.accounts.filter(a=>show||a.status==="active");
 $("registry-total").textContent=registryState.accounts.length;
 $("registry-active").textContent=registryState.accounts.filter(a=>a.status==="active").length;
 $("registry-linked").textContent=new Set(registryState.accounts.map(a=>a.risk_profile_id)).size;
 $("registry-count").textContent=registryState.accounts.length+" REGISTERED";
 $("registry-count").className=registryState.accounts.length?"pill ok":"pill off";
 box.replaceChildren();
 if(!rows.length){const empty=document.createElement("div");empty.className="placeholder";const inner=document.createElement("div");const strong=document.createElement("strong");strong.textContent="No registered accounts";const note=document.createElement("span");note.textContent="Create a matching risk profile, then register each evaluation or funded account separately.";inner.append(strong,note);empty.append(inner);box.append(empty);return;}
 rows.forEach(account=>{
  const card=document.createElement("article");card.className="tile";card.style.marginBottom="10px";
  const head=document.createElement("div");head.style.display="flex";head.style.justifyContent="space-between";head.style.gap="12px";head.style.flexWrap="wrap";
  const title=document.createElement("div");const name=document.createElement("b");name.textContent=account.nickname;name.style.fontSize="15px";const meta=document.createElement("div");meta.className="muted small";meta.textContent=account.firm+" · "+account.account_mode.toUpperCase()+" · "+registryMoney(account.account_size);title.append(name,meta);
  const badge=document.createElement("span");badge.className="pill "+(account.status==="active"?"ok":account.status==="completed"?"off":"warn");badge.textContent=account.status.toUpperCase();head.append(title,badge);card.append(head);
  const details=document.createElement("div");details.className="cards2";details.style.marginTop="10px";
  [["Current balance",registryMoney(account.current_balance)],["Linked profile",account.risk_profile_id],["Created",new Date(account.created_at).toLocaleString()],["Local events",String(account.event_count??0)]].forEach(([k,v])=>{const metric=document.createElement("div");metric.className="metric";const small=document.createElement("small");small.textContent=k;const b=document.createElement("b");b.textContent=v;metric.append(small,b);details.append(metric);});card.append(details);
  const actions=document.createElement("div");actions.className="toolbar";actions.style.marginTop="10px";
  const edit=document.createElement("button");edit.className="btn";edit.type="button";edit.textContent="Edit allowed fields";edit.addEventListener("click",()=>registryEditAccount(account));
  const history=document.createElement("button");history.className="btn";history.type="button";history.textContent="Open risk & history";history.addEventListener("click",()=>registryOpenRisk(account));
  const events=document.createElement("button");events.className="btn";events.type="button";events.textContent="View account events";events.addEventListener("click",()=>registryLoadEvents(account));
  actions.append(edit,history,events);card.append(actions);box.append(card);
 });
}
async function registryLoad(){
 const count=$("registry-count");count.textContent="LOADING";count.className="pill warn";
 try{
  const [ar,pr]=await Promise.all([fetch("/api/accounts?include_inactive=true"),fetch("/api/risk/profiles")]);
  const [ad,pd]=await Promise.all([ar.json(),pr.json()]);
  if(!ar.ok)throw new Error(ad.detail||"Account registry unavailable");
  if(!pr.ok)throw new Error(pd.detail||"Risk profiles unavailable");
  registryState.accounts=ad.accounts||[];registryState.profiles=pd.profiles||[];
  registryRenderAccounts();registryProfileOptions();
 }catch(error){count.textContent="UNAVAILABLE";count.className="pill warn";$("registry-account-list").textContent="Could not load registry: "+error.message;}
}
function registryOpenRisk(account){
 const nav=document.querySelector('[data-tab="risk"]');if(nav)nav.click();
 const profile=$("risk-profile-select"),history=$("history-profile-select");
 if(profile){profile.value=account.risk_profile_id;profile.dispatchEvent(new Event("change",{bubbles:true}));}
 if(history)history.value=account.risk_profile_id;
}
function registryEditAccount(account){
 const nickname=prompt("Account nickname:",account.nickname);if(nickname===null)return;
 const status=prompt("Status: active, inactive, or completed",account.status);if(status===null)return;
 const balanceText=prompt("Current balance (leave blank to clear):",account.current_balance??"");if(balanceText===null)return;
 const payload={nickname,status,current_balance:balanceText.trim()===""?null:Number(balanceText)};
 if(balanceText.trim()!==""&&(!Number.isFinite(payload.current_balance)||payload.current_balance<=0)){alert("Balance must be a positive number or blank.");return;}
 registryPatch(account.account_id,payload);
}
async function registryPatch(id,payload){
 try{const response=await fetch("/api/accounts/"+encodeURIComponent(id),{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({...payload,actor:"local_operator"})});const data=await response.json();if(!response.ok)throw new Error(data.detail||"Update rejected");$("registry-create-message").textContent="Account updated. Firm, mode, size and risk-profile linkage remain fixed.";await registryLoad();}
 catch(error){alert("Account not updated: "+error.message);}
}
async function registryLoadEvents(account){
 registryState.selected=account.account_id;$("registry-event-account").textContent=account.firm+" · "+account.nickname;
 const box=$("registry-events");box.textContent="Loading local events…";
 try{const response=await fetch("/api/accounts/"+encodeURIComponent(account.account_id)+"/events");const data=await response.json();if(!response.ok)throw new Error(data.detail||"Event history unavailable");box.replaceChildren();if(!(data.events||[]).length){box.textContent="No local events recorded.";return;}
 data.events.forEach(item=>{const row=document.createElement("div");row.className="status-row";const left=document.createElement("span");left.textContent=item.event_type+" · "+new Date(item.occurred_at).toLocaleString();const right=document.createElement("span");right.className="muted small";right.textContent=item.actor+" · "+item.details_json;row.append(left,right);box.append(row);});
 }catch(error){box.textContent="Could not load account events: "+error.message;}
}
$("registry-firm").addEventListener("change",registryProfileOptions);
$("registry-mode").addEventListener("change",registryProfileOptions);
$("registry-show-inactive").addEventListener("change",registryRenderAccounts);
$("registry-refresh").addEventListener("click",registryLoad);
$("registry-open-risk").addEventListener("click",()=>{const nav=document.querySelector('[data-tab="risk"]');if(nav)nav.click();});
$("registry-create-form").addEventListener("submit",async event=>{
 event.preventDefault();const msg=$("registry-create-message"),balance=$("registry-balance").value.trim();
 const payload={firm:$("registry-firm").value,nickname:$("registry-nickname").value.trim(),account_mode:$("registry-mode").value,status:$("registry-status").value,account_size:Number($("registry-size").value),risk_profile_id:$("registry-profile").value,actor:"local_operator"};
 if(balance!=="")payload.current_balance=Number(balance);
 msg.textContent="Validating firm, account type and unique risk-profile linkage…";
 try{const response=await fetch("/api/accounts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.detail||"Registration rejected");
 msg.textContent="Account registered. No provider requests, broker connections or orders were made.";
 $("registry-create-form").reset();$("registry-firm").value=payload.firm;$("registry-mode").value=payload.account_mode;await registryLoad();
 }catch(error){msg.textContent="Account not registered: "+error.message;}
});
registryLoad();
})();
</script>
'''
    if "</body>" in html and "function registryLoad()" not in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    return html
