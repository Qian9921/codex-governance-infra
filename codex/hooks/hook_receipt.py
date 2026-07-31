#!/usr/bin/env python3
"""Privacy-safe best-effort receipt helper; never stores raw args, cwd, tokens, or prompts."""
import hashlib,json,os,time

def safe_hash(value): return hashlib.sha256(str(value).encode()).hexdigest() if value is not None else None
def receipt(event,model,tool=None,decision=None,reason=None,snapshot_sha256=None):
    return {"schema":"v15-receipt-1","utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"event":event,"model":model,"tool_name":tool,"decision":decision,"reason":reason,"snapshot_sha256":snapshot_sha256,"identifiers_sha256":safe_hash(os.environ.get("CODEX_TASK_ID",""))}
if __name__ == "__main__": print(json.dumps(receipt("manual","unknown"),sort_keys=True))
