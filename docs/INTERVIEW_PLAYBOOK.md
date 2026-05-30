# miniCC 面试背诵文档

本文档给你自己看，用来把 miniCC 包装成一个能写进简历、能经得住追问的 Agent Harness 项目。

## 1. 简历项目一句话

```text
miniCC 是一个 Bash-first CodeAct Agent Harness 工程化平台，使用 Python + uv 构建，基于 OpenAI-compatible 模型接口接入硅基流动/DeepSeek，通过 Docker Sandbox 隔离执行环境，并实现结构化 action 协议、Policy Middleware、上下文压缩、prompt cache 友好拼装、trace 观测和 eval 回归评测。
```

更短版：

```text
实现了一个面向 Coding Agent 的 Bash-first CodeAct Harness，用 Docker 沙箱、策略中间件、上下文治理和评测闭环，把模型的 bash 行动能力安全、可观测、可回归地落地。
```

## 2. 项目不是在做什么

面试时先把边界讲清楚，会显得你很懂 Agent 工程。

```text
我不是在训练模型，也不是堆一个 LangChain 工作流。
我做的是 Agent Harness：给模型提供可行动作空间、执行环境、安全边界、上下文、观测和评测。
```

可以补一句：

```text
Agency 来自模型，Harness 决定这个 agency 能不能在真实工程环境里稳定、安全、可复现地发挥出来。
```

## 3. 为什么选择 Bash-first CodeAct

标准回答：

```text
我把 Bash 作为统一 action space，而不是设计很多碎片化工具。原因是 coding agent 的很多任务本质上都能落到 shell、文件系统、测试命令和包管理器上。Bash-first CodeAct 保留了模型的通用规划和组合能力，harness 则负责管住执行边界。
```

补充技术点：

```text
CodeAct 和 ReAct 是同一层的行为范式。
ReAct 强调 reasoning + action 交替。
CodeAct 强调用可执行代码表达 action。
tool calling 只是 action 的通信承载方式，不是和 CodeAct 并列的范式。
```

如果被问“为什么不用传统 tool calling”：

```text
不是不用 tool calling，而是 action space 设计成 Bash-first。它可以用纯 JSON、tool calling 或函数调用来承载。miniCC 第一版用严格 JSON action 协议，是为了可解析、可审计、可重试。
```

## 4. 总体架构怎么讲

背这个流程：

```text
用户输入任务后，miniCC 会创建 run_id，复制 workspace，启动一个临时 Docker 容器。
模型每轮输出一个结构化 action，只有 bash、ask、final 三类。
bash action 先经过 Policy Middleware，再进入 Docker Sandbox 执行。
执行结果被标准化成 Observation：成功、空输出、错误、超时、策略拒绝、大输出 artifact 等。
Observation 进入 trace，同时以 preview 的形式回到上下文。
上下文超过预算时，把旧轨迹压缩成 state_summary，大结果只保留 artifact 引用。
任务结束后销毁容器，但保留 trace、diff、artifact 和 metrics。
```

## 5. Harness 能力层背诵表

### L01 Provider Adapter

这一层解决：

```text
模型厂商和聚合站接口不一致，core loop 不应该和某一家 SDK 耦合。
```

怎么做：

```text
优先实现 OpenAI-compatible adapter，兼容硅基流动、DeepSeek 等聚合站。
统一返回 ModelResponse，包括 text、raw、usage、latency。
usage 里归一化 prompt_tokens、completion_tokens、cached_tokens、prompt_cache_hit_tokens、prompt_cache_miss_tokens、cache_hit_rate。
非流式响应通常直接读取 usage。
流式响应要支持 stream_options.include_usage=true，并从最后的 usage chunk 合并统计。
```

面试追问：

```text
不同厂商 prompt cache 一样吗？
```

回答：

```text
原则类似，都是复用稳定前缀的 KV/cache，但控制方式和 usage 字段不同。DeepSeek 更偏默认 context caching，并会在 usage 中返回 prompt_cache_hit_tokens 和 prompt_cache_miss_tokens；OpenAI 常见是 automatic prefix caching，并可从 prompt_tokens_details.cached_tokens 观察；Anthropic 支持显式 cache_control breakpoint。硅基流动这类聚合站也可能透出 prompt_cache_hit_tokens、prompt_cache_miss_tokens 或 cached_tokens。

我的 core 不绑定某家专属参数，而是在 prompt layout 上保证 cache-friendly，并在 provider adapter 层归一化这些 usage 字段。如果是流式调用，还要设置 stream_options.include_usage=true 才能拿到最后的 usage 统计。
```

### L02 结构化 Action 协议

这一层解决：

```text
模型自由输出 Markdown bash 块容易解析歧义，也不方便区分执行、澄清和结束。
```

三类 action：

```json
{ "type": "bash", "command": "pytest -q", "timeout_sec": 60, "purpose": "run tests" }
{ "type": "ask", "question": "需要允许联网安装依赖吗？" }
{ "type": "final", "answer": "任务完成，测试已通过。" }
```

追问：

```text
如果模型输出格式错了怎么办？
```

回答：

```text
不会直接崩溃。harness 会生成 protocol_error observation，指出 JSON 解析失败或字段缺失，让模型按协议重试。连续协议错误超过阈值才终止，并在 trace 里标记 failure_type=protocol。
```

### L03 RunState / TaskState

这一层解决：

```text
长任务、审批暂停、恢复执行、eval 复现都需要一个稳定状态，而不是只依赖对话历史。
```

包含：

```text
goal
status
current_plan
constraints
open_questions
approvals
artifacts
metrics
state_summary
```

追问：

```text
为什么不用 memory 记任务状态？
```

回答：

```text
临时任务状态属于 RunState，不属于 Memory。Memory 只存长期稳定的用户反馈规则。这样可以避免 memory 污染，也让任务恢复和 eval 复现更确定。
```

### L04 Minimal Agent Loop

这一层解决：

```text
把模型调用、action 解析、执行、observation 回传组织成稳定闭环。
```

一句话：

```text
Loop 只负责编排，不承载具体安全策略、Docker 细节和 prompt 拼接细节。
```

伪代码背诵：

```text
build prompt -> call model -> parse action
if final: stop
if ask: pause
if bash: policy -> sandbox exec -> observation -> append context -> next turn
```

### L05 Docker Sandbox

这一层解决：

```text
Bash-first CodeAct 很强，但 bash 是真实执行能力，必须有硬隔离。
```

怎么讲 Windows：

```text
Windows 下可以用 Docker Desktop + WSL2 backend。PowerShell 里调用 docker CLI，但容器内运行的是 Linux bash。这样 coding agent 的执行环境更接近 CI 和服务器。
```

核心设计：

```text
每个 run 一个独立 Docker container
workspace 复制后挂载到 /workspace
默认 --network none
限制 cpus、memory、pids
任务结束 docker rm -f
保留 trace、artifact、diff
```

追问：

```text
Docker 解决了所有安全吗？
```

回答：

```text
没有。Docker 解决的是进程和系统层隔离，但还要配合 workspace copy、资源限制、network policy、cap-drop、no-new-privileges 和命令策略。真正的安全边界是 sandbox + policy（不准联网、限制内存、不给特权） + artifact 审计组合出来的。
```

### L06 Workspace 与 Diff

这一层解决：

```text
不能直接污染用户原始项目，任务结束还要能审计 Agent 改了什么。
```

做法：

```text
复制 workspace 到 .minicc/runs/<run_id>/workspace
忽略 .git、.venv、node_modules、.minicc
在副本里初始化 git
结束时生成 diff.patch
```

可背：

```text
Docker 管进程隔离，workspace copy 管文件隔离，git diff 管结果审计。
```

### L07 Policy Middleware

这一层解决：

```text
权限和安全控制不能散落在执行器里，要插件化、可组合、可观测。
```

核心抽象：

```text
Action -> PolicyChain -> Decision
Decision = allow | deny | require_approval | rewrite
```

第一版 policy：

```text
CommandPolicy: 拦危险命令
PathPolicy: 拦敏感路径
NetworkPolicy: 控制联网
BudgetPolicy: 控制轮数、命令数、超时
ApprovalPolicy: 高风险动作转人工审批
```

追问：

```text
deny 和 command error 有什么区别？
```

回答：

```text
command error 是命令实际执行失败，比如 exit_code 非 0；deny 是 harness 在执行前基于策略拒绝，命令根本没进入 sandbox。二者在 Observation 里是不同 kind，方便模型理解，也方便 trace 和 eval 统计。
```

### L08 HITL

这一层解决：

```text
Agent 不应该在需求不清、权限不足或高风险操作时自己猜。
```

两类：

```text
模型主动 ask: 需求澄清
policy require_approval: 权限审批
```

追问：

```text
Stop and Resume 和 Hang & Wait 有什么区别？
```

回答：

```text
Stop and Resume 是把 RunState 持久化后退出，用户之后通过 resume 继续；Hang & Wait 是进程保持运行，等待用户审批。miniCC 的设计偏 Stop and Resume，因为它更适合 CLI、eval 和任务可恢复。
```

### L09 Observation Contract

这一层解决：

```text
工具结果不能粗暴截断或直接塞 prompt。模型需要结构化、可解释的 observation。
```

必须背熟这几种：

```text
no_output:
  exit_code=0 但 stdout/stderr 为空，明确告诉模型命令成功但无输出。

command_error:
  命令执行了但 exit_code 非 0，回传 stdout/stderr preview，让模型修正。

timeout:
  执行超时，提示模型缩小范围或拆分命令。

policy_violation:
  policy 拦截，没有执行，说明原因和替代方案。

protocol_error:
  模型 action 格式错误，让模型按协议重试。
```

追问：

```text
结果过长怎么办？
```

回答：

```text
全量 stdout/stderr 落盘到 artifact，只把前后片段、总字节数和 artifact_id 回传给模型。这样既保留可追溯性，又避免长工具结果污染上下文。
```

### L10 Artifact Store

这一层解决：

```text
长输出、测试报告、diff 不能全部进入上下文，但必须可追溯。
```

设计：

```text
.minicc/runs/<run_id>/artifacts/
stdout_0001.txt
stderr_0001.txt
diff.patch
final_summary.md
```

追问：

```text
模型如果需要完整输出怎么办？
```

回答：

```text
Observation 里提供 artifact_id 和路径。模型可以用 bash 分段查看，例如 sed -n '1,120p'。这比一次性塞完整输出更可控。
```

### L11 Prompt Assembly

这一层解决：

```text
prompt 不是拼一大段字符串，而是按稳定性、功能和缓存友好性分层组装。
```

布局：

```text
Stable Prefix:
  身份、CodeAct 规则、JSON action 协议、policy 摘要、observation contract

Semi-Stable Context:
  skill catalog、feedback memory

Dynamic Context:
  当前 goal、constraints、state_summary、recent trajectory、pending approval
```

追问：

```text
为什么这个顺序对 prompt cache 友好？
```

回答：

```text
多数 prompt cache 都更容易复用稳定前缀。如果每轮把 run_id、时间、metrics、工具结果插到前面，就会破坏缓存前缀。miniCC 把稳定规则固定在前面，把动态状态放后面，压缩摘要也只放 Dynamic Context，尽量不影响前缀缓存。
```

### L12 Context Compression

这一层解决：

```text
长任务会让上下文爆掉，旧工具结果会稀释关键状态。
```

压缩触发：

```text
估算 token 超阈值
连续 observation 过长
prompt too long 错误
用户手动 compact
eval 固定预算
```

压缩放哪里：

```text
放在 Dynamic Context 的 state_summary。
在 goal/constraints 后，recent trajectory 前。
不能覆盖 Stable Prefix、action protocol、policy 规则和 feedback memory。
```

追问：

```text
压缩摘要里应该保留什么？
```

回答：

```text
保留用户目标、已完成步骤、关键文件、关键错误、当前判断、未解决问题、下一步建议和 artifact 引用。不要保留大量原始日志。
```

### L13 Skill Registry

这一层解决：

```text
技能不能全塞进 prompt，否则上下文浪费且破坏缓存。
```

设计：

```text
skills/<name>/SKILL.md
prompt 只放 name + description
需要时模型用 bash 读取对应 SKILL.md
```

追问：

```text
Skill 和工具有什么区别？
```

回答：

```text
工具是可执行 action，Skill 是经验和流程知识。Skill 不直接执行，它告诉模型在某类任务中怎么思考、用哪些命令、注意哪些坑。miniCC 的 Skill Registry 采用按需加载，避免把所有经验前置进 prompt。
```

### L14 Feedback Memory

这一层解决：

```text
Memory 如果什么都存，会变成噪声和幻觉来源。
```

只存：

```text
never: 不要做 X
prefer: 做 Y 时优先 Z
caution: 做 Y 时注意 Z
```

明确不存：

```text
代码模式和约定，从代码推断
Git 历史，git log 更权威
调试方案，修复已在代码里
临时任务状态，用 RunState
```

追问：

```text
Memory 遇到上下文压缩怎么办？
```

回答：

```text
Feedback Memory 不属于 trajectory，不应该被压缩掉。压缩只处理旧轨迹，memory 由 prompt builder 每轮按关键词筛选后重新注入，所以不会因为 compact 丢失。
```

### L15 Trace / Metrics

这一层解决：

```text
Agent 不能只看最终答案，要看完整 trajectory，才能调试、评估和回归。
```

记录事件：

```text
run_started
prompt_built
model_response
action_parsed
policy_decision
sandbox_exec_started
sandbox_exec_finished
observation_created
artifact_written
context_compacted
approval_requested
run_completed
run_failed
```

指标：

```text
turns
bash_actions
protocol_errors
policy_denials
command_failures
timeouts
context_compactions
prompt_tokens
completion_tokens
cached_tokens
prompt_cache_hit_tokens
prompt_cache_miss_tokens
cache_hit_rate
latency
artifact_bytes
```

追问：

```text
为什么要记录 cached_tokens？
```

回答：

```text
因为 prompt cache 是 Agent 成本和延迟优化的重要部分。DeepSeek 和一些聚合站会直接返回 prompt_cache_hit_tokens、prompt_cache_miss_tokens；OpenAI 风格可能是 prompt_tokens_details.cached_tokens。Provider adapter 会把这些字段归一化成 cached_tokens、cache_hit_tokens、cache_miss_tokens 和 cache_hit_rate，用来评估 prompt layout 是否真的稳定、是否破坏了缓存命中。
```

### L16 Eval Runner

这一层解决：

```text
Agent 是非确定性系统，不能只靠肉眼 demo 判断变好还是变坏。
```

Eval 做什么：

```text
复制 fixture 到临时 sandbox
用固定 prompt 跑 miniCC
记录完整 trace
跑确定性断言，比如 pytest 通过、diff 范围合法
统计 turns、工具次数、失败恢复、压缩次数、token、耗时
```

更重要的是，Eval 要能回答“这个 Agent 能做什么真实工作”，所以我把 eval 分成两层：

```text
Harness eval:
  验证协议错误、policy 拦截、大输出落盘、上下文压缩、审批恢复等机制。

Capability eval:
  验证 Agent 作为 coding agent 的真实能力，例如读懂仓库、修 bug、加小功能、补测试、重构和处理环境问题。
```

第一版真实能力验收集：

```text
C01 Repo Onboarding:
  不修改源码，理解一个陌生仓库，生成 ONBOARDING.md。
  证明 Agent 能做仓库理解和交接文档。

C02 Fix Failing Test:
  面对失败的 pytest，定位根因并最小修改修复。
  证明 Agent 能读错误栈、定位代码、修 bug、跑测试闭环。

C03 Add CLI Option:
  给 CLI 增加 --json 参数，并补测试。
  证明 Agent 能完成一个小功能从理解到实现再到测试。

C04 Add Regression Test:
  只为已修复边界情况补回归测试，不改业务代码。
  证明 Agent 能按边界约束写测试。

C05 Refactor Without Behavior Change:
  抽取重复逻辑，保持行为不变，测试通过。
  证明 Agent 能做有边界的小重构。

C06 Dependency Env Repair:
  修复缺失依赖声明，让干净环境能跑测试。
  证明 Agent 能处理真实工程里的环境和依赖问题。

C07 Large Log Debugging:
  从超长日志里定位第一个真实失败原因并修复。
  证明 artifact + preview 机制真的服务于调试。

C08 Policy Sensitive Cleanup:
  清理构建产物但保护源码、测试和配置。
  证明 Agent 能在安全策略约束下做清理类任务。
```

能力报告不是只写 PASS/FAIL，而是这样讲：

```text
Repo understanding: PASS，生成 ONBOARDING.md，无源码 diff。
Debugging: PASS，6 轮内修复 pytest 失败。
Feature work: PASS，实现 --json 并新增测试。
Test writing: PASS，只改 tests/。
Refactoring: PASS，测试保持通过，改动文件数受控。
Environment repair: PASS，更新 pyproject/uv.lock 后干净环境测试通过。
Long output debugging: PASS，长日志落 artifact，模型用 preview 定位。
Safety-sensitive work: PASS，policy 和 diff 保护关键路径。
```

追问：

```text
怎么比较两个 prompt 或两个模型？
```

回答：

```text
用同一批 eval cases，在相同 sandbox、预算和断言下分别跑，比较成功率、平均 turns、命令失败率、超时率、token 和成本。这样 prompt 或模型升级可以做回归，而不是凭感觉。
```

### L17 Web Trace Viewer

这一层解决：

```text
CLI 不适合展示完整执行轨迹，面试或调试时需要可视化。
```

极简页面：

```text
Run 列表
Timeline
Action / Policy / Observation
Artifact 链接
Metrics
Diff
```

追问：

```text
为什么不做完整 Web Chat？
```

回答：

```text
这个项目重点是 Harness，不是聊天产品。Web 只做 trace viewer，更贴合可观测性和面试展示目标。
```

## 6. 简历 bullet 写法

版本 1：工程完整型

```text
- 设计并实现 Bash-first CodeAct Agent Harness，使用结构化 JSON action 协议约束模型输出，将 bash 作为统一 action space，支持 bash/ask/final 三类动作和协议错误自恢复。
- 基于 Docker Desktop + WSL2 构建任务级沙箱，每个 run 独立复制 workspace、启动容器、限制网络/CPU/内存/PID，任务结束销毁容器并保留 trace、artifact 和 diff。
- 实现 Policy Middleware 权限链，支持命令、路径、网络、预算和人工审批策略，统一输出 allow/deny/require_approval/rewrite 决策。
- 设计 Observation Contract，对空输出、命令失败、超时、策略拒绝和超长结果做结构化回传；大 stdout/stderr 落盘为 artifact，仅将 preview 与 artifact_id 注入上下文。
- 构建 cache-friendly Prompt Assembly，将稳定协议段、Skill catalog、Feedback Memory、动态状态和压缩摘要分层组织，降低长任务中 prompt cache 被破坏的概率。
- 实现 Eval Runner 和 Trace Viewer，设计 capability_suite_v1 覆盖仓库理解、失败测试修复、小功能开发、回归测试补全、重构、依赖环境修复、长日志调试和安全清理，并记录 trajectory、token/cache/latency/失败恢复等指标。
```

版本 2：偏面试精简型

```text
- 从零实现 miniCC Agent Harness：Bash-first CodeAct + Docker Sandbox + Policy Middleware + Context Compression + Trace/Eval。
- 将模型 action 约束为 bash/ask/final 三类结构化 JSON，统一处理协议错误、工具异常、超时、大输出落盘和 HITL 审批。
- 设计 prompt cache 友好的上下文拼装策略：稳定前缀固定，动态轨迹后置，压缩摘要不破坏协议段和缓存前缀。
- 通过真实能力 eval 证明 Agent 可完成仓库理解、bug 修复、小功能开发、测试补全和依赖环境修复等 coding agent 工作。
```

## 7. 高频面试问答

### Q1: 你这个项目和 LangChain Agent 有什么区别？

答：

```text
我重点做的是 Harness 底层，而不是调用某个 Agent 框架。LangChain 更多提供现成抽象，miniCC 关注 Agent Loop、action 协议、sandbox、policy、observation、context、trace 和 eval 这些底层机制。这样我能解释每一轮模型调用前后发生了什么，工具结果怎么处理，为什么这么处理。
```

### Q2: ReAct、PlanAct、CodeAct 怎么理解？

答：

```text
它们都是 Agent 行为范式。ReAct 是 reasoning 和 action 交替；PlanAct 是先规划再执行；CodeAct 是用可执行代码表达 action。miniCC 采用 Bash-first CodeAct，因为 coding 场景里 bash 能统一表达大量操作。tool calling 只是通信方式，不是和 CodeAct 同层的概念。
```

### Q3: 为什么只用 bash，不设计 read_file/write_file/edit_file 等工具？

答：

```text
这是有意的简化。Claude Code 这类 coding agent 的一个核心启发是：不要用过多 harness 逻辑替模型做决策。Bash-first action space 让模型能组合 rg、sed、pytest、python 脚本等能力。harness 的重点转移到安全执行、结果治理和上下文管理。
```

可以补一句：

```text
后续如果发现某些高频操作需要更强约束，可以再抽成专门 action，但第一版保持最小 action space。
```

### Q4: 工具结果过长，你为什么不直接截断？

答：

```text
粗暴截断会丢证据，也让模型无法继续定位问题。我采用 artifact store：全量结果落盘，prompt 里只放 preview、总字节数和 artifact_id。模型如果需要完整证据，可以分段读取 artifact。这样上下文可控，证据也不丢。
```

### Q5: 命令报错要不要中断？

答：

```text
一般不中断。命令失败是 Agent 探索环境的一部分。harness 会把 exit_code、stdout/stderr preview 结构化成 command_error observation，让模型根据错误修正。只有协议连续失败、预算耗尽、sandbox 崩溃这类不可恢复错误才终止 run。
```

### Q6: 空结果怎么处理？

答：

```text
空结果也要显式结构化。如果 exit_code=0 但没有 stdout/stderr，返回 no_output，并说明命令成功但无输出。否则模型可能误以为工具坏了，或者重复执行无意义命令。
```

### Q7: 上下文压缩放在哪里，怎么不破坏 prompt cache？

答：

```text
压缩摘要只放在 Dynamic Context 的 state_summary 位置，在 goal/constraints 后、recent trajectory 前。Stable Prefix 包括身份、action 协议、policy、observation contract，不会被压缩改写。这样既保留任务连续性，又尽量保持稳定前缀不变，减少 prompt cache 失效。
```

### Q8: Prompt cache 你做了什么优化？

答：

```text
我没有把 core 绑定到某家厂商的缓存 API，而是在 harness 层做 cache-friendly prompt layout。稳定规则和协议固定前置，动态状态、run_id、metrics、工具结果都放后面。

在指标上，Provider adapter 会读取并归一化缓存相关 usage。例如 DeepSeek/硅基流动可能返回 prompt_cache_hit_tokens 和 prompt_cache_miss_tokens，OpenAI 风格可能返回 prompt_tokens_details.cached_tokens。流式接口还需要设置 stream_options.include_usage=true，才能从最后一个 usage chunk 拿到统计。最后统一记录 cached_tokens 和 cache_hit_rate，观察我的 prompt 结构有没有破坏缓存命中。
```

### Q9: Skill 和 Memory 怎么设计？

答：

```text
Skill 是按需加载的经验文档，prompt 只暴露 name 和 description，不全量注入。
Memory 我只做 feedback 类型规则，比如 never/prefer/caution。它不存代码约定、git 历史、调试过程和临时任务状态，避免 memory 变成噪声池。
```

### Q10: Eval 是什么，为什么 Agent 项目需要 Eval？

答：

```text
Eval 是给 Agent 做离线考试。我的 eval 不只测 harness 机制，还测真实 coding 能力。每个 case 有一个小型真实仓库 fixture、用户任务、预算和成功断言。Runner 复制 fixture 到 sandbox，执行任务，记录 trace，然后用 pytest、文件断言、diff allowlist 等方式判断成功。

例如 capability_suite_v1 里有：陌生仓库生成 ONBOARDING.md、修复失败 pytest、给 CLI 增加 --json 并补测试、只补回归测试不改业务代码、做行为不变的小重构、修复 pyproject 依赖声明、从长日志定位失败原因、在 policy 约束下清理构建产物。

这样我能回答“这个 Agent 能做什么工作”：它不是只会跑命令，而是能完成仓库理解、bug 修复、小功能开发、测试补全、重构和环境修复这些可验收的 coding agent 任务。
```

### Q11: 你怎么处理 Human-in-the-loop？

答：

```text
miniCC 有两种 HITL。模型主动 ask 用于需求澄清；Policy 返回 require_approval 用于高风险动作审批。RunState 会持久化 pending action 和 approval question，用户 approve 后继续，deny 后把拒绝原因作为 observation 回给模型换策略。
```

### Q12: 你怎么防止模型在 Docker 里乱搞？

答：

```text
首先每个任务是独立 workspace 副本，不直接操作原项目。其次 Docker 默认断网、限制 CPU/内存/PID、cap-drop、no-new-privileges。再次，所有 bash action 先过 PolicyChain，危险命令、敏感路径、联网动作和超预算动作会被拒绝或要求审批。最后保留 diff 和 trace 供审计。
```

## 8. 你需要准备的技术八股

优先级从高到低：

```text
1. Agent Loop:
   messages / action / observation / stop condition / run state

2. CodeAct vs ReAct:
   行为范式区别，tool calling 不是同层概念

3. Docker:
   container vs image，bind mount，network none，resource limits，Docker Desktop + WSL2

4. Middleware:
   PolicyChain，allow/deny/approval/rewrite，为什么安全逻辑要从 executor 抽离

5. Context Management:
   prompt assembly，context budget，compression，artifact preview

6. Prompt Cache:
   stable prefix，dynamic suffix，prompt_cache_hit_tokens / prompt_cache_miss_tokens / cached_tokens 指标，不绑定厂商

7. Tool Result Handling:
   no_output / command_error / timeout / policy_violation / large output artifact

8. Eval:
   fixture，assertion，trajectory，metrics，regression

9. Trace / Observability:
   JSONL event，timeline，metrics，debug trajectory

10. Skill / Memory:
   skill catalog 按需加载，feedback memory 边界
```

## 9. 30 秒项目介绍

```text
我做了一个 miniCC，定位是 Bash-first CodeAct Agent Harness。模型每轮只输出 bash、ask、final 三类结构化 action，harness 负责协议解析、策略中间件、Docker 沙箱执行和结果治理。每个任务会复制 workspace 并启动独立容器，默认断网和资源限制，任务结束销毁容器但保留 trace、artifact、diff 和 metrics。上下文上我做了 cache-friendly prompt layout，把稳定协议前置，动态轨迹后置，大工具结果落盘只回传 preview，长任务再压缩成 state_summary。最后还做 eval runner，用 fixture、pytest、diff allowlist 和 trajectory metrics 做回归评测。

真实能力上，我设计了一组 capability eval，覆盖仓库理解、失败测试修复、小功能开发、回归测试补全、重构、依赖环境修复和长日志调试，用确定性断言证明 Agent 能完成这些 coding agent 工作。
```

## 10. 2 分钟项目介绍

```text
miniCC 是我为了理解 Coding Agent 底层机制做的 Agent Harness 项目。它不是一个 LangChain 工作流，也不是堆很多工具，而是采用 Bash-first CodeAct，把 bash 作为统一 action space。

模型每轮必须输出结构化 JSON action，只有三类：bash 表示执行命令，ask 表示需要人类澄清或授权，final 表示任务结束。这样 harness 可以稳定解析、校验和重试。

执行层我用了 Docker Sandbox。每个 run 都复制一份 workspace，启动独立 Linux 容器，默认禁网并限制 CPU、内存和进程数。所有 bash action 先经过 Policy Middleware，包括命令、路径、网络、预算和审批策略。允许的命令才会进入容器执行，拒绝或需要审批的动作会以结构化 observation 回传给模型。

我重点设计了工具结果治理。空输出、命令失败、超时、策略拒绝、协议错误都是不同 observation kind。长 stdout/stderr 不直接塞 prompt，而是落盘成 artifact，只把 preview 和 artifact_id 回传。

上下文方面，我把 prompt 分成稳定前缀、半稳定上下文和动态上下文。action 协议、policy、observation contract 放稳定前缀，skill catalog 和 feedback memory 放中间，当前任务状态、压缩摘要和最近轨迹放后面。这样能尽量利用各厂商 prompt cache，也不会因为压缩破坏协议段。

最后我做了 trace 和 eval。每轮模型响应、policy decision、sandbox exec、observation、artifact、compression 都写 JSONL trace；eval runner 会复制 fixture，用固定预算跑任务，再用 pytest、diff allowlist 等断言判断是否成功，并统计 turns、失败恢复、token、cached tokens 和耗时。

为了避免 eval 只测到机制，我单独设计了 capability suite：比如让 Agent 在陌生仓库生成 onboarding 文档、修复一个失败测试、给 CLI 增加参数并补测试、只补回归测试不改业务代码、做行为不变的小重构、修复依赖声明，以及从长日志里定位失败。这样面试时可以具体说明 miniCC 能做哪些真实 coding 工作，而不是只说我有一个 eval runner。
```

## 11. 容易被追问的薄弱点

你需要特别准备：

```text
Docker 安全不是绝对安全，要说组合防护。
Prompt cache 不同厂商不完全一样，要说 core 不绑定，layout 优化 + metrics 观测。
Bash-first 强但风险高，要强调 Docker + Policy + Observation。
Memory 边界要讲清楚，否则会被认为什么都塞。
Eval 第一版可以很小，但必须有确定性断言。
Web 页面只是 trace viewer，不要吹成完整平台。
```

## 12. 项目最终金句

```text
miniCC 的核心不是让 harness 代替模型思考，而是给模型一个极简但可控的行动空间：模型用 bash 表达意图，harness 用 sandbox、policy、context、trace 和 eval 把这个意图变成安全、可观测、可回归的工程过程。
```
