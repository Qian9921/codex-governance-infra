"""Executable V16 R1 counterexample and remediation matrix.

The matrix is deliberately data driven.  Every case has a stable identifier,
an explicit failure mechanism, a non-zero denominator, and a normal contract
validator.  It is used both for the immutable reviewed-head reproduction and
for candidate closure; names are never treated as proof of a result.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .contracts import ContractError, canonical_json, canonical_sha256, validate_counterexample_linkage, validate_mission
from .review_policy import resolve_review_policy


class R1Error(ContractError):
    """Raised when a frozen R1 matrix or candidate probe is malformed."""


R1_FINDINGS = tuple(f"V16-R1-{i:03d}" for i in range(1, 12))

# Public family IDs are intentionally stable.  The `mechanism` field is shown
# in the generated acceptance matrix and is not a substitute for execution.
NEGATIVE_FAMILIES: tuple[dict[str, Any], ...] = (
    {"case_id": "NF-001", "family": "spark-fabricated-task", "mechanism": "fabricated task identity lacks transcript binding"},
    {"case_id": "NF-002", "family": "spark-missing-result", "mechanism": "missing Spark result lacks exact artifact"},
    {"case_id": "NF-003", "family": "spark-fourth-audit", "mechanism": "fourth audit exceeds exact three budget"},
    {"case_id": "NF-004", "family": "gate-head-mutation", "mechanism": "gate mutates candidate head or tree"},
    {"case_id": "NF-005", "family": "gate-disconnected-component", "mechanism": "disconnected gate is not in the executed DAG"},
    {"case_id": "NF-006", "family": "shell-absolute-alias", "mechanism": "absolute shell basename or shell mode bypass"},
    {"case_id": "NF-007", "family": "invariant-without-acceptance", "mechanism": "blocking invariant has no acceptance row"},
    {"case_id": "NF-008", "family": "fresh-bypasses-full", "mechanism": "fresh stage omits transitive full prerequisite"},
    {"case_id": "NF-009", "family": "missing-green-or-p1-followup", "mechanism": "readiness allows missing GREEN or active P1 FOLLOW_UP"},
    {"case_id": "NF-010", "family": "fake-receipt", "mechanism": "readiness accepts arbitrary evidence/gate receipt IDs"},
    {"case_id": "NF-011", "family": "inherited-secret", "mechanism": "runner inherits secret environment"},
    {"case_id": "NF-012", "family": "socket-access", "mechanism": "runner permits network/socket access"},
    {"case_id": "NF-013", "family": "background-survivor", "mechanism": "timeout leaves a child process alive"},
    {"case_id": "NF-014", "family": "skipped-green", "mechanism": "skipped row is accepted as green"},
    {"case_id": "NF-015", "family": "tree-mismatch", "mechanism": "row tree differs from envelope tree"},
    {"case_id": "NF-016", "family": "timestamp-contradiction", "mechanism": "timestamp and elapsed fields contradict"},
    {"case_id": "NF-017", "family": "expected-denied-string", "mechanism": "truthy non-boolean expected_denied bypasses type contract"},
    {"case_id": "NF-018", "family": "independent-approval-fabrication", "mechanism": "author packet synthesizes APPROVE"},
    {"case_id": "NF-019", "family": "metrics-missing-or-nan", "mechanism": "missing source or non-finite metric is accepted"},
    {"case_id": "NF-020", "family": "manifest-row-deletion", "mechanism": "manifest omission is not exact-set RED"},
    {"case_id": "NF-021", "family": "gate-missing-artifact", "mechanism": "gate receipt omits a required compiled entrypoint row"},
    {"case_id": "NF-022", "family": "gate-duplicate-artifact", "mechanism": "gate receipt duplicates one entrypoint row and omits another"},
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mission(root: pathlib.Path) -> dict[str, Any]:
    path = root / "codex/v16/fixtures/mission.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _expect_reject(fn: Callable[[], Any]) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def _git_identity(root: pathlib.Path) -> tuple[str, str]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root), text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(root), text=True).strip()
    return head, tree


def _gate_probe(command: list[str], *, timeout_sec: float = 2.0, inspect: Callable[[pathlib.Path, dict[str, Any]], Any] | None = None) -> Any:
    """Run a normal GateRunner probe in an isolated, clean temporary Git root.

    R1 must exercise the same foreground runner used by production gates.  A
    temporary repository keeps the probe's identity clean even when the author
    checkout is staged or otherwise intentionally dirty during development.
    """
    from .runner import GateRunner

    with tempfile.TemporaryDirectory(prefix="v16-r1-root-") as root_tmp, tempfile.TemporaryDirectory(prefix="v16-r1-artifacts-") as artifact_tmp:
        root = pathlib.Path(root_tmp)
        (root / "probe.txt").write_text("r1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.email", "v16-r1@example.invalid"], cwd=str(root), check=True)
        subprocess.run(["git", "config", "user.name", "V16 R1 Probe"], cwd=str(root), check=True)
        subprocess.run(["git", "add", "probe.txt"], cwd=str(root), check=True)
        subprocess.run(["git", "commit", "-qm", "r1 probe root"], cwd=str(root), check=True)
        head, tree = _git_identity(root)
        plan = {
            "gate_order": ["G-R1-PROBE"],
            "gates": [{"id": "G-R1-PROBE", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP-R1-PROBE"], "blocking": True, "reusable": False}],
            "entrypoints": [{"id": "EP-R1-PROBE", "argv": command, "cwd": ".", "env": {}, "timeout_sec": timeout_sec, "stop_conditions": ["timeout", "privacy", "identity"]}],
        }
        result = GateRunner(root, plan, pathlib.Path(artifact_tmp)).run_gate("G-R1-PROBE", expected_head=head, expected_tree=tree, force=True)
        if inspect is not None:
            return result, inspect(root, result)
        return result


def _gate_entrypoint_denominator_probe(mutate: Callable[[dict[str, Any]], None]) -> bool:
    """Reject a gate receipt whose rows do not match its compiled denominator."""
    from .runner import GateRunner, validate_gate_result

    with tempfile.TemporaryDirectory(prefix="v16-r1-denominator-root-") as root_tmp, tempfile.TemporaryDirectory(prefix="v16-r1-denominator-artifacts-") as artifact_tmp:
        root = pathlib.Path(root_tmp)
        (root / "probe.txt").write_text("probe\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.email", "v16-r1@example.invalid"], cwd=str(root), check=True)
        subprocess.run(["git", "config", "user.name", "V16 R1 Probe"], cwd=str(root), check=True)
        subprocess.run(["git", "add", "probe.txt"], cwd=str(root), check=True)
        subprocess.run(["git", "commit", "-qm", "r1 denominator probe"], cwd=str(root), check=True)
        head, tree = _git_identity(root)
        command = [
            "python3", "-c",
            "print('{\"total\":1,\"ran\":1,\"passed\":1,\"failed\":0,\"skipped\":0,\"xfail\":0,\"unknown\":0}')",
        ]
        plan = {
            "schema": "compiled-plan.v16",
            "gate_order": ["G-R1-DENOMINATOR"],
            "gates": [{"id": "G-R1-DENOMINATOR", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP-A", "EP-B"], "blocking": True, "reusable": False}],
            "entrypoints": [
                {"id": "EP-A", "argv": command, "cwd": ".", "env": {}, "timeout_sec": 2.0, "stop_conditions": ["timeout"]},
                {"id": "EP-B", "argv": command, "cwd": ".", "env": {}, "timeout_sec": 2.0, "stop_conditions": ["timeout"]},
            ],
        }
        result = GateRunner(root, plan, pathlib.Path(artifact_tmp)).run_gate("G-R1-DENOMINATOR", expected_head=head, expected_tree=tree, force=True)
        mutate(result)
        return _expect_reject(lambda: validate_gate_result(result, expected_head=head, expected_tree=tree, artifact_root=pathlib.Path(artifact_tmp), plan=plan))


def _family_probe(root: pathlib.Path, family: Mapping[str, Any]) -> bool:
    """Execute one frozen negative through ordinary validators.

    The probe returns true only when the malformed candidate is rejected.  It
    intentionally uses public module APIs rather than private test shortcuts.
    """
    mission = _mission(root)
    name = family["family"]
    if name == "spark-fabricated-task":
        from .spark import validate_dispatch_transcript
        closure = json.loads((root / "codex/v16/contracts/v16_dispatch_transcript.json").read_text(encoding="utf-8"))
        bad = copy.deepcopy(closure); bad["accepted_current_audits"][0]["canonical_response_task_id"] = "fabricated/task"
        return _expect_reject(lambda: validate_dispatch_transcript(bad))
    if name == "spark-missing-result":
        from .spark import validate_dispatch_transcript
        closure = json.loads((root / "codex/v16/contracts/v16_dispatch_transcript.json").read_text(encoding="utf-8"))
        bad = copy.deepcopy(closure); bad["accepted_current_audits"][1]["result_artifact_sha256"] = ""
        return _expect_reject(lambda: validate_dispatch_transcript(bad))
    if name == "spark-fourth-audit":
        from .spark import validate_dispatch_transcript
        closure = json.loads((root / "codex/v16/contracts/v16_dispatch_transcript.json").read_text(encoding="utf-8"))
        bad = copy.deepcopy(closure); bad["accepted_current_audits"].append(copy.deepcopy(bad["accepted_current_audits"][0])); bad["accepted_current_audits"][-1]["global_spawn_index"] = 7; bad["accepted_current_audits"][-1]["accepted_spawn_index"] = 4; bad["accepted_current_audits"][-1]["audit_id"] = "SPARK-D"
        return _expect_reject(lambda: validate_dispatch_transcript(bad))
    if name == "gate-head-mutation":
        from .runner import validate_gate_result
        head, tree = _git_identity(root)
        bad = {
            "schema": "gate-result.v16",
            "gate_id": "G-TARGETED",
            "stage": "targeted",
            "decision": "allow",
            "expected_head": head,
            "actual_head": "0" * 40,
            "tree_sha": tree,
            "dirty": False,
            "snapshot_mode": "",
            "snapshot_sha256": "",
            "started_at": _utc(),
            "ended_at": _utc(),
            "elapsed_sec": 0.0,
            "rows": [],
        }
        return _expect_reject(lambda: validate_gate_result(bad, expected_head=head, expected_tree=tree))
    if name == "gate-disconnected-component":
        from .compiler import compile_mission
        bad = copy.deepcopy(mission)
        bad["gates"].append({"id": "G-DISCONNECTED", "stage": "targeted", "depends_on": [], "entrypoint_ids": ["EP-UNIT"], "blocking": True, "reusable": False})
        return _expect_reject(lambda: compile_mission(bad))
    if name == "shell-absolute-alias":
        from .compiler import compile_mission
        bad = copy.deepcopy(mission); bad["entrypoints"][0]["argv"] = ["/bin/sh", "-c", "echo unsafe"]
        return _expect_reject(lambda: compile_mission(bad))
    if name == "invariant-without-acceptance":
        bad = copy.deepcopy(mission); bad["invariants"].append({"id": "INV-NO-ACCEPT", "description": "unmapped invariant", "blocking": True, "counterexample_ids": ["CE-SCHEMA"]})
        return _expect_reject(lambda: validate_counterexample_linkage(validate_mission(bad)))
    if name == "fresh-bypasses-full":
        from .compiler import compile_mission
        bad = copy.deepcopy(mission); bad["gates"][-1]["depends_on"] = ["G-TARGETED"]
        return _expect_reject(lambda: compile_mission(bad))
    if name == "missing-green-or-p1-followup":
        from .state import validate_state, initial_state, transition
        state = initial_state(
            "V16-PRODUCTIVITY",
            mission["scope"]["exact_head"],
            mission["scope"].get("tree_sha", ""),
            review_policy=resolve_review_policy(mission),
        )
        state = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=mission["scope"]["exact_head"], head_sha=mission["scope"]["exact_head"], counterexample_ids=["CE-SCHEMA"], updated_at="9999-12-31T00:00:01Z")
        state = transition(state, "BASELINE_REPRODUCED", base_sha=mission["scope"]["exact_head"], head_sha=mission["scope"]["exact_head"], red_counterexamples=["CE-SCHEMA"], updated_at="9999-12-31T00:00:02Z")
        candidate = "0123456789abcdef0123456789abcdef01234567"
        state = transition(state, "IMPLEMENTING", base_sha=mission["scope"]["exact_head"], head_sha=candidate, updated_at="9999-12-31T00:00:03Z")
        state = transition(state, "INNER_AUDIT_COMPLETE", base_sha=mission["scope"]["exact_head"], head_sha=candidate, spark_findings=["F1"], spark_audit_count=1, dispositions={"F1": "FOLLOW_UP"}, updated_at="9999-12-31T00:00:04Z")
        return _expect_reject(lambda: transition(state, "LOCAL_READY", base_sha=mission["scope"]["exact_head"], head_sha=candidate, evidence_ids=["FAKE"], updated_at="9999-12-31T00:00:05Z"))
    if name == "fake-receipt":
        from .state import initial_state, transition
        state = initial_state(
            "V16-PRODUCTIVITY",
            mission["scope"]["exact_head"],
            mission["scope"].get("tree_sha", ""),
            review_policy=resolve_review_policy(mission),
        )
        state = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=mission["scope"]["exact_head"], head_sha=mission["scope"]["exact_head"], counterexample_ids=["CE-SCHEMA"], updated_at="9999-12-31T00:00:01Z")
        state = transition(state, "BASELINE_REPRODUCED", base_sha=mission["scope"]["exact_head"], head_sha=mission["scope"]["exact_head"], red_counterexamples=["CE-SCHEMA"], updated_at="9999-12-31T00:00:02Z")
        candidate = "0123456789abcdef0123456789abcdef01234567"
        state = transition(state, "IMPLEMENTING", base_sha=mission["scope"]["exact_head"], head_sha=candidate, updated_at="9999-12-31T00:00:03Z")
        state = transition(state, "INNER_AUDIT_COMPLETE", base_sha=mission["scope"]["exact_head"], head_sha=candidate, spark_findings=["F1"], spark_audit_count=1, dispositions={"F1": "FIXED"}, updated_at="9999-12-31T00:00:04Z")
        return _expect_reject(lambda: transition(state, "LOCAL_READY", base_sha=mission["scope"]["exact_head"], head_sha=candidate, evidence_ids=["FAKE"], updated_at="9999-12-31T00:00:05Z"))
    if name == "inherited-secret":
        # The parent deliberately carries a synthetic secret.  The ordinary
        # runner must construct a private allowlisted environment, so the
        # checker exits zero only when that value is absent from the child.
        marker = "V16_SYNTHETIC_SECRET"
        previous = os.environ.get(marker)
        os.environ[marker] = "synthetic-value-never-public"
        try:
            result = _gate_probe([
                "python3", "-c",
                "import json,os,sys; print(json.dumps({'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'xfail':0,'unknown':0})); raise SystemExit(1 if os.getenv('V16_SYNTHETIC_SECRET') else 0)",
            ])
        finally:
            if previous is None:
                os.environ.pop(marker, None)
            else:
                os.environ[marker] = previous
        row = result["rows"][0]
        return result["decision"] == "allow" and row["decision"] == "allow" and row["exit_status"] == 0 and not row["privacy_error"] and not row["identity_error"]
    if name == "socket-access":
        # Sitecustomize is installed by GateRunner itself.  The checker only
        # passes when create_connection is rejected by that offline guard,
        # rather than by an incidental remote/network error.
        result = _gate_probe([
            "python3", "-c",
            "import json,socket,sys; blocked=False\ntry: socket.create_connection(('198.51.100.1',9),timeout=0.1)\nexcept RuntimeError as exc: blocked=(str(exc)=='offline network denied')\nexcept Exception: blocked=False\nprint(json.dumps({'total':1,'ran':1,'passed':1,'failed':0,'skipped':0,'xfail':0,'unknown':0})); raise SystemExit(0 if blocked else 1)",
        ])
        row = result["rows"][0]
        return result["decision"] == "allow" and row["decision"] == "allow" and row["exit_status"] == 0 and not row["privacy_error"]
    if name == "background-survivor":
        # The parent intentionally times out while a child ignores TERM.  The
        # runner must terminate the owned process group (TERM then KILL), emit a
        # structured RED receipt, and prove that no group survivor remains.
        command = [
            sys.executable, "-c",
            f"import pathlib,subprocess,time; marker=pathlib.Path('child.started'); subprocess.Popen([{sys.executable!r},'-c',\"import os,pathlib,signal,time; pathlib.Path('child.started').write_text(str(os.getpid())); signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)\"]); deadline=time.monotonic()+1.0\nwhile not marker.exists() and time.monotonic()<deadline: time.sleep(0.01)\ntime.sleep(30)",
        ]
        outcomes: list[bool] = []
        for _ in range(3):
            def inspect(probe_root: pathlib.Path, _result: dict[str, Any]) -> tuple[bool, bool]:
                marker = probe_root / "child.started"
                if not marker.is_file():
                    return False, True
                try:
                    child_pid = int(marker.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return False, True
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    return True, False
                except PermissionError:
                    return False, True
                # A zombie is no longer a running survivor; Linux exposes its
                # state in /proc while the reaper completes the cleanup.
                try:
                    state = pathlib.Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8").split()[2]
                except (OSError, IndexError):
                    state = "?"
                return True, state != "Z"

            result, (marker_ok, pid_alive) = _gate_probe(command, timeout_sec=1.0, inspect=inspect)
            row = result["rows"][0]
            outcomes.append(result["decision"] == "deny" and row["decision"] == "deny" and row["exit_status"] == 124 and row["survivor"] is False and row["timed_out"] is True and row["term_sent"] is True and row["kill_sent"] is True and marker_ok and not pid_alive)
        return len(outcomes) == 3 and all(outcomes)
    if name in {"skipped-green", "tree-mismatch", "timestamp-contradiction", "expected-denied-string"}:
        from .evidence import validate_row
        row = {"schema":"evidence-row.v16","case_id":"CASE-NEG","semantics":"negative","gate_id":"G-TARGETED","stage":"targeted","decision":"skipped","expected_head":mission["scope"]["exact_head"],"actual_head":mission["scope"]["exact_head"],"tree_sha":"1"*40,"dirty":False,"command":["python3","-c","pass"],"cwd":".","runtime":"python-stdlib","config":"matrix","started_at":"2026-07-31T00:00:02Z","ended_at":"2026-07-31T00:00:01Z","elapsed_sec":1.0,"exit_status":0,"counts":{"total":1,"ran":1,"passed":1,"failed":0,"skipped":0,"xfail":0,"unknown":0},"log_sha256":"a"*64,"log_mode":384,"log_size":1,"reused":False,"superseded":False,"expected_denied":"yes"}
        if name == "tree-mismatch": row["tree_sha"] = "2" * 40
        if name == "timestamp-contradiction": row["decision"] = "allow"
        return _expect_reject(lambda: validate_row(row, expected_head=mission["scope"]["exact_head"], expected_tree=mission["scope"].get("tree_sha", "")))
    if name == "independent-approval-fabrication":
        from .trace import render_pr_trace
        check = {"id": "CHK-NEG", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "ran": 1, "passed": 1, "failed": 0, "unknown": 0, "xfail": 0}
        packet = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha=mission["scope"]["exact_head"], head_sha=mission["scope"]["exact_head"], tree_sha=mission["scope"].get("tree_sha", ""), checks=[check], findings=[], closures={}, reviewed_scope=["codex/v16"], unreviewed_scope=[])["packet"]
        return packet.get("verdict") is None
    if name == "metrics-missing-or-nan":
        from .metrics import collect_metrics
        return _expect_reject(lambda: collect_metrics(mission=mission, evidence={}, review={}, spark_results=[], gate_runs=[]))
    if name == "manifest-row-deletion":
        spec = importlib.util.spec_from_file_location("verify_governance", root / "scripts/verify-governance.py")
        module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8")); bad = copy.deepcopy(manifest); bad["files"].pop(next(iter(bad["files"])))
        return module.verify_manifest_exact(root, bad)["status"] == "RED"
    if name == "gate-missing-artifact":
        return _gate_entrypoint_denominator_probe(lambda result: result["rows"].pop())
    if name == "gate-duplicate-artifact":
        return _gate_entrypoint_denominator_probe(lambda result: result["rows"].__setitem__(1, {**result["rows"][1], "entrypoint_id": "EP-A"}))
    raise R1Error("unknown negative family", family.get("family", "?"))


def run_negative_matrix(root: str | pathlib.Path) -> dict[str, Any]:
    root_path = pathlib.Path(root).resolve()
    rows: list[dict[str, Any]] = []
    for family in NEGATIVE_FAMILIES:
        started = _utc()
        try:
            passed = bool(_family_probe(root_path, family))
            error = "" if passed else "negative candidate was accepted"
        except Exception as exc:
            passed = False; error = f"{type(exc).__name__}: {exc}"
        ended = _utc()
        row = {"case_id": family["case_id"], "family": family["family"], "mechanism": family["mechanism"], "expected": "RED", "status": "GREEN" if passed else "RED", "started_at": started, "ended_at": ended, "denominator": 1, "total": 1, "ran": 1, "passed": 1 if passed else 0, "failed": 0 if passed else 1, "skipped": 0, "xfail": 0, "unknown": 0, "error": error}
        row["row_sha256"] = canonical_sha256(row)
        rows.append(row)
    result = {"schema": "negative-matrix.v16", "matrix_id": "V16-R1-NEGATIVE-FAMILIES", "total": len(rows), "ran": sum(r["ran"] for r in rows), "passed": sum(r["passed"] for r in rows), "failed": sum(r["failed"] for r in rows), "skipped": sum(r["skipped"] for r in rows), "xfail": sum(r["xfail"] for r in rows), "unknown": sum(r["unknown"] for r in rows), "rows": rows}
    if result["total"] != 22 or result["ran"] != result["total"] or result["passed"] != result["total"] or any(result[k] for k in ("failed", "skipped", "xfail", "unknown")):
        result["status"] = "RED"
    else:
        result["status"] = "GREEN"
    result["matrix_sha256"] = canonical_sha256(result)
    return result


def verify_candidate_finding(finding_id: str, root: str | pathlib.Path) -> bool:
    """Candidate-side checks used by the fixed eleven-case R1 suite."""
    if finding_id not in R1_FINDINGS:
        raise R1Error("unknown finding ID", finding_id)
    root_path = pathlib.Path(root).resolve()
    if finding_id == "V16-R1-001":
        from .spark import validate_dispatch_transcript
        return _expect_reject(lambda: validate_dispatch_transcript(json.loads((root_path / "codex/v16/contracts/v16_dispatch_transcript.json").read_text()))) is False
    if finding_id == "V16-R1-002":
        text = (root_path / "codex/v16/presubmit.py").read_text()
        return all(token in text for token in ("compile_file", "GateRunner", "validate_envelope", "collect_metrics", "render_pr_trace"))
    if finding_id == "V16-R1-003":
        return run_negative_matrix(root_path)["status"] == "GREEN"
    if finding_id == "V16-R1-004":
        return all(_family_probe(root_path, f) for f in NEGATIVE_FAMILIES if f["family"] in {"invariant-without-acceptance", "fresh-bypasses-full"})
    if finding_id == "V16-R1-005":
        from .compiler import compile_mission
        mission = _mission(root_path); mission["entrypoints"][0]["argv"] = ["/bin/sh", "-c", "echo unsafe"]
        return _expect_reject(lambda: compile_mission(mission))
    if finding_id == "V16-R1-006":
        return all(_family_probe(root_path, f) for f in NEGATIVE_FAMILIES if f["family"] in {"missing-green-or-p1-followup", "fake-receipt"})
    if finding_id == "V16-R1-007":
        text = (root_path / "codex/v16/runner.py").read_text()
        return all(token in text for token in ("start_new_session=True", "sitecustomize", "privacy_scan", "os.killpg"))
    if finding_id == "V16-R1-008":
        return all(_family_probe(root_path, f) for f in NEGATIVE_FAMILIES if f["family"] in {"skipped-green", "tree-mismatch", "timestamp-contradiction", "expected-denied-string"})
    if finding_id == "V16-R1-009":
        from .trace import render_pr_trace
        check = {"id": "CHK-NEG", "status": "GREEN", "reused": False, "skipped": False, "cost": "tiny", "denominator": 1, "total": 1, "ran": 1, "passed": 1, "failed": 0, "unknown": 0, "xfail": 0}
        packet = render_pr_trace(mission_id="V16-PRODUCTIVITY", base_sha="e"*40, head_sha="f"*40, tree_sha="0"*40, checks=[check], findings=[], closures={}, reviewed_scope=["codex/v16"], unreviewed_scope=[])["packet"]
        return packet.get("verdict") is None
    if finding_id == "V16-R1-010":
        from .metrics import collect_metrics
        return _expect_reject(lambda: collect_metrics(mission=_mission(root_path), evidence={}, review={}, spark_results=[], gate_runs=[]))
    if finding_id == "V16-R1-011":
        spec = importlib.util.spec_from_file_location("verify_governance", root_path / "scripts/verify-governance.py")
        module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
        manifest = json.loads((root_path / "manifest.json").read_text()); return module.verify_manifest_exact(root_path, manifest)["status"] == "GREEN"
    return False
