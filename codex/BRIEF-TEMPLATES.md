# Codex Brief 模板（AGENTS.md §7–8 的执行细则）

本文件在派 assigned execution agent 或 Independent reviewer 前读取。Brief 必须独立成立：事实、路径、
权限、参数和验收可机验；不让子代理猜，不把未授权动作藏在“完成”里。

## Assigned execution mission

~~~text
assigned_model: gpt-5.6-sol | gpt-5.6-terra | gpt-5.6-luna
角色：<L0/L1/L2 writer、reviewer、planner 或其他明确 role>；reasoning_effort=<effort>。
权限：<read|write|run|review；逐项写明允许的动作、工具、进程和外部边界>
reviewer_separation: <是否与 writer/execution agent 分离；review role 的 report-only 等限制>
writer_lineage：
  writer_instance_id: <control-plane canonical instance/task ID>
  writer_task_id: <task ID>
  writer_run_or_session_id: <独立 run/session ID>
  writer_requested_model: <control-plane requested model>
  writer_requested_reasoning_effort: <control-plane requested effort>
writer_fork_turns: none
writer_parent_task_id: <control-plane parent task ID>
writer_spawn_evidence_ref: <immutable spawn request/response evidence ref>
lineage_mode: FULL_CONTROL_PLANE | DISPATCH_TRANSCRIPT
dispatch_transcript (fallback only when hidden backend fields are not exposed): <machine transcript of
spawn request model/effort/fork/task; response canonical task_name; platform parent task path; exact snapshot/
hash set; fresh distinct writer/reviewer tasks; fork_turns=none; Sender/final envelope; artifact hashes>
dispatch_lineage_verified: true | false
backend_instance_lineage: available | unavailable (unavailable is not full backend identity)
目标/用户可见结果：<具体结果>
模式与风险：<Explore|Integrate|Harden|Ship；四轴画像>
Source of truth、事实/假设/未知、数据 lineage、单位/frame、切分和基线：<写死>
REVIEW-CONTRACT-LOCK / Acceptance Envelope（身份建立后不可静默漂移；漂移须新 identity）：
  milestone: PARITY | HARDENING | SURPASS
  objective: <可证伪目标>
  reference_identities: <版本/commit/config + hash>
  operating_domain: <单位、frame、dtype、尺度、条件、适用数据>
  acceptance_thresholds: <冻结阈值与分母>
  mandatory_invariants: <必须成立的不变量>
  explicit_non_goals: <明确不验收的内容>
  exact_review_scope: <路径/依赖/测试>
  evidence_budget: <E0-review(owner=指定 reviewer)|E0-checker-evidence/E1-E4(owner=assigned execution agent) 及成本上限>
  reference_first: <本地 Theseus/Ceres URL+version/commit/config/artifact hash；禁止漂移 oracle>
  parity_sequence: <synthetic exact-zero gate -> real exact-zero gate；synthetic 未过则停止>
  parity_exact_zero: <mismatch_count=0；max_abs_error=0；无 NaN/Inf；total>0；failed/skipped/xfail/unknown_denominator=0>
  parity_blocker: <任一非零/skip/xfail/缺 oracle 或未知分母 => ZERO_PARITY_BLOCKED；不得放宽容差>
允许写入的精确路径/文件：<列出；只限本 mission>
允许的执行命令（含 cwd、runtime、配置、数据、并发、日志、timeout、stop 条件）：<写死>
允许的 Git/包/依赖/外部动作（若无写 none）：<逐项授权；编辑不推导 Git 权限>
GitHub roles（每项仍需授权）：development=`Qian9921` may branch/fix/commit/push/open PR/author-reply;
governance=`Liang9921` may review/comment/approve/merge only after Independent Sol + exact-head/match-head;
Qian 不得 review/approve/merge；Liang 不得 feature commit/push/open development PR；账号 mismatch stop，
切换账号须用户授权；PR-TRACE 必填 author/reviewer/approver/merger_login。
明确不在范围：<文件、仓库、数据、外部服务、破坏性动作>
验收与验证等级：<E0-review(owner=指定 reviewer)|E0-checker-evidence/E1-E4(owner=assigned execution agent)；E1–E4 均写命令、预期结构、
WHY-RED、expected cost、已知分母及 red/green 可证明什么；E2–E4 另写下层为何不足；相同 exact head 的有效证据不得无意义重跑>
失败/升级/回滚：<停止条件；无授权执行 lane/tool 才 EXEC_INFRA_BLOCKED；回滚路径>
硬禁令：不得静默改 assigned_model、role、permissions 或 scope；不得 nohup/disown/孤儿；不得 brief 外实验；不得静默吞异常。
交付：仅在上面列出的精确授权路径/动作内完成端到端生产、直接调用、相关测试/构建/文档和
普通失败修复；不得把“完整交付”理解为扩展到未授权的 caller、consumer、生产面或依赖；模型名称不构成能力拒绝。
任何必要但越界的工作都作为 scoped blocker 请求新授权，不得擅自补齐。返回：改动摘要、精确
路径、真实命令/输出/分母、契约影响、未验证项、残余风险、回滚。

每个 checker/test/eval 必须附 evidence envelope（不得复制旧结果）：
review_identity: Git 的完整 tested head SHA，或 non-Git 的 content-snapshot ID/hash set
worktree_identity: dirty/clean 及可复核标识（non-Git 写 N/A）
command, cwd, runtime, config, started_at, finished_at, exit_status
total=passed+failed+skipped；ran=passed+failed；passed、failed、skipped 及每项分母
artifact_identity 与 log_identity
~~~

### Hook receipt acceptance (Route 2)

`SessionStart`/`SubagentStart`/`PreToolUse` allow+deny must append privacy-safe JSONL through
`hooks/hook_receipt.py`: schema/version, UTC, event, model, optional tool_name, decision/reason code,
combined exact snapshot SHA-256, source, pid/ppid; only SHA-256 session/turn/tool-call IDs. Never raw
prompt/tool args/cwd/token/credentials. Runtime dir 0700, daily file 0600, append+no-follow; receipt
failure cannot change policy decision but blocks runtime-proof acceptance. Tests set explicit test source and
temp dir; runtime without test env is runtime. A report-only reviewer may only `rg -n`; assigned execution agent reads receipt and verifies
snapshot/task transcript hashes.

## Review record / evidence envelope（Independent Sol 唯一终审）

~~~text
review_record_id: <唯一 ID>
gate_type: independent_sol
lineage_mode: FULL_CONTROL_PLANE | DISPATCH_TRANSCRIPT
dispatch_lineage_verified: true | false
backend_instance_lineage: available | unavailable
round_id: <不可变轮次 ID>
writer_instance_id: <ID>
writer_task_id: <ID>
writer_run_or_session_id: <独立 ID>
writer_requested_model: <requested model>
writer_requested_reasoning_effort: <requested effort>
writer_fork_turns: none
writer_parent_task_id: <parent task ID>
writer_spawn_evidence_ref: <control-plane spawn request/response evidence ref>
reviewer_instance_id: <独立 fresh reviewer ID>
reviewer_task_id: <review task ID>
reviewer_run_or_session_id: <独立 ID>
reviewer_requested_model: gpt-5.6-sol
reviewer_requested_reasoning_effort: xhigh
reviewer_fork_turns: none
reviewer_parent_task_id: <control-plane parent task ID>
reviewer_spawn_evidence_ref: <control-plane spawn request/response evidence ref>
context_mode: zero-context
reviewer_is_writer: false
lineage_verified: true
review_identity_mode: Git/PR exact-head | Non-Git exact-snapshot
pinned_identity:
  git_base_sha: <完整 40-hex 或 N/A>
  git_head_sha: <完整 40-hex 或 N/A>
  snapshot_id: <ID 或 N/A>
  exact_paths_scope_hash_set: <逐项 exact path、scope、SHA-256；Git 可写 N/A>
  before_sha256_bytes_lines: <每个有 before snapshot 的 path；无则 N/A>
  final_sha256_bytes_lines: <每个 exact path>
  current_initial_sha256_bytes_lines: <每个 exact path>
  current_final_sha256_bytes_lines: <每个 exact path>
  identity_drift: none | true（true 时 review invalid）
acceptance_envelope:
  milestone: PARITY | HARDENING | SURPASS
  objective: <冻结目标>
  reference_identities: <版本/commit/config + hash>
  operating_domain: <单位/frame/dtype/尺度/条件>
  acceptance_thresholds: <阈值与分母>
  mandatory_invariants: <不变量>
  explicit_non_goals: <非目标>
  exact_review_scope: <冻结 scope>
  evidence_budget: <E0-review(owner=指定 reviewer)|E0-checker-evidence/E1-E4(owner=assigned execution agent) 与成本>
  contract_identity: <hash/ID>
coverage_status: PARTIAL | COMPLETE
reviewed_scope: <本轮实际审查>
unreviewed_scope: <未审；COMPLETE 时必须为空>
prior_reviewed_scope: <上一轮 scope；首轮 empty>
prior_unreviewed_scope: <上一轮 scope；首轮等于冻结 scope>
scope_progression: reviewed_scope=prior∪current_delta；unreviewed_scope 严格缩小
delta_basis:
  Git: old_head..new_head；Non-Git: old_snapshot_hash_set -> new_snapshot_hash_set（变更 exact paths/content）
  counterexamples: <原反例>
  affected_boundaries: <直接 affected boundaries>
  non_git_identity_rule: <path-set/scope/Acceptance Envelope drift => new identity>
evidence:
  tested_identity: <与 pinned_identity 完整相等的 Git head 或 snapshot ID/hash set>
  worktree_identity: dirty:<标识> | clean:<标识> | N/A (non-Git)
  command: <真实命令>
  cwd: <绝对路径>
  runtime: <解释器/版本/环境>
  config: <配置路径与 hash/版本>
  started_at: <时间戳>
  finished_at: <时间戳>
  exit_status: <整数>
  total: <整数；total=passed+failed+skipped>
  ran: <整数；ran=passed+failed>
  passed: <整数>
  failed: <整数>
  skipped: <整数及原因>
  artifact_identity: <路径/ID + SHA-256 或 N/A>
  log_identity: <路径/ID + SHA-256 或 N/A>
checks_run:
  - subtype: E0-review(owner=指定 reviewer) | E0-checker-evidence/E1/E2/E3/E4(owner=assigned execution agent)
    owner: <mechanically derived from assigned_model/role/permissions>
    command: <真实命令或 reused evidence ref>
    status: passed | failed | skipped | reused
    denominator: <E1–E4 必填；已知数字>
    cost: <E1–E4 必填；expected time/resource cost>
    why_red: <E1–E4 必填；当前 diff 的可证伪失败机制>
    lower_level_insufficient: <E2–E4 必填>
    red_green_proves: <E1–E4 必填>
    owner_pair_check: <E0-review+指定 reviewer 或 E0-checker-evidence/E1/E2/E3/E4+assigned execution agent；交叉组合非法>
findings:
  - finding_id: <唯一 ID>
    severity: P1 | P2 | P3
    label: BLOCKING | NON_BLOCKING | NIT | QUESTION | FOLLOW_UP | CONTRACT_CHALLENGE
    attribution: INTRODUCED | PRE_EXISTING | UNRESOLVED_ATTRIBUTION
    location: <精确 file:line>
    contract_clause: <BLOCKING 必填>
    counterexample: <最小可证伪反例>
    impact: <实际影响>
    smallest_acceptable_outcome: <最小可接受结果>
    acceptance_check: <验证关闭方式>
    blocker_admission: <若第一次 COMPLETE 之后且 label=BLOCKING：DELTA_INTRODUCED | ORIGINAL_SCOPE_MISSED | NEW_FALSIFIABLE_EVIDENCE>
    admission_evidence_ref: <BLOCKING 必填；ORIGINAL 引冻结合同+漏审位置；NEW 引新反例+evidence identity>
closure_matrix: <每个 finding ID -> disposition/commit/evidence/residual limitation/admission>
pr_trace:
  github_review_or_comment_id: <不可变记录 ID；线下裁决回写 PR>
  round_id: <不可变轮次 ID>
  exact_head_or_snapshot: <当前 identity>
  pinned_contract: <contract_identity + envelope>
  coverage: <coverage_status/reviewed/unreviewed/prior/current>
  checks_run_skipped_reused: <含 denominator/cost>
  findings: <逐 finding 含 admission evidence/ref>
  author_dispositions: <每 finding: FIXED|DISAGREE|FOLLOW_UP + commit/evidence/limitation>
  closure_matrix: <逐 finding closure>
  final_decision_record: <milestone/domain/closed blockers/limitations/non-goals/follow-ups/evidence/head/verdict>
mechanical_identity_match: true | false
review_status: COMPLETE | INFRA_BLOCKED
verdict: REQUEST_CHANGES | APPROVE | null
readiness_decision: GO | null
unverified_items: <明确列出>
residual_risks: <明确列出>
rollback: <可逆路径或 N/A>
~~~

机械规则：任何必填 lineage/acceptance/coverage/finding 字段缺失、placeholder、N/A、requested model/effort/fork/parent/run
mismatch、context 非 zero、reviewer_is_writer 非 false、lineage_verified 非 true、分母未知、
stale/copied count、tested_identity 与 pinned_identity 不相等、path/scope/hash/byte/line drift，
均为 `review_status=INFRA_BLOCKED` 且 `verdict=null`，不得产生 approval event。`APPROVE` 仅表示
Independent Sol review gate 通过且 `review_status=COMPLETE && coverage_status=COMPLETE && unreviewed_scope=empty && no active P1 && no BLOCKING`；
`coverage_status=PARTIAL` 只能出现在 `review_status=COMPLETE` 的 active P1/BLOCKING 提前反馈，且必须 `REQUEST_CHANGES`，绝不能 APPROVE。Finding 机械约束为：`REQUEST_CHANGES iff exists(active severity=P1 OR label=BLOCKING)`；
`APPROVE iff review_status=COMPLETE && coverage_status=COMPLETE && unreviewed_scope=empty && no active P1 && no BLOCKING`。
P1 必须 BLOCKING；P2/P3 不得 BLOCKING；P1 不得 NON_BLOCKING/NIT/QUESTION/FOLLOW_UP/CONTRACT_CHALLENGE；
P2/P3 可携带非阻塞标签。合同挑战本身不作为 BLOCKING，须 owner 决策后新 identity。`GO` 只能写在独立
`readiness_decision`，二者不得混用。新 identity
必须新 reviewer 与新 verdict；复用 reviewer/followup 必须提供新的 run ID。assigned execution agent
是 brief 指定的 writer，默认可建议 Luna；并行仅在 scope 互斥且无依赖时允许；Git add/commit 串行且只有一个 Git owner。
仅无授权执行 lane/tool 时返回 `EXEC_INFRA_BLOCKED`，不得以模型名称自我拒绝或静默降低权限。

EVIDENCE-BUDGET owner：`E0-review` 由指定 reviewer 完成 verified no-persist exact identity/source/diff/static reasoning；
若 review role 的 brief 设为 report-only，则 reviewer 只读。`E0-checker-evidence` 若需 checker 工具及 E1/E2/E3/E4 的
deterministic、unit、known-answer、boundary、integration、build/regression、benchmark/training/profiling 均由 assigned execution agent 执行。

REVIEW-CONTRACT-LOCK：PARITY 仅按冻结 reference/domain/threshold/invariant 验收；超域默认 `FOLLOW_UP`/`NON_BLOCKING`。
只有域内反例、invariant 违反、当前 delta 回归或新证据证明合同不安全可 `BLOCKING`；合同不足为 `CONTRACT_CHALLENGE`。
PARTIAL 只允许已有 active P1/BLOCKING 的提前反馈且必须 REQUEST_CHANGES；scope 单调扩大、unreviewed 严格缩小。
COMPLETE 后新 blocker 必填 admission=`DELTA_INTRODUCED|ORIGINAL_SCOPE_MISSED|NEW_FALSIFIABLE_EVIDENCE` 及 evidence ref；
DELTA-ONLY 使用 Git old_head..new_head 或 Non-Git snapshot hash-set delta，携带原反例/affected boundaries；identity drift 须新 review。
标签只用 `BLOCKING|NON_BLOCKING|NIT|QUESTION|FOLLOW_UP|CONTRACT_CHALLENGE`；BLOCKING 必须有条款、位置、反例、影响、最小结果和关闭验证。

## Independent Sol review（fresh zero-context 读审模板）

~~~text
角色：fresh zero-context GPT-5.6 Sol reviewer；requested_model=gpt-5.6-sol；requested_reasoning_effort=xhigh；
fork_turns=none；严格 report-only（planning L0 不是 reviewer）。
目标/风险/用户授权：<完整写出>
REVIEW-CONTRACT-LOCK（先锁定再读审，字段漂移即新 identity）：
  milestone/objective/reference_identities/operating_domain/acceptance_thresholds/mandatory_invariants:
    <冻结值>
  explicit_non_goals/exact_review_scope/evidence_budget/contract_identity: <冻结值与 hash/ID>
lineage（全部必填并机械核对，值直接来自 control-plane spawn request/response）：
  writer_instance_id/task_id/run_or_session_id: <值>
  writer_requested_model/requested_reasoning_effort/fork_turns/parent_task_id/spawn_evidence_ref: <值>
  reviewer_instance_id/task_id/run_or_session_id: <值>
  reviewer_requested_model: gpt-5.6-sol
  reviewer_requested_reasoning_effort: xhigh
  reviewer_fork_turns: none
  reviewer_parent_task_id: <值>
  reviewer_spawn_evidence_ref: <值>
  context_mode: zero-context
  reviewer_is_writer: false
  lineage_verified: true
审查身份（只选一项并固定）：
  Git/PR exact-head：base_sha=<完整 40-hex>；head_sha=<完整 40-hex>；提交/PR URL；head drift => invalid
  Non-Git exact-snapshot：snapshot_id=<ID>；exact_paths=<逐项列出>；scope=<写死>；每 path 的
    before_sha256/bytes/lines（若有 before snapshot）与 final_sha256/bytes/lines；current_initial_sha256/
    bytes/lines 与 current_final_sha256/bytes/lines；path/scope/hash drift => invalid
非 Git 高风险审查不得 ad hoc。无 before snapshot 时，INTRODUCED/PRE_EXISTING 必须相对明确提供的
baseline；无法历史归因则 attribution=UNRESOLVED_ATTRIBUTION，blocking-risk change 不得 GO。
PARITY-BEFORE-SURPASS：PARITY 只按冻结 reference/domain 验收，超域只报 FOLLOW_UP/NON_BLOCKING；
不得自行提高阈值。合同不足只能 CONTRACT_CHALLENGE 并交 owner/user。
本轮覆盖：coverage_status=PARTIAL|COMPLETE；reviewed_scope=<值>；unreviewed_scope=<值>；
prior_reviewed_scope=<上一轮>；prior_unreviewed_scope=<上一轮>；scope_progression=<并集扩大/剩余严格缩小>。
PARTIAL 只允许已有 active P1/BLOCKING 的提前反馈，verdict=REQUEST_CHANGES；APPROVE 必须 COMPLETE 且 unreviewed_scope=empty。
关闭提前 blocker 后下一轮优先覆盖全部剩余 scope；再次 PARTIAL 必须写新可证伪停止原因与剩余分母。
第一次达到 COMPLETE 的轮次之后，每个新 BLOCKING finding 的 blocker_admission 仅为 DELTA_INTRODUCED、
ORIGINAL_SCOPE_MISSED（引原冻结合同和漏审位置）或 NEW_FALSIFIABLE_EVIDENCE（引新反例和 evidence identity），
并逐 finding 填 admission_evidence_ref；非阻塞标签不得与同一 finding 的 BLOCKING 并存。
DELTA-ONLY-REREVIEW：Git=`old_head..new_head`；Non-Git=`old_snapshot_hash_set -> new_snapshot_hash_set` changed exact paths/content（变更 exact paths/content）；
两者带原反例与直接 affected boundaries；Non-Git path-set/scope/Acceptance Envelope drift => 新 identity。
审查范围：<Git diff + 直接依赖 + affected tests，或 non-Git exact_paths；whole-repo audit 必须明示>
允许：仅 verified no-persist read operations（源码/diff/log/status 与读取 assigned execution agent evidence envelope）。
       assigned execution agent 必须预创建并清理 0700 isolation root；report-only reviewer 不得向该 root、仓库或任何持久路径写入。
       若工具无法无写运行，由 assigned execution agent 在授权 lane 执行并提供 envelope，否则 review_status=INFRA_BLOCKED。
禁止：改任何 tracked/untracked/文档/配置/仓库外持久文件；任何 formatter/test/build/experiment/
       diagnostic/profiling；Git mutation、commit/push/merge、外部动作、扩范围；report-only reviewer 不得 process mutation/kill。
先做：要求 assigned execution agent 按 evidence budget 运行最小充分 checker。`E0-review` 由指定 reviewer 完成 verified no-persist
exact identity/source/diff/static reasoning；`E0-checker-evidence` 若需 checker 由 assigned execution agent 执行。
E1/E2/E3/E4 的 deterministic/unit/known-answer/boundary/integration/build/regression/benchmark/training/profiling
均由 assigned execution agent 执行；reviewer 是否运行行为性检查以其 brief permissions 为准。E2–E4 必须记录
WHY-RED、expected cost、下层不足、red/green 证明；相同 exact head 的有效 evidence 不重跑。
Finding schema（逐项）：finding_id、severity、label、attribution、location、contract_clause、counterexample、impact、
smallest_acceptable_outcome、acceptance_check、blocker_admission、admission_evidence_ref。统一规则：
`REQUEST_CHANGES iff exists(active severity=P1 OR label=BLOCKING)`；`APPROVE iff review_status=COMPLETE && coverage_status=COMPLETE &&
unreviewed_scope=empty && no active P1 && no BLOCKING`。P1 必须 BLOCKING；P2/P3 不得 BLOCKING；P1 不得 NON_BLOCKING/NIT/QUESTION/FOLLOW_UP/CONTRACT_CHALLENGE；
P2/P3 可携带非阻塞标签，合同挑战本身不作为 BLOCKING。
攻击：边界/精确等价/测度零是否构造；容差分辨率；分母；独立 oracle/变异；数据、单位、frame、schema、
权限、恢复；测试本身是否会在目标错误时变红（WHY-RED）。
输出：review_status=COMPLETE|INFRA_BLOCKED；verdict=REQUEST_CHANGES|APPROVE|null；另列 readiness_decision=GO|null。
       status=INFRA_BLOCKED 时 verdict 必须 null 且不得 approval event；coverage_status=PARTIAL 时 verdict 必须 REQUEST_CHANGES；
       `REQUEST_CHANGES iff exists(active severity=P1 OR label=BLOCKING)`；`APPROVE iff review_status=COMPLETE && coverage_status=COMPLETE &&
       unreviewed_scope=empty && no active P1 && no BLOCKING`。P1 必须 BLOCKING；P2/P3 不得 BLOCKING；P1 不得 NON_BLOCKING/NIT/QUESTION/FOLLOW_UP/CONTRACT_CHALLENGE。
       GO 只属于 readiness_decision。返回 findings（逐 finding admission/evidence/ref）、identity checks（含 hash/byte/line/drift）、evidence envelopes
       及机械匹配结果、coverage/prior-current scope、closure matrix、GitHub state（如 PR workflow）、
       status/verdict、未验证与残余风险；严格不写补丁、不做外部动作。授权 PR workflow 每轮必须回写不可变
       review/comment（round ID、exact head、pinned contract、checks run/skipped/reused+denominator/cost、
       findings/admission evidence、作者 disposition、closure matrix、最终 decision record）；线下裁决不得成为唯一历史。
~~~

## Sol planning / dispatch checklist

~~~text
Mission target and stop condition:
Source of truth and direct consumers:
assigned_model / role / reasoning_effort and one owner:
Exact paths and commands authorized:
Data/config/concurrency/log/timeout/cleanup ownership:
Dispatch/stop are control-plane only; assigned execution agent self-terminates on fixed conditions; forced cleanup requires execution-agent ownership proof:
Verification level, expected denominator, WHY-RED and falsifiable counterexample:
EVIDENCE-BUDGET: E0-review=assigned reviewer (report-only only when brief says so); E0-checker-evidence/E1-E4=assigned execution agent;
E1-E4 require WHY-RED, expected cost, known denominator, red/green proof; E2-E4 additionally require lower-level insufficiency:
First-round coverage: coverage_status=PARTIAL|COMPLETE; prior/current reviewed_scope; prior/current unreviewed_scope; monotonic union/shrink:
Per-finding blocker admission/evidence/ref; monotonic closure; delta-only rereview Git old_head..new_head or Non-Git snapshot hash-set content delta:
Review identity mode (Git exact base/head or non-Git exact-snapshot ID/hash set):
Review gate (Independent Sol exact identity, fresh zero-context, xhigh, fork_turns=none):
EXEC_INFRA_BLOCKED and other escalation conditions:
DONE-1: review gate APPROVE iff review_status=COMPLETE + coverage COMPLETE + unreviewed empty + no active P1/BLOCKING;
readiness_decision=GO is a separate later predicate requiring APPROVE + affected evidence/EVID + readiness conditions.
~~~

## V15 nested specialist extension

Every mission packet must freeze milestone, objective, owner, operating domain, invariants, non-goals, exact scope, evidence budget, rollback, assigned model/role/permissions, and Independent Sol reviewer separation. Nested packets use `schema=delegation.v1`, `max_depth=1`, depth `1`, exclusive path leases, no Git/GitHub/review/merge permissions, `active_mission_lock=true`, `plugin_inventory=informational`, and `retry_budget.semantic_contamination=1`. Results must link parent/child/model/task identity, changed paths within lease, count arithmetic, retry transcript, and contamination state. Any mismatch is `NESTED_CHILD_CONTRACT_REJECTED`.
