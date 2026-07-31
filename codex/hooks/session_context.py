#!/usr/bin/env python3
import json, os, sys

def build_context(event=None, model=None):
    payload={"event":event or "SessionStart","policy":"v16","model":model or os.environ.get("CODEX_MODEL","unknown"),"spark_supported":True,"additionalContext":"ACTIVE-MISSION-LOCK: parent brief controls scope; Spark is unrestricted technically; plugin inventory informational."}
    payload["additionalContext"]=payload["additionalContext"][:1200]
    return payload
if __name__ == "__main__": print(json.dumps(build_context(),sort_keys=True))
