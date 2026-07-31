import copy
import unittest

from codex.v16.metrics import (
    MetricsError,
    collect_review_efficiency_metrics,
    validate_review_efficiency_metrics,
)


HEAD = "a" * 40
TREE = "b" * 40
ENVELOPE = "c" * 64
T0 = "2026-08-01T00:00:00Z"


def _event(event_id, kind, timestamp, *, round_id="round-1", run_id="run-1", **extra):
    event = {
        "event_id": event_id,
        "type": kind,
        "timestamp": timestamp,
        "round_id": round_id,
        "run_id": run_id,
        "head_sha": HEAD,
        "envelope_sha256": ENVELOPE,
    }
    event.update(extra)
    return event


def _finding(*, finding_id="F-1", severity="P2", attribution="ORIGINAL_SCOPE_MISSED", disposition="FIXED"):
    return {
        "id": finding_id,
        "severity": severity,
        "label": "FOLLOW_UP" if severity != "P1" else "BLOCKING",
        "attribution": attribution,
        "contract_clause": "contract clause",
        "location": "codex/v16/metrics.py:1",
        "counterexample": "deterministic counterexample",
        "impact": "incorrect decision",
        "smallest_acceptable_outcome": "close finding",
        "acceptance_check": "focused check",
        "disposition": disposition,
    }


def _source(events, adjudications=None, usage=None):
    return {
        "schema": "review-efficiency.v16",
        "envelope": {"head_sha": HEAD, "tree_sha": TREE, "envelope_sha256": ENVELOPE},
        "events": events,
        "adjudications": adjudications or [],
        "usage": usage or {"model_calls": 2, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "token_count_basis": "provider-reported"},
    }


class ReviewEfficiencyMetricsTests(unittest.TestCase):
    def test_valid_no_finding_approval_keeps_first_finding_unavailable(self):
        source = _source([_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:02Z", verdict="APPROVE")])
        metrics = collect_review_efficiency_metrics(source)
        self.assertIsNone(metrics["time_to_first_actionable_finding_sec"])
        self.assertEqual(metrics["review_round_count"], 1)

    def test_finding_closure_and_merge(self):
        events = [
            _event("S", "review_started", T0),
            _event("F", "actionable_finding", "2026-08-01T00:00:03Z", finding=_finding()),
            _event("V", "verdict", "2026-08-01T00:00:05Z", verdict="REQUEST_CHANGES"),
            _event("M", "merge", "2026-08-01T00:00:09Z", merge_status="MERGED"),
        ]
        adjudications = [{"adjudication_id": "A", "type": "verdict_correctness", "timestamp": "2026-08-01T00:00:08Z", "round_id": "round-1", "run_id": "run-1", "head_sha": HEAD, "envelope_sha256": ENVELOPE, "correct": True, "oracle": "accepted_closure", "evidence_ref": "closure-artifact"}]
        metrics = collect_review_efficiency_metrics(_source(events, adjudications))
        self.assertEqual(metrics["time_to_first_actionable_finding_sec"], 3.0)
        self.assertEqual(metrics["time_to_verdict_sec"], 5.0)
        self.assertEqual(metrics["time_to_correct_verdict_sec"], 8.0)
        self.assertEqual(metrics["time_to_correct_merge_sec"], 9.0)

    def test_truth_unavailable_does_not_become_zero(self):
        source = _source([_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:01Z", verdict="REQUEST_CHANGES")])
        metrics = collect_review_efficiency_metrics(source)
        self.assertIsNone(metrics["time_to_correct_verdict_sec"])
        self.assertIsNone(metrics["time_to_correct_merge_sec"])
        self.assertIsNone(metrics["false_blocker_rate"])
        self.assertIsNone(metrics["p1_miss_count"])

    def test_false_blocker_requires_objective_denominator(self):
        events = [_event("S", "review_started", T0), _event("F", "actionable_finding", "2026-08-01T00:00:01Z", finding=_finding()), _event("V", "verdict", "2026-08-01T00:00:02Z", verdict="REQUEST_CHANGES")]
        base = {"adjudication_id": "B", "type": "blocker", "timestamp": "2026-08-01T00:00:03Z", "round_id": "round-1", "run_id": "run-1", "head_sha": HEAD, "envelope_sha256": ENVELOPE, "finding_id": "F-1", "is_false_blocker": True, "oracle": "objective", "evidence_ref": "oracle"}
        self.assertEqual(collect_review_efficiency_metrics(_source(events, [base]))["false_blocker_rate"], 1.0)
        bad = {**base, "oracle": "author_disagree"}
        with self.assertRaises(MetricsError):
            collect_review_efficiency_metrics(_source(events, [bad]))

    def test_p1_miss_requires_closed_observation_window(self):
        events = [_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:01Z", verdict="APPROVE")]
        window = {"adjudication_id": "W", "type": "observation_window", "started_at": T0, "ended_at": "2026-08-01T00:00:10Z", "closed": False, "head_sha": HEAD, "envelope_sha256": ENVELOPE}
        miss = {"adjudication_id": "P", "type": "p1_miss", "timestamp": "2026-08-01T00:00:09Z", "round_id": "round-1", "run_id": "run-1", "head_sha": HEAD, "envelope_sha256": ENVELOPE, "finding_id": "late-p1", "observation_id": "W"}
        self.assertIsNone(collect_review_efficiency_metrics(_source(events, [miss, window]))["p1_miss_count"])
        closed = {**window, "closed": True}
        self.assertEqual(collect_review_efficiency_metrics(_source(events, [miss, closed]))["p1_miss_count"], 1)

    def test_invalid_timestamp_fabricated_values_and_extra_fields_rejected(self):
        source = _source([_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:01Z", verdict="APPROVE")])
        bad_time = copy.deepcopy(source); bad_time["events"][1]["timestamp"] = "2026-08-01T00:00:00+08:00"
        with self.assertRaises(MetricsError): collect_review_efficiency_metrics(bad_time)
        source_with_extra = copy.deepcopy(source); source_with_extra["events"][0]["fabricated"] = 0
        with self.assertRaises(MetricsError): collect_review_efficiency_metrics(source_with_extra)
        metrics = collect_review_efficiency_metrics(source)
        forged = {**metrics, "time_to_verdict_sec": 0}
        with self.assertRaises(MetricsError): validate_review_efficiency_metrics(forged, source_bundle=source)
        forged_bool = {**metrics, "review_round_count": True}
        with self.assertRaises(MetricsError): validate_review_efficiency_metrics(forged_bool, source_bundle=source)

    def test_envelope_drift_is_not_scope_reopen(self):
        events = [_event("S", "review_started", T0), _event("R", "scope_reopened", "2026-08-01T00:00:01Z", previous_envelope_sha256="d" * 64), _event("V", "verdict", "2026-08-01T00:00:02Z", verdict="APPROVE")]
        self.assertEqual(collect_review_efficiency_metrics(_source(events))["scope_reopened_count"], 0)

    def test_usage_arithmetic_and_bool_as_int(self):
        events = [_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:01Z", verdict="APPROVE")]
        bad = {"model_calls": 1, "input_tokens": 2, "output_tokens": 3, "total_tokens": 99, "token_count_basis": "provider-reported"}
        with self.assertRaises(MetricsError): collect_review_efficiency_metrics(_source(events, usage=bad))
        bool_usage = {"model_calls": True, "input_tokens": 2, "output_tokens": 3, "total_tokens": 5, "token_count_basis": "provider-reported"}
        with self.assertRaises(MetricsError): collect_review_efficiency_metrics(_source(events, usage=bool_usage))

    def test_source_hash_mismatch_rejected(self):
        source = _source([_event("S", "review_started", T0), _event("V", "verdict", "2026-08-01T00:00:01Z", verdict="APPROVE")])
        metrics = collect_review_efficiency_metrics(source)
        source["events"][1]["verdict"] = "REQUEST_CHANGES"
        with self.assertRaises(MetricsError): validate_review_efficiency_metrics(metrics, source_bundle=source)


if __name__ == "__main__":
    unittest.main()
