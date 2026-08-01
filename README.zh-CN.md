# Codex Governance Infrastructure v16

简体中文 · [English](README.md)

这是一套面向 **OpenAI Codex 用户**的可移植、隐私安全治理基础设施，目标是在不降低正确性、
证据质量和授权边界的前提下，加快开发与审查。

仓库打包了 Codex 全局规则、任务合同、affected-first 证据门、确定性工具路由、有界
multi-agent 审计、风险路由的独立审查、隐私安全 hooks，以及可复现的 presubmit。它只面向
Codex，不声称兼容 Claude Code、Kimi Code、Zcode 或其他 agent runtime。

> **预览状态**
>
> V16 目前适合源码审阅、隔离安装、确定性验证和受控团队试用。安装器会替换传入的目标目录，
> 因此试用时只能指向新建的隔离 `CODEX_HOME`，绝不能指向正在使用的 `~/.codex`。正式接入
> 必须另走经过审查的合并/部署流程，并保留本机认证、sessions、plugins、memories 和机器专属
> 配置。

## 为什么需要它

Coding-agent 治理经常向两个极端滑落：

- 规则太松，结果很快，但缺乏证据、不可追溯，甚至不安全；
- 规则太重，每轮审查都重新 build、重新扫全仓、重复旧工作，消耗大量时间和 token，却没有
  改善最终判断。

V16 把正确性和有效证据作为硬门，然后依次优化：

1. 得到正确判断或正确合并的时间；
2. 单调收敛的审查闭环，避免重复全量审查；
3. token 和调用成本。

设计坚持 evidence-based：任何绿色结论都必须可证伪、当期、可独立核验，并且分母已知且大于
零。skip、陈旧、复制、未知、NaN/Inf 或 identity 不匹配的证据都不能算通过。

## 你会得到什么

| 层 | 能力 |
|---|---|
| 全局规则 | `codex/AGENTS.md` 定义授权、证据、Git、审查、模型角色和工具路由边界。 |
| 任务 brief | `codex/BRIEF-TEMPLATES.md` 把任务固化为 scope、owner、model、permissions、invariants、non-goals、gates、budget 和 stop conditions。 |
| 合同 | `codex/v16/contracts.py` 与 registry 严格验证 mission、evidence、review、lineage、runtime 和 metrics。 |
| 执行引擎 | FAST、CANDIDATE、FINAL 对精确 identity 运行 content-addressed affected gates。 |
| 审查引擎 | 每个任务只有一个风险路由的正式独立 reviewer；稳定修复由原 reviewer 做 delta-only 闭环。 |
| 工具路由 | 已知结构用 CodeGraph，语义发现用 Semble，精确文本用 `rg`，进入模型上下文的 shell 输出用 `rtk`。 |
| Hooks | Session 与 pre-tool hooks 提供有界上下文和隐私安全 receipt，不记录 prompt、原始参数、cwd、token 或凭据。 |
| 验证 | manifest verifier、单元/负向 fixtures 和完整 presubmit 让包级结论可复现。 |
| 隐私 | 包内排除 sessions、凭据、tokens、receipts、plugin/cache 状态、model cache、memories 和用户数据。 |

## 这个仓库不会做什么

- 不安装或登录 Codex。
- 当 Codex control plane 或账号没有暴露某模型时，不会凭空让该模型可用。
- 不 vendoring 或静默安装 CodeGraph、Semble、`rtk`、`rg`。
- 不切换 GitHub 账号，不创建 PR，不 approve，不 merge。
- 不复制 sessions、memories、plugins、connections、tokens 或机器专属配置。
- 目前不提供对现有 live `~/.codex` 的安全一键 overlay。
- 不替代仓库本地 `AGENTS.md`、测试、领域合同或项目 ownership。

## 前置条件

源码与隔离试用所需：

- Git；
- Python 3.9 或更高版本；
- 后续交互使用所需的 OpenAI Codex 安装。

在工作站正式接受完整工具路由合同前，还必须具备：

- 与目标 revision 匹配、属于目标 child repo 的 CodeGraph 能力；
- 当前可用的 Semble agent/MCP 能力；
- `PATH` 中可用的 `rtk`；
- `PATH` 中可用的 `rg`。

本包的 Python 实现只使用标准库。当前试用已在 Linux 验证；其他平台在安装器、hooks、路径、
权限和进程行为被明确测试前，都应标记为尚未验证。

Codex 官方资料：

- [Codex CLI](https://developers.openai.com/codex/cli)
- [Codex 配置基础](https://developers.openai.com/codex/config-basic)
- [Codex 配置参考](https://developers.openai.com/codex/config-reference)
- [AGENTS.md 与定制](https://developers.openai.com/codex/concepts/customization)
- [Hooks](https://developers.openai.com/codex/config-advanced#hooks)

## 五分钟安全试用

### 1. Clone 预览分支

```bash
git clone \
  --branch codex/v16-productivity-engine \
  --single-branch \
  https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra
git rev-parse HEAD
git status --short
```

记录完整 40 位 commit。审查和证据只对实际测试过的精确 snapshot 有效。

### 2. 安装前验证源码包

```bash
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

verifier 必须输出 `"status": "GREEN"` 且 errors 为空；测试必须是零 failure、零 error。manifest
不匹配或 privacy scan 失败时不得安装。

### 3. 预览安装计划

```bash
GOV_TRIAL_ROOT="$(mktemp -d)"
GOV_TRIAL_HOME="$GOV_TRIAL_ROOT/.codex"
mkdir -p "$GOV_TRIAL_HOME"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --dry-run
```

dry-run 输出 JSON 计划，其中包含目标占位符、文件数和每个 managed file 的 SHA-256；它不会写
目标目录。

### 4. 只安装到隔离 trial home

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME"

test -f "$GOV_TRIAL_HOME/AGENTS.md"
test -f "$GOV_TRIAL_HOME/BRIEF-TEMPLATES.md"
test -f "$GOV_TRIAL_HOME/hooks/hooks.json"
test -f "$GOV_TRIAL_HOME/v16/contracts.py"
test ! -e "$GOV_TRIAL_HOME/codex"
```

仓库 `codex/` 下的内容会被正确展开到隔离 `CODEX_HOME` 根目录。仓库贡献规则、文档、测试、Git
状态和生成的 `__pycache__` 不会被安装。

### 5. 在不暴露真实任务数据的情况下 smoke-test hooks

```bash
GOV_RECEIPTS="$GOV_TRIAL_ROOT/receipts"
mkdir -p "$GOV_RECEIPTS"

CODEX_HOOK_SOURCE=test \
CODEX_HOOK_RECEIPT_DIR="$GOV_RECEIPTS" \
python3 "$GOV_TRIAL_HOME/hooks/session_context.py" </dev/null

printf '%s\n' '{"tool_name":"rg","model":"trial"}' |
  CODEX_HOOK_SOURCE=test \
  CODEX_HOOK_RECEIPT_DIR="$GOV_RECEIPTS" \
  python3 "$GOV_TRIAL_HOME/hooks/pre_tool_use_policy.py"
```

预期结果：

- `session_context.py` 返回有界 V16 context 和
  `"receipt_status": "success"`；
- `rg` 探针返回 `"decision": "allow"` 和 route `"rg"`；
- receipt 只写入临时试用目录；
- 整个过程不提供真实 prompt、command args、cwd、credential 或 session ID。

### 6. 回滚隔离安装

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --rollback
```

rollback 会恢复 sibling `.codex.v16-backup` 中保存的目标目录状态。它属于隔离 installer 测试，
不能替代经过审查的 live-home migration。

## 不要直接安装到正在使用的 Codex home

预览阶段明确不支持下面的操作：

```bash
# 试用阶段不要运行。
python3 scripts/install-governance.py --source . --codex-home "$HOME/.codex"
```

安装器执行目标目录替换；对 active home 运行会暂时替换无关 Codex 状态。正式部署至少必须保留并
核验：

- 认证和账号状态；
- `config.toml` 与本机 project trust；
- 已安装 plugins、skills、MCP servers 和 connector state；
- sessions、memories、caches 和 shell state；
- 既有 hooks 与本地自有规则；
- 文件 owner、mode、rollback identity 和 active Codex process。

在专用 overlay installer 具备独立 Acceptance Lock、负向 fixtures、独立审查和 rollback proof
前，只使用隔离流程。

## 运行模型

### 模型角色按任务路由，不按名称封禁能力

所有模型都受相同授权和证据规则约束。mission 只能从当前 Codex runtime 实际可用且已授权的模型
中选择 writer。V16 不会假装编辑本地 model list 就能获得 provider 或 control-plane 权限。

默认正式审查路由：

| 冻结风险 | 正式 reviewer | Context |
|---|---|---|
| Low / medium | `gpt-5.6-terra`，high effort | fresh `independent_clean_room` |
| High / unresolved | `gpt-5.6-sol`，xhigh effort | fresh `independent_clean_room` |
| COMPLETE coverage 后的稳定修复 | 原 reviewer 与原 model，high effort | `delta_continuation` |

高风险包括数学/数值、exact parity、安全/隐私、公共合同、schema/data format、不可逆迁移、
supply-chain 或 installer、生产 runtime、正式科研/发布，以及 hook/reviewer/model-routing 治理。

当 Spark 确实可用时，V16 可选择 0–3 个有界、report-only 的
`gpt-5.3-codex-spark` 内部审计。它们用于正式 gate 前发现风险，不能 approve、merge，也不能替代
唯一的 Independent reviewer。

### 审查收敛

首次正式审查收到的是紧凑、hash-bound clean-room packet，而不是作者完整聊天。它只审精确
diff/snapshot、直接依赖、affected tests、evidence denominators、invariants、non-goals、
limitations 与 Acceptance Envelope。

普通修复保持 reviewer continuity，只提供：

- old/new exact identities；
- exact delta；
- prior findings 与作者 dispositions；
- 新证据或复用证据；
- 直接受影响边界。

只有明确升级条件才更换 fresh reviewer：contract/domain/scope drift、material rewrite、
independence 或 lineage 丢失、新可证伪 P1、两轮不收敛、governance 变化或 evidence identity
失效。

`APPROVE` 必须同时满足 COMPLETE coverage、unreviewed scope 为空、无 active P1/`BLOCKING`、
lineage 匹配且 Independent artifact 匹配。`APPROVE` 不是 merge 授权，也不等于 readiness `GO`。

### Affected-first 证据

| Stage | 用途 |
|---|---|
| FAST | 只运行会被当前改动打红的小型 targeted checks。 |
| CANDIDATE | 在干净 exact candidate 上运行冻结 affected route 的其余检查。 |
| FINAL | 补齐仍需的 fresh portability evidence，再运行唯一正式 review gate。 |

每个可执行检查都必须声明 WHY-RED、预计成本、分母，以及 red/green 分别证明什么。有效 evidence
按内容寻址，只有完整 identity 保持一致时才能复用。

### 工具路由

| 意图 | 首选工具 | 关键边界 |
|---|---|---|
| 已知 symbol、call、dependency、impact | CodeGraph | 使用 revision-matching child-repo index；build/sync 索引属于需授权 mutation。 |
| 未知语义入口、相似实现 | Semble | 结果只是候选召回；重要结构必须回到 source 或 CodeGraph 核验。 |
| 精确 string、error、config、log | `rg` 或有界精确读取 | 用于 literal truth，不能冒充语义或依赖证据。 |
| 展示给模型的 shell output | `rtk` | raw output 只保留给下游 machine input 或 exact denominator。 |

只有首选工具真实失败/不可用，并记录稳定 reason code 与 evidence reference 后才能 fallback；
fallback 不得声称提供了等价语义或结构覆盖。

## 完整仓库验证

只在冻结、干净的 candidate 上运行完整 presubmit：

```bash
git status --short
python3 scripts/presubmit.py --repo .
```

presubmit 会编译 mission、运行正向和强制负向 contracts、检查 gate ordering 与 identity drift、
验证 evidence arithmetic/privacy、渲染 sanitized trace、派生 metrics，并验证 fresh archive。它
不会调用 GitHub，也不会切换 GitHub identity。

文档类 working-tree 改动先运行 affected checks：

```bash
python3 scripts/verify-governance.py --repo .
python3 -m unittest tests.test_installer tests.test_privacy -v
git diff --check
```

manifest 是 exact path-and-hash 边界。任何 tracked file 增、删、内容变化，都必须同步更新
manifest，verifier 才能为 GREEN。

## Hooks 与 receipts

包内包含：

- `SessionStart` 与 `SubagentStart` context generation；
- `PreToolUse` allow/deny 与 normalized route signal；
- nested delegation 的 parent pre-dispatch、subagent mission-lock、post-result 和 dispatch
  transcript 检查；
- best-effort、privacy-safe JSONL receipts。

receipt 包含 normalized event/model/tool/decision/reason code、combined hook snapshot hash、
source、PID/PPID 和 hashed identifiers。它排除 raw prompt、tool args、cwd、token、credential
和 private identifier。receipt 写入失败必须保持可见，也不能支撑 runtime-proof acceptance。

在 Codex 中 trust hook 前，先审阅 exact commit 和 hook source。trusted hook 或全局规则变化后，
启动新的 Codex task；旧 task 可能仍保留创建时上下文。

## 安全与隐私

绝不能加入：

- API key、GitHub token、OAuth state、cookie 或 provider credential；
- Codex session、prompt、history、transcript、memory 或 shell snapshot；
- hook receipt JSONL；
- plugin cache、connection、model cache、browser profile 或用户数据；
- 个人绝对路径或私有仓库内容。

分享改动前运行：

```bash
python3 scripts/verify-governance.py --repo .
git diff --check
```

详见 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md) 和
[privacy threat model](docs/privacy-threat-model.md)。

## 更新 trial clone

每次更新都视为新的 evidence identity：

```bash
git fetch origin
git status --short
git log --oneline --decorate HEAD..origin/codex/v16-productivity-engine
git pull --ff-only
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

不要在未审阅时覆盖本地改动。commit、path set、manifest、hook snapshot、Acceptance Envelope、
command、runtime 或 denominator 变化后，不得复用旧 evidence。Codex CLI、Codex Desktop 与远程
app-server 的刷新生命周期可能不同；本仓库不管理它们的升级或 model catalog。安装治理变更后，
重启实际受影响的 Codex surface 并开启 fresh task。

## 故障排查

| 现象 | 含义 | 处理 |
|---|---|---|
| `Unknown model ...` | 当前 runtime/catalog 没有暴露该模型。 | 使用 frozen mission 允许的现有模型；若合同要求 exact model，则标记 infrastructure blocker。本仓库不能授予模型权限。 |
| Luna 或 Spark 只在部分 surface 出现 | CLI、Desktop、远程 host 或 app-server catalog state 不一致。 | 在精确 host/surface 检查 catalog，刷新对应进程，再运行 fresh exact probe。 |
| Hook 未加载 | surface 尚未 trust/reload，或 hook 配置不同。 | 审阅 source，trust hook，重启对应 Codex surface 并开启新 task。 |
| `receipt_status=write_failed` | runtime receipt 持久化失败。 | 检查隔离 receipt dir、ownership、permissions 和 no-follow 约束；不得宣称 runtime-proof acceptance。 |
| CodeGraph 缺失或陈旧 | structural evidence 不可用或 revision 不匹配。 | 获得授权后 build/sync child-repo index，再查询该索引。 |
| Semble 为 `unknown` | CLI probe 不能证明 MCP/agent capability。 | 由 orchestrator 提供当期 capability observation，不能静默当作已安装。 |
| Manifest verifier 为 RED | tracked path/hash、privacy、UTF-8 或 required file 失败。 | 停止安装，逐项检查错误，经审查更新 package 与 manifest 后重跑。 |
| FINAL/presubmit 拒绝 dirty tree | 未满足 frozen clean identity。 | 开发阶段使用 affected checks；FINAL 前准备 clean candidate。 |
| Review 每轮重新扫全仓 | review packet 或 continuity mode 错误。 | 冻结单一 exact scope；普通修复用 `delta_continuation`，只在声明触发器出现时升级。 |

## 试用验收清单

一次 teammate trial 只有记录完所有适用项才算成功：

- exact repository commit 与 clean/dirty state；
- verifier status 与文件分母；
- unit-test total、failures、errors、skips、expected failures；
- isolated installer 文件数与目标目录；
- hook smoke-test decision 与 receipt status；
- CodeGraph、Semble、`rtk`、`rg` 的当期 availability/health；
- 被测试 Codex surface 实际暴露的模型；
- limitations、unknowns 与 rollback 结果。

源码存在不代表 runtime routing 真正发生；本地 review 成功不等于 GitHub approval；synthetic
fixture 绿色不等于生产或科研结论。

## 仓库结构

```text
.
├── codex/
│   ├── AGENTS.md
│   ├── BRIEF-TEMPLATES.md
│   ├── hooks/
│   ├── contracts/
│   └── v16/
├── docs/
├── scripts/
│   ├── install-governance.py
│   ├── verify-governance.py
│   └── presubmit.py
├── tests/
├── manifest.json
├── SECURITY.md
└── PRIVACY.md
```

详细设计文档：

- [Architecture](docs/architecture.md)
- [Deployment model](docs/deployment.md)
- [Review workflow](docs/review-workflow.md)
- [V16 contract registry](codex/v16/contracts/README.md)

## 贡献与发布政策

- 改动必须 small、coherent、portable、privacy-safe。
- 使用仓库 exact manifest 与 mandatory negative fixtures。
- `Qian9921` 负责开发 commit 与 PR authoring。
- `Liang9921` 负责 independent governance review、approval 与 merge。
- 作者不能 review 或 approve 自己的改动。
- 不得直推 `main`。
- dirty tree、incomplete review、unknown denominator 或 stale evidence 不能发布。

当前包是 private preview。公开发布需要独立的 security、privacy、license、binary/artifact、
portability、support 与 documentation review。

## License

见 [LICENSE](LICENSE)。
