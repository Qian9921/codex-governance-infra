#!/usr/bin/env python3
"""Conservative policy checker; collaboration spawn is parent-validated, not intercepted here."""
import json,sys
FORBIDDEN_CHILD={"git","github","merge","review","approve"}
def decide(tool,args=None):
    if tool in FORBIDDEN_CHILD: return {"decision":"deny","reason":"child action requires parent authorization"}
    return {"decision":"allow","reason":"policy-pass"}
if __name__ == "__main__":
    x=json.load(sys.stdin); print(json.dumps(decide(x.get("tool_name",""),x.get("args")),sort_keys=True))
