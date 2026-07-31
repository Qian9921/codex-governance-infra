# Review workflow

Freeze the acceptance envelope and exact Git head. The Qian author runs affected presubmit and records known denominators, privacy scan, fresh-clone verification, and nested delegation trace. A fresh zero-context GPT-5.6-Sol xhigh report-only reviewer is the sole gate; Liang9921 is the governance identity. APPROVE requires complete coverage, empty unreviewed scope, and no P1/BLOCKING findings. Writer cannot approve or merge.

## V16 trace and productivity gates

The V16 author runs `python3 scripts/presubmit.py --repo .`, which compiles the
mission, exercises positive/negative contract cases, checks gate ordering and
identity drift, validates evidence arithmetic/privacy, renders a sanitized PR
packet, derives the metrics dashboard, and verifies a fresh archive. The packet
contains exact base/head/tree, Qian author and Liang reviewer identities,
lineage mode, coverage, check denominators/costs, finding closures, and the
mechanically derived verdict. It never calls GitHub or changes identity.

The three Spark audit packets are bounded and report-only; every finding is
explicitly `FIXED`, `DISAGREE`, or `FOLLOW_UP` before readiness can advance.
Metrics are derived from artifacts, while thresholds are policy targets rather
than asserted outcomes. A fresh zero-context GPT-5.6-Sol report-only review is
requested once at `REVIEW_READY`; one remediation identity is permitted before
escalating a contract challenge.
