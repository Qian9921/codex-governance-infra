"""Stdlib validators for v15 nested delegation packets/results."""
from __future__ import annotations
import re

class ContractError(ValueError): pass

REQUIRED_PACKET = {"schema","parent_task_id","child_task_id","assigned_model","role","max_depth","depth","permissions","forbidden_permissions","lease","retry_budget","active_mission_lock","plugin_inventory","result_schema"}
REQUIRED_RESULT = {"schema","parent_task_id","child_task_id","assigned_model","task_id","depth","changed_paths","counts","retry_used","contamination","status"}

def _id(value):
    return isinstance(value,str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]+",value))

def validate_packet(packet, parent_task_id=None):
    if not isinstance(packet,dict) or not REQUIRED_PACKET <= packet.keys(): raise ContractError("missing packet field")
    if packet["schema"] != "delegation.v1": raise ContractError("schema")
    if parent_task_id and packet["parent_task_id"] != parent_task_id: raise ContractError("parent mismatch")
    if not _id(packet["parent_task_id"]) or not _id(packet["child_task_id"]): raise ContractError("task identity")
    if packet["assigned_model"] not in {"gpt-5.6-luna","gpt-5.3-codex-spark"}: raise ContractError("model")
    if packet["max_depth"] != 1 or packet["depth"] != 1: raise ContractError("depth")
    if not packet["active_mission_lock"] or packet["plugin_inventory"] != "informational": raise ContractError("mission lock")
    if any(p in packet["permissions"] for p in ("git","github","review","merge")): raise ContractError("forbidden child permission")
    if not isinstance(packet["lease"],dict) or not packet["lease"].get("paths"): raise ContractError("lease")
    if packet["retry_budget"].get("semantic_contamination") != 1: raise ContractError("retry budget")
    return True

def validate_result(result, packet):
    if not isinstance(result,dict) or not REQUIRED_RESULT <= result.keys(): raise ContractError("missing result field")
    if result["schema"] != "delegation-result.v1": raise ContractError("result schema")
    for key in ("parent_task_id","child_task_id"): 
        if result[key] != packet[key]: raise ContractError("result identity")
    if result["assigned_model"] != packet["assigned_model"] or result["task_id"] != packet["child_task_id"]: raise ContractError("child model/task mismatch")
    if result["depth"] != packet["depth"]: raise ContractError("result depth")
    if result["retry_used"] not in (0,1): raise ContractError("retry overflow")
    c=result["counts"]
    if not isinstance(c,dict) or any(not isinstance(c.get(k),int) or c[k] < 0 for k in ("passed","failed","skipped")): raise ContractError("counts")
    if c.get("total") != c["passed"]+c["failed"]+c["skipped"] or c.get("ran") != c["passed"]+c["failed"]: raise ContractError("count arithmetic")
    if result["contamination"] not in (False,True): raise ContractError("contamination")
    lease=packet["lease"]["paths"]
    for path in result["changed_paths"]:
        if not any(path == p or path.startswith(p.rstrip("/")+"/") for p in lease): raise ContractError("changed path outside lease")
    if result["contamination"]: raise ContractError("contaminated result")
    return True
