import pytest
from mission_control.risk_config import create_profile,get_profile,list_profiles,resolve_effective_profile

def sample():
 return {"profile_name":"Example 50K","prop_firm":"User supplied","account_mode":"evaluation","drawdown_type":"end_of_day","verification_status":"user_verified","prop_firm_maximums":{"max_daily_loss":500,"max_session_loss":300,"max_drawdown":2000,"max_contracts":10,"max_per_trade_risk":200,"allowed_sessions":["NY","GLOBEX"]},"user_limits":{"max_daily_loss":250,"max_contracts":3,"max_per_trade_risk":100,"allowed_sessions":["NY"]}}

def test_strictest_limits_and_versions(tmp_path):
 db=tmp_path/"risk.db"; a=create_profile(sample(),"operator",db); e=resolve_effective_profile(a["profile_id"],db)["effective_limits"]
 assert e["max_daily_loss"]==250 and e["max_session_loss"]==300 and e["max_contracts"]==3 and e["max_per_trade_risk"]==100 and e["allowed_sessions"]==["NY"]
 p=sample(); p["profile_id"]=a["profile_id"]; p["user_limits"]["max_daily_loss"]=200
 b=create_profile(p,"operator",db); assert b["version"]==2 and get_profile(a["profile_id"],db)["version"]==2 and len(list_profiles(db)["profiles"])==1

def test_missing_prop_limit_rejected(tmp_path):
 p=sample(); del p["prop_firm_maximums"]["max_drawdown"]
 with pytest.raises(ValueError): create_profile(p,"operator",tmp_path/"risk.db")

def test_session_intersection_rejected(tmp_path):
 p=sample(); p["prop_firm_maximums"]["allowed_sessions"]=["NY"]; p["user_limits"]["allowed_sessions"]=["GLOBEX"]
 with pytest.raises(ValueError): create_profile(p,"operator",tmp_path/"risk.db")

def test_unverified_profile_blocked(tmp_path):
 p=sample(); p["verification_status"]="unverified"; a=create_profile(p,"operator",tmp_path/"risk.db")
 with pytest.raises(ValueError): resolve_effective_profile(a["profile_id"],tmp_path/"risk.db")

def test_no_provider_or_order_actions(tmp_path):
 a=create_profile(sample(),"operator",tmp_path/"risk.db")
 assert a["provider_requests_triggered"]==0 and a["orders_created"]==0 and a["execution"]=="disabled"
