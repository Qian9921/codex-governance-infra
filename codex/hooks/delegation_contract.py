"""Stdlib validators for v15 nested delegation packets/results."""
from __future__ import annotations
import re
from collections.abc import Mapping

class ContractError(ValueError): pass

REQUIRED_PACKET = {"schema","parent_task_id","child_task_id","assigned_model","role","max_depth","depth","permissions","forbidden_permissions","lease","retry_budget","active_mission_lock","plugin_inventory","result_schema"}
REQUIRED_RESULT = {"schema","parent_task_id","child_task_id","assigned_model","task_id","depth","changed_paths","counts","retry_used","contamination","status"}

def _id(value):
    return isinstance(value,str) and bool(re.fullmatch(r"[A-Za-z0-9_.:/-]+",value))


_MODEL_FAMILY_ALIASES = {
    "sol": "sol", "luna": "luna", "terra": "terra", "spark": "spark",
}
_NAME_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def model_family(model):
    """Return the portable family token used in task names and telemetry."""

    if not isinstance(model, str):
        return "unknown"
    lowered = model.lower()
    families = {
        token for token in _MODEL_FAMILY_ALIASES
        if re.search(r"(?:^|[-_.:/])" + token + r"(?:$|[-_.:/])", lowered)
    }
    if len(families) > 1:
        return "ambiguous"
    if families:
        return _MODEL_FAMILY_ALIASES[next(iter(families))]
    return "unknown"


def _name_tokens(value):
    return {token.lower() for token in _NAME_TOKEN.findall(value or "")}


def validate_task_identity(
    task_name, *, requested_model=None, actual_model=None, role=None,
    fallback_reason=None,
):
    """Validate identity-bearing task names without blocking unknown metadata.

    Existing callers may omit identity fields in adaptive mode.  A supplied
    name is rejected only when it demonstrably claims a different known model
    family (the deliberate-misrepresentation case); otherwise the naming
    signal remains advisory and can be reported for follow-up.
    """

    task_name_missing = not isinstance(task_name, str) or not task_name
    tokens = _name_tokens(task_name) if not task_name_missing else set()
    requested = model_family(requested_model)
    actual = model_family(actual_model if actual_model is not None else requested_model)
    role_token = role.lower() if isinstance(role, str) else "unknown"
    # A known actual family must be visible whenever a name carries any known
    # family marker.  This catches luna-prefixed Sol/Terra fallbacks while
    # allowing legacy/unknown labels to continue in advisory mode.
    known = {"sol", "luna", "terra", "spark"}
    named_families = tokens & known
    if len(named_families) > 1:
        raise ContractError("task name model family is ambiguous")
    if requested == "ambiguous" or actual == "ambiguous":
        raise ContractError("model identity family is ambiguous")
    if actual in known and named_families and actual not in named_families:
        raise ContractError("task name misrepresents actual model family")
    if requested in known and actual in known and requested != actual:
        if not isinstance(fallback_reason, str) or not fallback_reason.strip():
            raise ContractError("fallback reason required when model family changes")
        if requested in named_families:
            raise ContractError("fallback task name retains requested model family")
    if task_name_missing:
        return {
            "status": "missing",
            "advisory": True,
            "requested_model_family": requested,
            "actual_model_family": actual,
        }
    if fallback_reason and actual in known and actual not in tokens:
        return {"status": "advisory", "advisory": True, "missing": [actual]}
    missing = []
    if actual in known and actual not in tokens:
        missing.append(actual)
    role_tokens = set(_name_tokens(role_token)) if role_token not in {"unknown", ""} else set()
    if role_tokens and not role_tokens.issubset(tokens):
        missing.extend(sorted(role_tokens - tokens))
    return {
        "status": "ok" if not missing else "advisory",
        "advisory": bool(missing),
        "missing": missing,
        "requested_model_family": requested,
        "actual_model_family": actual,
    }


def validate_identity_fields(value: Mapping, *, task_key="task_name"):
    """Apply identity naming checks to an optional packet/result mapping."""

    if not isinstance(value, Mapping) or task_key not in value:
        return {"status": "missing", "advisory": True}
    return validate_task_identity(
        value.get(task_key),
        requested_model=value.get("requested_model", value.get("assigned_model")),
        actual_model=value.get("actual_model", value.get("assigned_model")),
        role=value.get("role"),
        fallback_reason=value.get("fallback_reason"),
    )

def validate_packet(packet, parent_task_id=None):
    if not isinstance(packet,dict) or not REQUIRED_PACKET <= packet.keys(): raise ContractError("missing packet field")
    if packet["schema"] != "delegation.v1": raise ContractError("schema")
    if parent_task_id and packet["parent_task_id"] != parent_task_id: raise ContractError("parent mismatch")
    if not _id(packet["parent_task_id"]) or not _id(packet["child_task_id"]): raise ContractError("task identity")
    if not _id(packet["assigned_model"]): raise ContractError("model")
    validate_identity_fields(packet)
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
    validate_identity_fields(result)
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
