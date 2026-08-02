# Codex Governance Infrastructure v16

简体中文 · [English](README.md)

这是一套只面向 Codex 用户的 starter：在不牺牲正确性、证据和 GitHub 审批边界的前提下，加快
开发与审查。

V16 提供：

- 可持续的 Codex 全局规则与任务 brief；
- affected-first 测试，而不是动不动全量 rebuild；
- CodeGraph、Semble、`rtk` 的强制就绪检查与实际使用证据；
- 单一风险路由的独立 reviewer，以及同 reviewer 的 delta-only 复审；
- privacy-safe hook receipts；
- 确定性 package verifier 和隔离试用安装器。

它不声称兼容 Claude Code、Kimi Code、Zcode 或其他 agent runtime。

> **安全边界**
>
> 安装器会替换传入的目标目录。只能使用新建的隔离 `CODEX_HOME`，绝不能指向正在使用的
> `~/.codex`。仓库不会复制 credential、session、memory、plugin、connection、model cache 或
> 其他私人数据。

## 师兄师姐十分钟上手

### 1. Clone 并验证

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

git rev-parse HEAD
git status --short
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

只有 verifier 输出 `"status":"GREEN"`，且测试为零 failure、零 error 时才继续。

### 2. 缺少工具时安装

安装软件前先审阅 upstream 指令：

```bash
# CodeGraph
npm install -g @colbymchenry/codegraph

# Semble
uv tool install semble

# rtk
cargo install --git https://github.com/rtk-ai/rtk
```

Upstream：

- [CodeGraph](https://github.com/colbymchenry/codegraph)
- [Semble](https://github.com/MinishLab/semble)
- [rtk](https://github.com/rtk-ai/rtk)

### 3. 配置 Codex

```bash
codegraph install --target codex --location global --yes
semble install --agent codex --type mcp --yes

rtk init --codex --global --dry-run
rtk init --codex --global
```

Semble 命令只安装 MCP，避免再注入一大段重复规则。审阅配置变化后，重启真正受影响的 Codex
CLI、Desktop 或 app-server，并开启新任务。

### 4. 准备当前仓库的 CodeGraph 索引

```bash
codegraph status --json .
```

仓库尚未初始化且已授权索引时：

```bash
codegraph init .
```

结构改动后，仅在已授权时同步：

```bash
codegraph sync .
```

索引只属于 owning repository，绝不能把父 workspace 的图当作 child repo 真值。

### 5. 运行严格 toolchain doctor

```bash
python3 scripts/toolchain-doctor.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

唯一通过条件是退出码 `0`、`"status":"ready"`、分母 `3/3`：

- CodeGraph 已配置、绑定当前 repo、索引完整且 fresh，并能找到预期当前源码；
- Semble 已配置、可调用、repo scope 正确，语义 query 能返回预期源码；
- `rtk` 能复现当前 Git identity，并保持确定性失败命令的非零退出码。

binary 存在不等于就绪。doctor 只读，只保存 hash/reason code，不保存 raw output、绝对路径、
prompt、环境变量或 credential。

### 6. 在隔离目录试用治理包

```bash
GOV_TRIAL_ROOT="$(mktemp -d)"
GOV_TRIAL_HOME="$GOV_TRIAL_ROOT/.codex"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --dry-run

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME"

CODEX_HOOK_SOURCE=test \
CODEX_HOOK_RECEIPT_DIR="$GOV_TRIAL_ROOT/receipts" \
python3 "$GOV_TRIAL_HOME/hooks/session_context.py" <<'JSON'
{"hook_event_name":"SessionStart","model":"trial"}
JSON
```

预期：

- dry-run 给出 managed file denominator；
- 所有安装文件只在隔离目录；
- hook 输出包含 `"receipt_status":"success"`；
- live Codex home 没有被修改。

Rollback：

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --rollback
```

只有之前目标目录确实被备份时才能 rollback。

### 7. 给 Codex 复制这一段

```text
阅读这个仓库的 README 和 AGENTS.md。验证 package；检查当前
CodeGraph/Semble/rtk 配置；只有获得我的授权才准备 owning-repo CodeGraph
索引；选择一个仓库专属 semantic sentinel 并运行 strict toolchain doctor；
复用当期 preflight receipt。任务中：未知语义入口用 Semble，已知结构/影响
用 CodeGraph，进入上下文的 shell output 用 rtk，精确文本用 rg，hash/parser/
精确 denominator 用 raw command。记录带 receipt 的实际工具使用；不要做无关
打卡调用。
```

师兄师姐只需完成 clone、审阅授权，剩余步骤可以交给 Codex 驱动，同时所有 mutation 和失败都
保持可见。

## “强制工具”到底是什么意思

它包含两道不同的门。

### Gate 1：就绪

`tool-preflight.v16` 绑定当前 host/runtime、工具版本、Codex config、repo root、Git head、
worktree、CodeGraph index 和 semantic sentinel。任一 identity 变化都使旧 receipt 失效。

### Gate 2：实际使用

`tool-usage.v16` 把每条 declared route 绑定到成功且与任务相关的调用、evidence reference 和
privacy-safe hook receipt hash。

| 任务意图 | 强制路由 |
|---|---|
| 未知语义入口或相似实现 | Semble |
| 已知 symbol、call、dependency 或 blast radius | CodeGraph |
| 展示给模型的 shell output | `rtk` |
| 精确 string、error、config 或 log | `rg`/有界精确读取 |
| hash、parser input、byte identity、精确 denominator | raw command |

无关地把每个工具调用一次属于违规。只有 preferred tool 真实失败，并留下 reason code 和
evidence reference 后才能 fallback；fallback 不得冒充等价语义或结构覆盖。

详细合同与修复方法：
[强制工具链详细说明](docs/TOOLCHAIN.md)；本页保留中文快速上手流程。

## 开发和审查模型

1. 冻结 objective、scope、invariants、non-goals、exact identity 和 evidence budget。
2. 只运行具备具体 WHY-RED 与已知 denominator 的 affected checks。
3. 每个任务只有一个 independent reviewer：
   - 低/中风险：Terra high；
   - 高风险：fresh Sol xhigh。
4. 稳定修复由同一 reviewer 做 delta-only 闭环。
5. coverage complete、unreviewed scope 为空、无 active P1/`BLOCKING`、evidence 与 exact head
   匹配时才 approve。
6. 使用 expected-head/match-head guard 合并。

正确性与证据是硬门。第一优化目标是得到正确判断或正确合并的时间，token/call cost 排第二。

## 完整验证

开发中先跑最小 affected checks。冻结 clean candidate 后运行：

```bash
git status --short
python3 scripts/presubmit.py --repo .
git diff --check
```

manifest 是精确 tracked path/hash 边界。新增、删除或修改 tracked 文件后必须同步 manifest。

## 隐私与限制

绝不能提交：

- API/GitHub token、OAuth state、cookie 或 credential；
- Codex session、prompt、transcript、memory 或 receipt；
- plugin/connection/model cache 或 browser profile；
- 个人绝对路径或私有仓库内容。

本包不能授予当前 Codex surface 没有暴露的模型或工具。CLI、Desktop、远程 host 和 app-server
可能在不同生命周期刷新。治理、MCP、hook 或 model-routing 变化后，重启对应 surface 并开启新任务。

详见 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md) 和
[privacy threat model](docs/privacy-threat-model.md)。

## 故障排查

| 现象 | 处理 |
|---|---|
| `CODEGRAPH_WRONG_PROJECT` | 停止，把 doctor/query 指向 owning child repo。 |
| `CODEGRAPH_STALE` | 审阅变化，授权后运行 `codegraph sync .`。 |
| `SEMBLE_MCP_NOT_CONFIGURED` | 运行已审阅的 Semble MCP 配置命令并重启 Codex。 |
| `SEMBLE_SENTINEL_MISMATCH` | 改善 semantic query 或修复 repo/index scope；不得宣称 ready。 |
| `RTK_FALSE_GREEN` | 硬停止；修复前不得接受 shell evidence。 |
| `receipt_status=write_failed` | 修复私有 receipt 目录；runtime-proof acceptance 被阻塞。 |
| `Unknown model ...` | 检查精确 host/surface catalog；本仓库不能授予模型权限。 |
| Manifest verifier 为 RED | 停止，逐项检查，经审查更新后重跑。 |

## 仓库结构

```text
codex/                     可安装治理包
  AGENTS.md
  BRIEF-TEMPLATES.md
  hooks/
  v16/
docs/TOOLCHAIN.md          工具就绪与路由详细合同
scripts/toolchain-doctor.py
scripts/install-governance.py
scripts/verify-governance.py
scripts/presubmit.py
tests/
manifest.json              精确 tracked path/hash 边界
```

## Codex 官方资料

- [Codex CLI](https://developers.openai.com/codex/cli)
- [配置基础](https://developers.openai.com/codex/config-basic)
- [配置参考](https://developers.openai.com/codex/config-reference)
- [AGENTS.md 与定制](https://developers.openai.com/codex/concepts/customization)
- [Hooks](https://developers.openai.com/codex/config-advanced#hooks)
