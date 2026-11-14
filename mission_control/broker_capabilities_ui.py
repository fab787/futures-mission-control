"""Account workspace controls for per-account broker/platform permission records."""

from __future__ import annotations


def build_broker_capabilities_dashboard(html: str) -> str:
    panel = r'''
<div class="panel" id="broker-capability-panel" style="margin-top:12px">
 <div class="panel-head">
  <div><div class="eyebrow">Connection eligibility · per account</div><div class="panel-title">Broker &amp; platform permissions</div></div>
  <div class="toolbar"><span class="pill warn" id="broker-capability-status">PERMISSION UNVERIFIED BY DEFAULT</span><button class="btn" id="broker-capability-refresh" type="button">Refresh</button></div>
 </div>
 <p class="muted small">Record the actual provider and platform for each account. Only mark permission confirmed when you have current official terms or written firm/platform support evidence. This registry does not connect or execute trades.</p>
 <div id="broker-capability-list"><div class="placeholder"><div><strong>Loading broker capabilities</strong>New and unknown accounts remain unverified.</div></div></div>
 <div class="status-row"><span class="muted">API credentials / broker connection</span><b>Not requested · Not connected</b></div>
 <div class="status-row"><span class="muted">Execution</span><b>Disabled · 0 orders</b></div>
</div>
'''
    marker = '<div class="panel" style="margin-top:12px">\n  <div class="panel-head"><div><div class="eyebrow">Local account audit</div>'
    if marker in html and 'id="broker-capability-panel"' not in html:
        html = html.replace(marker, panel + "\n" + marker, 1)
    script = r'''
<script>
(function(){
"use strict";
const root=document.getElementById("broker-capability-panel");
if(!root||root.dataset.initialized==="1")return;
root.dataset.initialized="1";
const list=document.getElementById("broker-capability-list");
const status=document.getElementById("broker-capability-status");
const refresh=document.getElementById("broker-capability-refresh");
const providers=["unknown","tradovate","cqg","rithmic","other"];
const platforms=["unknown","tradovate","tradingview","ninjatrader","other"];
const methods=["unknown","native_platform","official_api","local_desktop_adapter","manual_only"];
const permissions=["unverified","confirmed","prohibited"];
const esc=value=>String(value??"");
function selectField(label,id,values,current){
 const wrap=document.createElement("label");wrap.className="small";wrap.textContent=label;
 const select=document.createElement("select");select.className="control";select.id=id;select.style.display="block";select.style.width="100%";select.style.marginTop="4px";
 values.forEach(value=>select.add(new Option(value.replaceAll("_"," "),value)));
 select.value=values.includes(current)?current:values[0];wrap.append(select);return wrap;
}
function inputField(label,id,value,placeholder){
 const wrap=document.createElement("label");wrap.className="small";wrap.textContent=label;
 const input=document.createElement("input");input.className="control";input.id=id;input.value=esc(value);input.maxLength=label==="Evidence reference"?1000:2000;input.placeholder=placeholder||"";input.style.display="block";input.style.width="100%";input.style.marginTop="4px";wrap.append(input);return wrap;
}
function render(records){
 list.replaceChildren();
 if(!records.length){const empty=document.createElement("div");empty.className="placeholder";empty.textContent="Register a prop-firm account first.";list.append(empty);return;}
 records.forEach((item,index)=>{
  const card=document.createElement("div");card.className="tile";card.style.marginBottom="10px";
  const heading=document.createElement("div");heading.style.display="flex";heading.style.justifyContent="space-between";heading.style.flexWrap="wrap";heading.style.gap="8px";
  const name=document.createElement("b");name.textContent=item.firm+" · "+item.nickname+" · "+item.account_mode;
  const badge=document.createElement("span");badge.className="pill "+(item.permission_status==="confirmed"?"ok":item.permission_status==="prohibited"?"warn":"off");badge.textContent=item.permission_status.toUpperCase();
  heading.append(name,badge);card.append(heading);
  const grid=document.createElement("div");grid.className="cards2";grid.style.marginTop="10px";
  const prefix="broker-cap-"+index+"-";
  const provider=selectField("Connection provider",prefix+"provider",providers,item.connection_provider);
  const platform=selectField("Trading platform",prefix+"platform",platforms,item.platform);
  const method=selectField("Integration method",prefix+"method",methods,item.integration_method);
  const permission=selectField("Automation permission",prefix+"permission",permissions,item.permission_status);
  const evidence=inputField("Evidence reference",prefix+"evidence",item.evidence_reference,"Official policy URL or written support reference");
  const notes=inputField("Notes",prefix+"notes",item.notes,"Account-specific restrictions or support response");
  grid.append(provider,platform,method,permission,evidence,notes);card.append(grid);
  const footer=document.createElement("div");footer.className="toolbar";footer.style.marginTop="10px";
  const save=document.createElement("button");save.className="btn";save.type="button";save.textContent="Save account capability";
  const detail=document.createElement("span");detail.className="muted small";detail.textContent=item.updated_at?"Last updated "+new Date(item.updated_at).toLocaleString():"No capability recorded yet";
  save.addEventListener("click",async()=>{
   save.disabled=true;detail.textContent="Saving…";
   const payload={
    connection_provider:document.getElementById(prefix+"provider").value,
    platform:document.getElementById(prefix+"platform").value,
    integration_method:document.getElementById(prefix+"method").value,
    permission_status:document.getElementById(prefix+"permission").value,
    evidence_reference:document.getElementById(prefix+"evidence").value.trim(),
    notes:document.getElementById(prefix+"notes").value.trim()
   };
   try{
    const response=await fetch("/api/accounts/"+encodeURIComponent(item.account_id)+"/broker-capability",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Save rejected");
    detail.textContent="Saved · execution remains disabled";await load();
   }catch(error){detail.textContent="Not saved: "+error.message;save.disabled=false;}
  });
  footer.append(save,detail);card.append(footer);list.append(card);
 });
}
async function load(){
 refresh.disabled=true;status.textContent="LOADING";
 try{
  const response=await fetch("/api/broker-capabilities");const data=await response.json();
  if(!response.ok)throw new Error(data.detail||"Capability registry unavailable");
  render(data.capabilities||[]);
  const total=(data.capabilities||[]).length;
  const verified=(data.capabilities||[]).filter(x=>x.permission_status==="confirmed").length;
  status.textContent=verified+" OF "+total+" PERMISSIONS MARKED CONFIRMED";
  status.className=verified?"pill warn":"pill off";
 }catch(error){status.textContent="UNAVAILABLE";status.className="pill warn";list.textContent="Could not load broker capability registry: "+error.message;}
 finally{refresh.disabled=false;}
}
refresh.addEventListener("click",load);load();
})();
</script>
'''
    if "</body>" in html and "broker-capability-refresh" not in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    return html
