"""Versioned fail-closed risk profiles; no broker or market-data access."""
from __future__ import annotations
import json, math, os, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

DB=Path(os.environ.get("FAB787_AUDIT_DB",Path(__file__).resolve().parent.parent/"data"/"fab787_quantum_audit.sqlite3"))
FIELDS=("max_daily_loss","max_session_loss","max_drawdown","max_contracts","max_per_trade_risk")
OPTIONAL=("profit_target","profit_protection_activation","profit_protection_floor","max_daily_profit_share")
SESSIONS={"NY","GLOBEX"}

def _db(path=None):
 p=Path(path) if path is not None else DB; p.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(p,timeout=10); c.row_factory=sqlite3.Row
 c.executescript("""
 CREATE TABLE IF NOT EXISTS risk_profiles(profile_id TEXT,version INTEGER,created_at TEXT,actor TEXT,profile_json TEXT,PRIMARY KEY(profile_id,version));
 CREATE TABLE IF NOT EXISTS risk_config_events(event_id INTEGER PRIMARY KEY,profile_id TEXT,version INTEGER,actor TEXT,occurred_at TEXT,event_type TEXT,details_json TEXT);
 CREATE TRIGGER IF NOT EXISTS risk_profiles_no_update BEFORE UPDATE ON risk_profiles BEGIN SELECT RAISE(ABORT,'risk profile versions are immutable'); END;
 CREATE TRIGGER IF NOT EXISTS risk_profiles_no_delete BEFORE DELETE ON risk_profiles BEGIN SELECT RAISE(ABORT,'risk profile versions are immutable'); END;
 CREATE TRIGGER IF NOT EXISTS risk_config_events_no_update BEFORE UPDATE ON risk_config_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
 CREATE TRIGGER IF NOT EXISTS risk_config_events_no_delete BEFORE DELETE ON risk_config_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
 """); return c

def _num(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v))
def _actor(v):
 if not isinstance(v,str) or not v.strip() or len(v.strip())>120: raise ValueError("A non-empty actor label (maximum 120 characters) is required.")
 return v.strip()

def _limits(data,label,required):
 if not isinstance(data,dict): raise ValueError(f"{label} must be an object.")
 out={}
 for k in FIELDS:
  if k not in data:
   if required: raise ValueError(f"{label}.{k} is required; no firm limits are assumed.")
   continue
  v=data[k]
  if not _num(v) or v<=0 or (k=="max_contracts" and not float(v).is_integer()): raise ValueError(f"{label}.{k} must be positive and finite; max_contracts must be a whole number.")
  out[k]=int(v) if k=="max_contracts" else float(v)
 if "allowed_sessions" in data:
  s=data["allowed_sessions"]
  if not isinstance(s,list) or not s or any(not isinstance(x,str) or x not in SESSIONS for x in s): raise ValueError(f"{label}.allowed_sessions must contain NY and/or GLOBEX.")
  out["allowed_sessions"]=sorted(set(s))
 elif required: raise ValueError(f"{label}.allowed_sessions is required.")
 for k in OPTIONAL:
  if k in data:
   v=data[k]
   if not _num(v) or v<0: raise ValueError(f"{label}.{k} must be non-negative and finite.")
   out[k]=float(v)
 if ("profit_protection_activation" in out)!=("profit_protection_floor" in out): raise ValueError(f"{label} must include both profit-protection activation and floor.")
 if out.get("profit_protection_floor",0)>out.get("profit_protection_activation",float("inf")): raise ValueError("Profit-protection floor cannot exceed activation.")
 if out.get("max_daily_profit_share",0)>100: raise ValueError("max_daily_profit_share must be at most 100.")
 return out

def _combine(p,u):
 e={k:min(p[k],u.get(k,p[k])) for k in FIELDS}
 e["allowed_sessions"]=sorted(set(p["allowed_sessions"])&set(u.get("allowed_sessions",p["allowed_sessions"])))
 if not e["allowed_sessions"]: raise ValueError("User sessions do not intersect prop-firm permitted sessions.")
 for k in ("profit_target","profit_protection_activation","max_daily_profit_share"):
  vals=[x[k] for x in (p,u) if k in x]
  if vals: e[k]=min(vals)
 if "profit_protection_floor" in p or "profit_protection_floor" in u:
  e["profit_protection_floor"]=max(x["profit_protection_floor"] for x in (p,u) if "profit_protection_floor" in x)
  if e.get("profit_protection_activation") is None or e["profit_protection_floor"]>e["profit_protection_activation"]: raise ValueError("Effective profit-protection floor/activation is invalid.")
 return e

def create_profile(payload,actor,db_path=None):
 actor=_actor(actor)
 if not isinstance(payload,dict): raise ValueError("A structured profile is required.")
 name,firm=payload.get("profile_name"),payload.get("prop_firm")
 if not isinstance(name,str) or not name.strip() or len(name)>100: raise ValueError("profile_name is required (maximum 100 characters).")
 if not isinstance(firm,str) or not firm.strip() or len(firm)>100: raise ValueError("prop_firm is required (maximum 100 characters).")
 mode,draw=payload.get("account_mode"),payload.get("drawdown_type")
 if mode not in {"evaluation","funded"}: raise ValueError("account_mode must be evaluation or funded.")
 if draw not in {"trailing","end_of_day","static"}: raise ValueError("drawdown_type must be trailing, end_of_day, or static.")
 verified=payload.get("verification_status","unverified")
 if verified not in {"unverified","user_verified"}: raise ValueError("verification_status must be unverified or user_verified.")
 p=_limits(payload.get("prop_firm_maximums"),"prop_firm_maximums",True); u=_limits(payload.get("user_limits",{}),"user_limits",False)
 rule=payload.get("consistency_rule")
 if rule is not None and not isinstance(rule,dict): raise ValueError("consistency_rule must be an object or null.")
 profile={"profile_name":name.strip(),"prop_firm":firm.strip(),"account_mode":mode,"drawdown_type":draw,"verification_status":verified,"prop_firm_maximums":p,"user_limits":u,"effective_limits":_combine(p,u),"consistency_rule":rule}
 pid=str(payload.get("profile_id") or uuid.uuid4())
 if len(pid)>80: raise ValueError("profile_id is invalid.")
 now=datetime.now(timezone.utc).isoformat()
 with _db(db_path) as c:
  row=c.execute("SELECT MAX(version) v FROM risk_profiles WHERE profile_id=?",(pid,)).fetchone(); ver=int(row["v"] or 0)+1
  c.execute("INSERT INTO risk_profiles VALUES(?,?,?,?,?)",(pid,ver,now,actor,json.dumps(profile,sort_keys=True)))
  c.execute("INSERT INTO risk_config_events(profile_id,version,actor,occurred_at,event_type,details_json) VALUES(?,?,?,?,?,?)",(pid,ver,actor,now,"profile_version_created",json.dumps({"profile_name":name.strip(),"verification_status":verified})))
 return {"profile_id":pid,"version":ver,"profile":profile,"configuration_changed":True,"provider_requests_triggered":0,"orders_created":0,"execution":"disabled"}

def get_profile(pid,db_path=None):
 if not isinstance(pid,str) or not pid.strip(): raise ValueError("risk_profile_id must be a non-empty string.")
 with _db(db_path) as c: r=c.execute("SELECT * FROM risk_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1",(pid,)).fetchone()
 return None if r is None else {"profile_id":r["profile_id"],"version":r["version"],"created_at":r["created_at"],"actor":r["actor"],"profile":json.loads(r["profile_json"])}

def list_profiles(db_path=None):
 with _db(db_path) as c:
  rows=c.execute("SELECT p.* FROM risk_profiles p JOIN (SELECT profile_id,MAX(version) version FROM risk_profiles GROUP BY profile_id) x ON p.profile_id=x.profile_id AND p.version=x.version ORDER BY p.created_at DESC").fetchall()
 return {"profiles":[{"profile_id":r["profile_id"],"version":r["version"],"created_at":r["created_at"],"actor":r["actor"],"profile":json.loads(r["profile_json"])} for r in rows],"provider_requests_triggered":0,"execution":"disabled"}

def resolve_effective_profile(pid,db_path=None):
 item=get_profile(pid,db_path)
 if item is None: raise ValueError("Configured risk profile was not found.")
 p=item["profile"]
 if p["verification_status"]!="user_verified": raise ValueError("Risk profile is unverified; verify the official firm maximums before risk review.")
 return {"profile_id":item["profile_id"],"version":item["version"],"effective_limits":p["effective_limits"],"profile_name":p["profile_name"],"prop_firm":p["prop_firm"],"account_mode":p["account_mode"],"drawdown_type":p["drawdown_type"]}

def list_profile_versions(profile_id,db_path=None):
    if not isinstance(profile_id,str) or not profile_id.strip():
        raise ValueError("profile_id must be a non-empty string.")
    with _db(db_path) as c:
        rows=c.execute("SELECT * FROM risk_profiles WHERE profile_id=? ORDER BY version",(profile_id,)).fetchall()
        events=c.execute("SELECT * FROM risk_config_events WHERE profile_id=? ORDER BY event_id",(profile_id,)).fetchall()
    return {"profile_id":profile_id,
            "versions":[{"version":r["version"],"created_at":r["created_at"],"actor":r["actor"],"profile":json.loads(r["profile_json"])} for r in rows],
            "events":[{"event_id":e["event_id"],"version":e["version"],"actor":e["actor"],"occurred_at":e["occurred_at"],"event_type":e["event_type"],"details":json.loads(e["details_json"])} for e in events],
            "provider_requests_triggered":0,"execution":"disabled"}
