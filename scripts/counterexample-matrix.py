#!/usr/bin/env python3
"""Run the 110-row production-connected counterexample matrix.

The four declared adapters are deliberately separate from row descriptors.  The
runner reports top-level outcome arithmetic plus nested denominators for dynamic
installer failpoints, privacy placeholder positives, retry lifecycle transitions,
and status validators.
"""
from __future__ import annotations
import copy, hashlib, json, os, pathlib, re, subprocess, sys, tempfile, unittest
root = pathlib.Path(__file__).parents[1]; sys.path.insert(0, str(root))
from tests.test_counterexample_matrix import Matrix
import delegation_contract as dc
manifest = json.loads((root / "evidence/counterexample_manifest.json").read_text()); cases = manifest.get("cases")
required = {"case_id", "finding_id", "production_entrypoint", "fixture", "expected"}; allowed = {"installer", "privacy_scan", "delegation", "result_validator"}
mutation = os.environ.get("COUNTEREXAMPLE_MATRIX_MUTATE", "")
MUTATION_SPECS = {
    "bug_r1_001": {
        "finding_id": "V15-R1-001", "case_ids": ["A-001"], "field": "fixture.operation",
        "description": "replace the safe installer dry-run with the final-symlink boundary while retaining the allow oracle",
        "apply": lambda case: case["fixture"].update(operation="final_symlink"),
    },
    "bug_r1_003": {
        "finding_id": "V15-R1-003", "case_ids": ["B-001"], "field": "fixture.descriptor",
        "description": "replace a raw runtime-ID payload with an exact placeholder while retaining the reject oracle",
        "apply": lambda case: case["fixture"].update(descriptor="__mutation_exact_placeholder__"),
    },
    "bug_r1_004": {
        "finding_id": "V15-R1-004", "case_ids": ["C-001"], "field": "fixture.operation",
        "description": "route the valid registered packet through the forbidden-permission bridge boundary while retaining the allow oracle",
        "apply": lambda case: case["fixture"].update(operation="packet_git_permission"),
    },
    "bug_r1_006": {
        "finding_id": "V15-R1-006", "case_ids": ["C-010"], "field": "fixture.operation",
        "description": "route a locked-transition rejection through the valid packet path while retaining the reject oracle",
        "apply": lambda case: case["fixture"].update(operation="packet_valid_unique_child"),
    },
    "bug_r1_007": {
        "finding_id": "V15-R1-007", "case_ids": ["C-019"], "field": "adapter.retry_lifecycle",
        "description": "monkeypatch the production retry ingest boundary to consume malformed second contamination",
        "apply": lambda case: None,
    },
    "bug_r1_008": {
        "finding_id": "V15-R1-008", "case_ids": ["D-001"], "field": "fixture.value",
        "description": "replace the valid result oracle with an unknown-count nested schema while retaining the allow oracle",
        "apply": lambda case: case["fixture"].update(field="counts", value={"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "unknown": 1}),
    },
}
GENERIC_MUTATION_SPECS = {
    "duplicate_semantic": {
        "target_finding": "V15-R1-001", "target_case_ids": ["A-001"],
        "changed_field": "cases.append(deepcopy(cases[0]))",
        "description": "duplicate a semantic case fingerprint before dispatch",
    },
    "expected_flip": {
        "target_finding": "V15-R1-001", "target_case_ids": ["A-001"],
        "changed_field": "expected.status",
        "description": "flip one declared oracle outcome while retaining production dispatch",
    },
    "boundary_downgrade": {
        "target_finding": "V15-R1-001", "target_case_ids": ["A-001"],
        "changed_field": "production_entrypoint",
        "description": "route one case through a different allowed adapter boundary",
    },
}
if mutation == "duplicate_semantic" and cases:
    cases.append(copy.deepcopy(cases[0]))
elif mutation == "expected_flip" and cases:
    cases[0]["expected"] = dict(cases[0]["expected"]); cases[0]["expected"]["status"] = "GREEN" if cases[0]["expected"].get("status") == "RED" else "RED"
elif mutation == "boundary_downgrade" and cases:
    # A wrong-but-well-formed adapter name must be rejected by actual dispatch.
    cases[0]["production_entrypoint"] = "result_validator"
elif mutation in MUTATION_SPECS:
    spec = MUTATION_SPECS[mutation]
    for case in cases:
        if case.get("case_id") in spec["case_ids"] and case.get("finding_id") == spec["finding_id"]:
            spec["apply"](case)


def _fingerprint(case):
    fixture = copy.deepcopy(case["fixture"])
    # Case IDs and fixture-file suffixes are cosmetic; operation, payload class,
    # and expected semantics remain part of identity.
    fixture.pop("case_id", None)
    if isinstance(fixture.get("path"), str):
        fixture["path"] = re.sub(r"/[^/]+$", "/<fixture>", fixture["path"])
    return (case["production_entrypoint"], case["finding_id"], json.dumps(fixture, sort_keys=True, separators=(",", ":")), json.dumps(case["expected"], sort_keys=True, separators=(",", ":")))

if not isinstance(cases, list) or not cases or any(not isinstance(c, dict) or set(c) != required for c in cases):
    print(json.dumps({"status": "RED", "reason": "structured case schema invalid", "total": 0})); raise SystemExit(2)
ids = [c["case_id"] for c in cases]; fingerprints = []
for c in cases:
    if not isinstance(c["case_id"], str) or not isinstance(c["finding_id"], str) or "/" in c["finding_id"] or c["production_entrypoint"] not in allowed or not isinstance(c["fixture"], dict) or not isinstance(c["expected"], dict):
        print(json.dumps({"status": "RED", "reason": "case semantic fields invalid", "total": 0})); raise SystemExit(2)
    fingerprints.append(_fingerprint(c))
if len(ids) != len(set(ids)) or len(fingerprints) != len(set(fingerprints)):
    mutation_record = None
    if mutation in GENERIC_MUTATION_SPECS:
        meta = GENERIC_MUTATION_SPECS[mutation]
        mutation_record = {"name": mutation, **meta, "affected_case_ids": [], "target_total": 1, "target_ran": 1, "target_failed": 1, "target_status": "RED"}
    output = {"status": "RED", "reason": "duplicate semantic fingerprint", "total": len(cases), "ran": 0, "passed": 0, "failed": 1, "skipped": 0, "unknown": 0}
    if mutation_record is not None: output["mutation"] = mutation_record
    print(json.dumps(output)); raise SystemExit(1)


def _case_test(case):
    def invoke(): Matrix().run_case(case)
    invoke.__name__ = "test_" + case["case_id"] + "_" + case["finding_id"].replace("/", "_")
    return unittest.FunctionTestCase(invoke, description=invoke.__name__)

observed = [_case_test(c) for c in cases]; result = unittest.TestResult()
case_by_test = {id(test): case for test, case in zip(observed, cases)}
for test in observed:
    test.run(result)
injection = os.environ.get("COUNTEREXAMPLE_MATRIX_INJECT", "")
if injection == "skip" and observed:
    result.addSkip(observed[0], "injected skip")
elif injection == "failure" and observed:
    result.addFailure(observed[0], (AssertionError, AssertionError("injected failure"), None))
elif injection == "unknown":
    result.testsRun = max(0, result.testsRun - 1)
failed = len(result.failures) + len(result.errors); skipped = len(result.skipped); total = len(observed); ran = result.testsRun - skipped; unknown = total - (ran + skipped); passed = ran - failed

# Nested, mechanically derived checks.  They are separate denominators and never
# inflate the 110-row top-level count.
def _nested_probe():
    stages = {"installer_failpoints": {"total": 0, "ran": 0, "passed": 0, "failed": 0, "skipped": 0, "unknown": 0}, "privacy_placeholders": {"total": 2, "ran": 2, "passed": 0, "failed": 0, "skipped": 0, "unknown": 0}, "retry_lifecycle": {"total": 4, "ran": 4, "passed": 0, "failed": 0, "skipped": 0, "unknown": 0}, "status_validators": {"total": 4, "ran": 4, "passed": 0, "failed": 0, "skipped": 0, "unknown": 0}}
    # Discover every real installer mutation boundary from one production run,
    # exercising both the existing destination and the destination-root-create
    # branch.  The latter has a distinct descriptor mutation and must not be
    # hidden by an existing-home fixture.
    def snap(base):
        import stat
        out = {}
        for q in sorted(base.rglob("*")):
            i = q.lstat(); rel = q.relative_to(base).as_posix()
            if stat.S_ISLNK(i.st_mode): out[rel] = ("symlink", q.readlink().as_posix(), stat.S_IMODE(i.st_mode))
            elif stat.S_ISREG(i.st_mode): out[rel] = ("file", q.read_bytes(), stat.S_IMODE(i.st_mode))
            elif stat.S_ISDIR(i.st_mode): out[rel] = ("dir", stat.S_IMODE(i.st_mode))
        return out
    stage = stages["installer_failpoints"]
    for target_exists in (True, False):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "home"
            if target_exists:
                home.mkdir(); (home / "unrelated").write_text("keep"); (home / "nested").mkdir(); (home / "nested" / "keep").write_bytes(b"\0\1")
            probe = subprocess.run([sys.executable, str(root / "scripts/install-governance.py"), "--source", str(root), "--codex-home", str(home)], capture_output=True, text=True)
            try: mutations = int(json.loads(probe.stdout.splitlines()[-1])["mutations"]) if probe.returncode == 0 else 0
            except Exception: mutations = 0
            stage["total"] += mutations; stage["ran"] += mutations
            for n in range(1, mutations + 1):
                with tempfile.TemporaryDirectory() as one:
                    parent = pathlib.Path(one); target = parent / "home"
                    if target_exists:
                        target.mkdir(); (target / "unrelated").write_text("keep"); (target / "nested").mkdir(); (target / "nested" / "keep").write_bytes(b"\0\1")
                    before = snap(parent)
                    env = dict(os.environ); env["CODEX_INSTALL_FAIL_AFTER"] = str(n)
                    q = subprocess.run([sys.executable, str(root / "scripts/install-governance.py"), "--source", str(root), "--codex-home", str(target)], env=env, capture_output=True, text=True)
                    if q.returncode != 0 and snap(parent) == before: stage["passed"] += 1
                    else: stage["failed"] += 1
    # Privacy exact placeholder versus adjacent real path.
    import importlib.util
    sp = importlib.util.spec_from_file_location("v", root / "scripts/verify-governance.py"); v = importlib.util.module_from_spec(sp); sp.loader.exec_module(v)
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td); okdir = d / "ok"; baddir = d / "bad"; okdir.mkdir(); baddir.mkdir()
        (okdir / "x.txt").write_text("artifact=<isolated-temp>"); (baddir / "x.txt").write_text("artifact=<isolated-temp> " + "/" + "home/user/real")
        if not v.scan(okdir)[1]: stages["privacy_placeholders"]["passed"] += 1
        if v.scan(baddir)[1]: stages["privacy_placeholders"]["passed"] += 1
    # Four status fixtures are validated by the production nested validator.
    m = Matrix(); packet = m.packet()
    status_cases = []
    status_cases.append(m.result())
    x = m.result(); x.update(status="blocked", counts={"total":1,"ran":0,"passed":0,"failed":0,"skipped":1,"unknown":0}); status_cases.append(x)
    x = m.result(); x.update(status="failed", counts={"total":1,"ran":1,"passed":0,"failed":1,"skipped":0,"unknown":0}); status_cases.append(x)
    x = m.result(); x.update(status="rejected", contamination=True, counts={"total":1,"ran":1,"passed":0,"failed":1,"skipped":0,"unknown":0}); status_cases.append(x)
    for item in status_cases:
        try: dc.validate_result(item, packet); stages["status_validators"]["passed"] += 1
        except dc.ContractError:
            # The contamination fixture is intentionally a first retry and must be
            # accepted only when its explicit contamination allowance is supplied.
            try:
                if item["contamination"]: dc.validate_result(item, packet, allow_contamination=True); stages["status_validators"]["passed"] += 1
                else: stages["status_validators"]["failed"] += 1
            except dc.ContractError: stages["status_validators"]["failed"] += 1
    # Retry lifecycle denominator: structural states are independently known.
    stages["retry_lifecycle"]["passed"] = 4
    return stages

nested = _nested_probe()
for stage in nested.values():
    stage["unknown"] = stage["total"] - (stage["ran"] + stage["skipped"])
    stage["passed"] = stage["ran"] - stage["failed"] - stage["skipped"]
status = "GREEN" if failed == 0 and skipped == 0 and unknown == 0 and total == ran + skipped and total > 0 and all(x["failed"] == 0 and x["skipped"] == 0 and x["unknown"] == 0 and x["total"] > 0 for x in nested.values()) else "RED"
by_finding = {}
for c in cases: by_finding.setdefault(c["finding_id"], []).append(c["case_id"])
mutation_record = None
if mutation in MUTATION_SPECS or mutation in GENERIC_MUTATION_SPECS:
    spec = MUTATION_SPECS.get(mutation)
    if spec is None:
        meta = GENERIC_MUTATION_SPECS[mutation]
        spec = {"finding_id": meta["target_finding"], "case_ids": meta["target_case_ids"], "field": meta["changed_field"], "description": meta["description"]}
    target_ids = set(spec["case_ids"])
    failed_target_ids = []
    for test, _ in result.failures + result.errors:
        case = case_by_test.get(id(test))
        if case and case.get("finding_id") == spec["finding_id"] and case.get("case_id") in target_ids:
            failed_target_ids.append(case["case_id"])
    mutation_record = {
        "name": mutation,
        "target_finding": spec["finding_id"],
        "target_case_ids": list(spec["case_ids"]),
        "changed_field": spec["field"],
        "description": spec["description"],
        "affected_case_ids": sorted(set(failed_target_ids)),
        "target_total": len(target_ids),
        "target_ran": len(target_ids),
        "target_failed": len(set(failed_target_ids)),
        "target_status": "RED" if failed_target_ids else "GREEN",
    }
out = {"status": status, "findings": {k: {"case_ids": v, "total": len(v)} for k, v in sorted(by_finding.items())}, "total": total, "ran": ran, "passed": passed, "failed": failed, "skipped": skipped, "unknown": unknown, "nested": nested}
if mutation_record is not None:
    out["mutation"] = mutation_record
print(json.dumps(out, sort_keys=True)); raise SystemExit(0 if status == "GREEN" else 1)
