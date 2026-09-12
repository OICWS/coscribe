# 本地办公 Agentic 系统 — 架构设计

> **更新（后续演进，本文档未同步重写）**：下面"关键设计决策与理由"第 1 节
> 和"技术栈"一节里描述的自建 `Runner`/`RunState`/`ToolPolicy`/`FileStateStore`
> agent runtime，已经整体迁移到基于 LangChain/LangGraph 的新 runtime
> （`coscribe.runtime_lg/`），原因是那套自建 runtime 手写的 Gemini
> provider 适配层（`coscribe/providers/gemini_provider.py`）连续出过三个
> 真实 bug。旧 runtime 的死代码（`runtime/runner.py`、`runtime/policies.py`、
> `runtime/state_store.py`、`tools/subagents.py`、
> `providers/anthropic_provider.py` 等）已在迁移完成并审计确认无人调用后
> 删除；`runtime/` 下现在只保留 `runtime_lg`/`coscribe` 仍在直接复用的
> 部分（`ToolMetadata`/`Agent` 等类型定义、Hooks、Provider 配置加载）。
> 另外，Persona 系统（本文档下面提到的会话开始时选人设、`{thread_id}.persona`
> sidecar 等）已经整体删除——发现这是从单一参考项目 `andrewyng/openworker`
> 借鉴的做法，不是主流产品（Claude Cowork、ChatGPT、WorkBuddy）的通用模式，
> 它们统一用一个全局"Custom Instructions"文本框，coscribe 自己早就有等价物
> （`MEMORY.md`），删除记录见 `office-agent/ROADMAP.md`。完整的迁移过程、
> 每一步的真实发现（不是事后合理化）
> 和最终的死代码审计结果，见
> `office-agent/src/coscribe/runtime_lg/README.md`——本文档保留原样，
> 作为"当初为什么这么设计"的历史记录，不作为当前实现的准确描述。
> "前端：本地 Web UI（技术选型未定，后期再定）"一句也已不准确：Web UI
> 早就跑通了，现在又多了一个桌面端——`office-agent-desktop/`，
> 一个 Electron 壳，启动同一个 `coscribe-web` 服务器作为本地 sidecar
> 子进程，窗口直接指向它，不是另一套前端（最早是 Tauri 壳，为了原生嵌入
> Browser 面板换成了 Electron，见 `office-agent/ROADMAP.md`的迁移记录；
> 这个公开仓库本身没有携带那个 Tauri 壳）。详见
> `office-agent/README.md`的"Desktop app"一节和
> `office-agent-desktop/src/main/index.ts`自身的设计说明。
> 前端本身也已从原生 HTML/JS 重写为 React + TypeScript（分 A/B/C/D 四个
> 阶段完成，逐阶段真实验证后 cutover）——`office-agent/frontend/`，
> `npm run build` 直接产出到 `src/coscribe/web/static/`（现在是构建
> 产物，不再是手写源码），后端协议/路由完全未变。详见
> `office-agent/frontend/README.md`。

## 目标

一个本地运行的、面向办公场景的 agent 系统。用户只需配置各厂商 LLM 的 API Key
即可使用。不从零打造 agent 框架本身，而是：

> **明确目标用户（后续演进澄清，防止方向漂移）**：coscribe 面向的是更广泛、
> 不一定懂代码的办公用户——本地读写文件、生成文档/表格/PPT 这类交付物、
> 连接 Slack/Asana 这类第三方办公工具、设置定时任务，是一个"日常文件和
> 工作流助理"，**不是**给开发者用的 agentic coding 工具。参考 claude-code
> 是借鉴其任务规划/Skill/Hooks/Subagent 这些**实现思路**（见下面"参考项目"），
> 不代表产品定位要往开发者工具靠拢。实测中发现过一次真实的方向漂移：内置
> MCP 目录一度加了 `git`/`github` 两个开发者工具属性的连接器（后者还带一套
> GitHub OAuth Device Flow），已经整体移除（见"扩展点"一节）——以后新增
> 功能前，先问一句"这是不是普通办公用户会用到的"，不是"这对开发者是否有用"。

- **骨架自建**：LLM 调用层、agent runtime（多智能体、任务规划、审查）、权限/审批引擎、
  会话与工作流持久化、UI。

- **骨架自建**：LLM 调用层、agent runtime（多智能体、任务规划、审查）、权限/审批引擎、
  会话与工作流持久化、UI。
- **能力借生态**：浏览器自动化、邮件/日历等具体办公能力，一律通过 MCP、
  Skill、Plugin 接入，不在骨架里手写。Word/PDF 文档处理是这条原则下的一个
  例外，见"范围边界"一节的说明。

参考项目：
- [`aisuite`](https://github.com/andrewyng/aisuite)（`aisuite-main.zip`）：借鉴其
  LLM Provider 抽象层与 `agents` 模块（Runner / RunState / ToolPolicy / StateStore /
  MCP client），可直接作为依赖复用。
- [`claude-code`](https://github.com/anthropics/claude-code)（`claude-code-main.zip`）：
  借鉴其任务规划（Task 工具族 + Plan Mode）、Skill 加载机制、Hooks 事件模型、
  Subagent 隔离设计的**思路**，用 Python 重新实现（原实现是 TypeScript，且和终端
  UI 强耦合，直接搬运代码成本高于重写）。

---

## 分层总览

```
┌─────────────────────────────────────────────────────────┐
│  UI（本地 Web）                                           │
│  - 对话界面：自然语言驱动，隐式生成工作流（非 LangGraph 式的   │
│    显式声明图，后期再考虑做可视化编辑器）                    │
│  - 任务面板：实时展示当前计划（Task 列表）与执行进度           │
│  - 审批弹窗：中高风险工具调用需要用户确认                    │
│  - 扩展管理页：增删 MCP Server、管理 Skills、管理 Plugins    │
├─────────────────────────────────────────────────────────┤
│  Agent Runtime（coscribe.runtime 自建，设计参考 aisuite）│
│  - Coordinator agent：任务分解 + 派发 + 维护任务列表          │
│  - Specialist agents：按领域划分的专家 agent（文档/文件/      │
│    网页等，具体工具集由用户配置的 MCP/Skill 决定）            │
│  - Reviewer agent：复核 specialist 的产出是否满足原始需求，    │
│    在高风险动作落地前或任务收尾前介入                        │
│  - Task 追踪工具：外化的计划列表（非单独 agent，是 Coordinator │
│    自带的工具能力）                                         │
│  - Plan Mode：复杂任务先只读产出计划，用户确认后再放开写权限    │
├─────────────────────────────────────────────────────────┤
│  权限 / 审批引擎                                           │
│  - ToolPolicy + ToolMetadata(risk_level, requires_approval) │
│  - 不区分工具来源（内置 / MCP / Skill 脚本），统一走同一套    │
│    风险分级与审批流程                                       │
├─────────────────────────────────────────────────────────┤
│  扩展点                                                    │
│  - MCP：标准 mcpServers 配置（stdio 或 http），复用           │
│    aisuite/mcp 的 config 校验 + client                     │
│  - Skill：SKILL.md 常驻描述 + 按需加载完整说明/脚本           │
│  - Hooks：精简版生命周期事件（PreToolUse / PostToolUse /     │
│    SessionStart），用户可挂自定义脚本做治理/联动              │
│  - Plugin：Skill + MCP 配置 + 命令的打包分发单元（后期做）     │
├─────────────────────────────────────────────────────────┤
│  LLM Provider 层（复用 aisuite）                            │
│  - 统一 chat(messages, tools, model="provider:model") 接口  │
│  - 按需精简到实际使用的厂商                                  │
├─────────────────────────────────────────────────────────┤
│  会话 / 工作流持久化（coscribe.runtime 自建）             │
│  - RunState / RunStep：可序列化，落盘用 FileStateStore        │
│  - 隐式工作流：对话自动产生并持久化                          │
│  - 显式工作流（后期）：把某次 RunState 的 tool_call 序列        │
│    抽取为参数化模板，可编辑、可重放、可定时触发                │
├─────────────────────────────────────────────────────────┤
│  内置基础工具（最小集合，不做办公专用解析）                    │
│  - 本地文件读写搜索（coscribe.tools.files 自建）          │
└─────────────────────────────────────────────────────────┘
```

---

## 关键设计决策与理由

### 1. 为什么 Agent Runtime 是自己写的，不是直接依赖 aisuite

最初的判断是"直接依赖 `aisuite.agents`"（Runner/RunState/ToolPolicy/
FileStateStore 都已经设计好了）。**实际动手搭骨架时发现这个判断是错的**：
`pip install aisuite` 装到的已发布版本（0.1.14）里只有
`client.py`/`provider.py`/`providers/`/`mcp/`/`framework/`——**`agents/` 和
`toolkits/` 根本不在发布包里**，只存在于 GitHub `main` 分支的未发布代码中
（也就是我们最初看的那份 zip 源码）。而且已发布版本里 `Client` 自带的
"传 `tools=[可调用对象]` + `max_turns` 就自动执行"这条路径完全没有审批
介入点（`_tool_runner` 拿到 tool_calls 后直接执行，不给任何回调机会）——
这和本项目"风险分级+审批"的核心需求直接冲突。

所以现在的做法是：
- **LLM Provider 层继续用已发布的 aisuite**：`aisuite.provider.ProviderFactory`
  直接拿 provider 实例，`aisuite.Tools` 做"Python 函数 → JSON Schema 工具定义"
  的转换和单次工具执行（`Tools.execute_tool`）——这两个是已发布包里稳定、
  职责单一的部分，复用它们能省掉重新写 docstring→schema 转换的功夫。
- **Agent Runtime（`coscribe.runtime`）完全自己写**：`Agent`/`RunState`/
  `RunStep`/`ToolPolicy`/`ToolMetadata`/`FileStateStore`，设计思路照搬我们
  读过的那份未发布代码（序列化、风险分级、审批回调三件事的形状基本一致），
  但代码是我们自己的，不依赖任何未发布/无版本保证的第三方模块。
- **`Runner` 自己驱动多轮循环**：调用 provider 拿一次模型回复 → 如果有
  tool_calls，先过 `ToolPolicy`（按 `ToolMetadata.requires_approval` 决定是否
  要问用户）→ 批准的才调用 `Tools.execute_tool` 真正执行 → 结果写回消息
  历史 → 下一轮，直到模型不再要求调用工具或达到 `max_turns`。

### 2. Planning 和 Reflection 不是"装了 MCP/Skill/Hooks/Subagent 就自带"的

Tool use 和 Multiagent 是结构性问题，靠 Runtime + MCP + Skill + Subagent 就解决了。
但 Planning（先想清楚步骤再动手）和 Reflection（回头检查自己做得对不对）是需要
专门设计的行为机制：

- **Planning**：不需要单独的"规划 agent"，是 Coordinator agent 自带的一个工具
  能力——任务列表工具（拆步骤、执行时更新状态，对应 claude-code 的
  `TaskCreateTool/TaskUpdateTool` 思路）。可选再加 Plan Mode：复杂任务先只读
  产出计划、用户确认后再放开写权限（对应 `EnterPlanModeTool/ExitPlanModeTool`）。
- **Reflection**：最好由"没参与做这件事"的 agent 来看，所以设计为单独一个
  **Reviewer agent**——在专家 agent 完成产出后、或高风险操作真正落地前，对照
  原始需求复核一遍。

净增：1 个新 agent 角色（Reviewer）+ 1 个工具（任务追踪），不是对称的"两个新
agent"。

### 3. 从 claude-code 抄思路、自己用 Python 重新实现的部分

以下机制 aisuite 里没有，需要参考 claude-code 的设计自己实现（不直接搬 TS 代码，
因其与终端 UI 强耦合）：

- **Task 追踪工具族**：`TaskCreate/TaskUpdate/TaskList/TaskGet` 的 Python 等价物。
- **Plan Mode**：只读模式产出计划 → 用户确认 → 放开写权限执行。
- **Skill 加载机制**：`SKILL.md`（name + description 常驻可见）+ 触发后加载完整
  说明/脚本，对应 `SkillTool` + `loadSkillsDir.ts`。
- **精简版 Hooks**：不需要 claude-code 的全部 15 种生命周期事件，先落地
  `PreToolUse`（审批介入点）、`PostToolUse`（联动/日志）、`SessionStart`
  （初始化）三种即可。
- **Subagent 上下文隔离**：子 agent 独立上下文窗口、独立工具白名单、结果不
  污染主对话上下文（对应 `AgentTool` 的设计），在 aisuite 的 `handoff` 机制
  之上封装。

### 4. 明确不抄的部分（对本地单用户场景是过度设计）

- claude-code 的多层权限配置来源体系（`userSettings/projectSettings/
  localSettings/flagSettings/policySettings` 五级覆盖）——这是给团队协作场景
  设计的，本地单用户用不上，直接用 aisuite 的三档 `risk_level` + 审批回调即可。
- `PostgresStateStore`——本地场景 `FileStateStore` 够用。
- OpenAPI 自动生成工具、A2A（Agent2Agent）协议——生态还不成熟或对当前场景收益低，
  先不做。

### 5. 扩展点：MCP / Skill / Hooks / Plugin

- **MCP**：外部工具/数据的标准协议，直接复用 `aisuite/mcp` 的 config 校验
  （`command/args/env` 或 `server_url`，与 Claude Desktop/Claude Code 的
  `mcpServers` 配置格式兼容）+ client。用户可以直接把社区现成的 MCP server
  （文档处理、浏览器自动化、Google Drive、邮件等）配置进来，不用我们写解析代码。
  "Connector"本质上就是 MCP 的更友好 UI 包装（预填好 command/args 的一键配置
  模板）。**更新**：内置目录里原来有 `git`/`github` 两个条目（后者走 GitHub
  官方 OAuth Device Flow，连到 GitHub 托管的远程 MCP server）——重新审视
  coscribe 的目标用户（面向更广泛、不一定懂代码的办公场景，不是 agentic
  coding 工具）后，判定这两个是开发者工具属性、跟目标用户不匹配，已经整体
  移除（`web/github_oauth.py` 连带删除，不留死代码）。仍然可以通过 Custom
  标签页手动配置 git/GitHub 相关的 MCP server（填 name/command/args），只是
  不再是内置目录里一键可加的预设条目。
- **Skill**：按需加载的操作说明+脚本包，覆盖"怎么做"（如 pptx 处理流程——
  docx/pdf/xlsx 现在都是内置工具，见下文），不是协议而是文件系统约定。
  两个来源：coscribe 自带三个内置 skill（PPTX/Excel/Word 设计指导，包内
  固定路径，随包分发），和用户自己在 `skills_dir` 里放的本地 skill
  （详见下文范围边界一节）——按会话勾选哪些生效，见 Web UI 的 Skills 面板。
- **Hooks**：生命周期事件挂自定义脚本，做治理/联动，复用同一套 ToolPolicy
  审批引擎的事件点。
- **Plugin**：Skill + MCP 配置 + 命令打包成一键安装单元，是分发层，不是新协议。
  后期做，等积累了自己常用的 Skill/MCP 组合后再打包。

### 6. UI：隐式优先，显式工作流后期再做

不做 LangChain/LangGraph 式的显式声明图编辑器。用户用自然语言描述任务，
Coordinator agent 隐式规划并执行，任务面板实时展示计划/进度作为透明度保障。
由于 `RunState` 本身可序列化，后期要做的"显式工作流编辑器"其实是"把某次隐式
运行的记录可视化/参数化"，不需要另起炉灶建一套图执行引擎。

---

## 技术栈

- 语言：Python
- LLM 调用层：`aisuite`（已发布版本，精简到实际使用的厂商；用
  `ProviderFactory` + `Tools` 做单轮补全和工具schema转换，MCP client 同样来自
  已发布的 `aisuite.mcp`）
- Agent Runtime：`coscribe.runtime` 自建（Runner/RunState/ToolPolicy/
  FileStateStore，设计参考 aisuite 未发布的 `agents` 模块，原因见上文）
  + 自建 Task 工具、Plan Mode、Skill loader、精简 Hooks、Subagent 封装
- 后端：FastAPI（承载 Runner 执行、WebSocket 推送审批请求/执行轨迹）
- 前端：本地 Web UI（技术选型未定，后期再定；先把对话界面跑通即可）
- 持久化：`FileStateStore`（本地 JSON），无需数据库

## 范围边界（v1 明确不做）

- Word（docx）/PDF 读写是内置工具（`tools/documents.py`）——写用
  `python-docx`/`reportlab`；读用 `mammoth`+`markdownify`（docx→html→
  markdown）和 `pdfplumber`（PDF 文本提取），这两组分别是
  microsoft/markitdown 自己内部转换 docx/pdf 时用的库，借用它们而不直接依赖
  markitdown 这层壳，是为了绕开 markitdown 强制依赖的 `magika`（文件类型嗅探，
  连带 onnxruntime + numpy，100+MB，且用不上——每个工具已经知道自己读的是什么
  格式）。**不**走 Skill/MCP —— 例外原因：调研过现有的
  Word/PDF MCP server（Office-Word-MCP-Server、mcp-pdf 等），都是个人维护
  的小众仓库，没有像 playwright（微软官方）、fetch（MCP 官方参考实现）那样
  的权威来源，引入即等于让一个未经审计的第三方进程读写用户本地文件，信任
  风险比用我们自己审查、维护成熟的标准库更高。xlsx 同理，也是内置工具
  （`tools/spreadsheets.py`，读写都用 `openpyxl`——同样成熟、审计充分，且不存在
  比"个人维护小众仓库"更权威的 Excel MCP server，跟 docx/pdf 是完全一样的理由）。
  pptx 也做成了内置工具（`tools/presentations.py`），但有两点诚实说明、不套用
  上面"成熟审计"的说法：(1) 生成/读取用的 `python-pptx` 自 2024 年 8 月起
  没有再发布过版本，不算积极维护，只是这个场景下最标准、够用的库；
  (2) Anthropic 官方 pptx skill（`anthropics/skills`，仅供参考不是开源，见
  仓库 README）本质是靠执行脚本（含调 LibreOffice、生成并执行 pptxgenjs
  脚本）实现的，但 coscribe 目前没有代码执行工具（`tools/skills.py` 的
  docstring 里写明了这点）——于是 `write_pptx` 把"调 LibreOffice 渲染检查
  文字溢出"这一步做成了工具内部的实现细节（`subprocess` 调 headless
  LibreOffice + 已有的 `pdfplumber` 读取渲染后 PDF 的文字位置），不暴露成
  agent 可执行脚本的能力，也不需要通用代码执行工具。LibreOffice 是可选的
  运行时依赖，装不装都不影响 `write_pptx` 本身成功——没装就跳过溢出检查，
  在返回值里说明原因。同一个 LibreOffice 调用后来又多了一个用途：
  `tools/_thumbnail.py` 的 `render_thumbnail` 把"渲染出的第一页/第一张
  幻灯片存成一张 PNG 缩略图"这一步抽成了共用 helper，`write_docx`/
  `write_pdf`/`write_xlsx`/`write_pptx` 四个写工具都调用它，返回值里加了
  `preview_path`——前端聊天记录里工具调用结果下面会直接显示这张缩略图，
  不用打开文件才知道生成的东西长什么样。缩略图不存进用户的 workspace（避免
  在用户文件旁边多出一堆 `.png`），存在 `settings.state_dir`（默认
  `.coscribe/state/`，跟 tasks/workflows 那些内部状态同一个目录）下的
  `previews/` 子目录，文件名是随机生成的 uuid，因此 `GET
  /api/previews/{name}` 只需要按精确文件名匹配一个固定目录，不需要
  `WorkspaceScope` 那套路径穿越校验。同样是可选依赖、优雅降级——没装
  LibreOffice，`preview_path` 就是 `null`，`preview_skipped_reason` 说明
  原因，写文件本身仍然成功。
- `add_xlsx_chart`/`add_pptx_chart`（分别在 `tools/spreadsheets.py`/
  `tools/presentations.py` 里）给已有的 xlsx/pptx 文件加柱状图/折线图/饼图，
  不负责建文件本身，接在 `write_xlsx`/`write_pptx` 后面用。调研过 Anthropic
  官方 pptx skill 后发现的坑——堆叠图的数据标签位置、组合图表的次坐标轴
  注册——都是 schema 合法但 PowerPoint/Excel 判定文件损坏的情况，根源是
  手写图表 XML。这两个工具全程只用 openpyxl/`python-pptx` 的高层图表 API
  （`BarChart`/`LineChart`/`PieChart`、`CategoryChartData`），从不手写 XML，
  坐标轴/系列注册由库保证正确；范围也刻意收窄到单组柱状图/折线图/饼图，
  不做堆叠图和组合图——用范围控制规避这类损坏，而不是加一个事后校验器。
  `add_pptx_chart` 的图表数据故意做成 pipe-table 字符串（跟 `write_xlsx`
  的 `content` 同一个写法），不是 list/dict 类型的工具参数——这个包里目前
  没有任何工具用过 list/dict 参数，aisuite 的 Gemini schema 推断在比这更
  普通的类型注解上都出过真实 bug（见 `Optional[int]`/`Optional[str]` 那几处
  注释），没必要在这里冒险。两个工具复用同一套 `render_thumbnail`，加完
  图表照样能在聊天里看到缩略图。
- 调研 Anthropic 官方 xlsx skill 后，发现 coscribe 原来的 `write_xlsx`
  有个真实缺口：`_coerce_cell` 只做 int/float/字符串三选一，看起来支持
  公式（`"=SUM(...)"` 会原样存成字符串），但从没验证过——openpyxl 写公式时
  不带缓存值，`read_xlsx` 用的 `data_only=True` 在这种情况下读回来全是
  `None`，模型自己检查刚写的公式会看到一片空白，以为写坏了。补的东西按
  Anthropic 官方 xlsx skill 的要求对齐：(1) `write_xlsx` 写公式前先校验，
  硬拒绝 `XLOOKUP`/`XMATCH`/`SORT`/`FILTER`/`UNIQUE`/`SEQUENCE`——这几个
  LibreOffice 在任何前缀下都算不出来，而且 openpyxl 写出的文件没有 spill
  range 元数据，"部分能用"的版本会静默地只填左上角一个格子，比直接报错
  更危险；(2) 自动给 `TEXTJOIN`/`CONCAT`/`IFS`/`SWITCH`/`MAXIFS`/`MINIFS`
  这六个 2007 后新增函数加上 `_xlfn.` 隐藏前缀——Excel 自己的文件格式就是
  这么存的（UI 上不显示），裸写这几个函数名存进 xlsx 会变成 `#NAME?`，不能
  指望模型记得这个 OOXML 的坑；(3) 新增 `_recalc_xlsx`，通过往一个临时
  LibreOffice profile 里塞一段 StarBasic 宏（`ThisComponent.calculateAll()
  + store() + close()`）、headless 调用该宏来强制重算——这是 Anthropic 官方
  xlsx skill 自己的 `recalc.py` 用的同一个技术，验证过普通的
  `soffice --convert-to` **不会**强制重算（只会原样吐出已有的缓存值，对一个
  刚用 openpyxl 写出的文件就是啥都没有）。这个宏调用方式在本地沙盒里踩过
  一次真坑：直接 mkdir 一个 profile 目录、把宏文件塞进去是不够的，必须先
  用 `--terminate_after_init` 跑一次空的 headless 实例，让 LibreOffice 自己
  把 profile 目录结构（`user/basic/Standard/` 及配套的 `script.xlb`）建好，
  再覆盖里面的 `Module1.xba`——跳过这一步，宏调用会直接卡死不返回（在这个
  沙盒里实测复现过），不是随便糊一个 profile 目录就能用。`write_xlsx` 写完
  只要有公式就自动跑一遍重算，把 `recalc_status`/`total_errors`/
  `formula_error_locations` 塞进返回值；额外加了 `recalc_xlsx` 独立工具（给
  非 write_xlsx 写出来的文件用）和 `format_xlsx_cells`（数字格式/字体颜色/
  加粗，对齐 skill 里"金融模型蓝色输入/黑色公式/绿色跨表链接"的配色约定）。
  跟 `write_pptx` 的溢出检查一样优雅降级——没装 LibreOffice、超时、或工作簿
  链接了外部文件（重算会把这些链接的缓存值解析成 `#NAME?` 并永久删掉链接）
  时都只是跳过、给出原因，不会让 `write_xlsx` 本身失败。
- `tools/presentations.py`/`tools/documents.py` 后续又加了一批 pptx/docx
  能力，按风险从低到高分两类：
  - **纯用 python-pptx/python-docx 高层 API，零手写 XML**：`write_docx`/
    `write_pptx` 都加了可选的 `template_path` 参数（复用用户已有的模板/
    主题），`add_pptx_image`（`slide.shapes.add_picture()`）、
    `set_pptx_notes`（`notes_slide.notes_text_frame.text`）。模板清空
    这一步有个真实踩过的坑：只删 `presentation.slides._sldIdLst` 里的
    条目、不调 `presentation_part.drop_rel(r_id)`，会在 zip 包里留下
    孤立的旧幻灯片 XML part，跟新加幻灯片自动生成的 part 名字冲突
    （`Duplicate name: 'ppt/slides/slide1.xml'` 警告，是真实的文件
    损坏风险，不是无害警告）——写代码前先用一个独立脚本、开
    `warnings.simplefilter("error")` 空跑验证过这个修复，再落到工具里。
    docx 同理，清空 body 时只保留 `sectPr`（页面大小/页边距），别的
    子元素全删，同样先空跑验证过。
  - **手写 OOXML**：`set_pptx_transition`（`<p:transition>`）风险较低——
    ECMA-376 基础 schema 就有这个元素，一个空白幻灯片的 `<p:sld>` 子元素
    经验证是 `['cSld', 'clrMapOvr']`，所以直接 append 在末尾就是 schema
    合法的插入位置；真正的 duration 精度用的是 PowerPoint 2010 才加的
    `p14:dur` 扩展属性（毫秒），不是基础 schema 那个只有
    fast/medium/slow 三档的 `spd`。`add_pptx_animation`（`<p:timing>`）
    风险最高——`python-pptx` 完全没有动画 API（作者自己在 GitHub issue
    里说这块"每次尝试实现都卡在 edge case 上"，从 2018 年开到现在没关），
    只能手写整棵 timing 树。这里没有凭记忆瞎写：核心骨架（tmRoot/mainSeq/
    click-group 两层包装/id 编号从 1 递增）和两个具体效果
    （`fade`：presetID=10 + `p:animEffect filter="fade"`；`fly-in`：
    presetID=2 + 用 PowerPoint 的 `#ppt_x`/`#ppt_y`/`#ppt_h`
    变量语法把 `ppt_y` 从"幻灯片外一个自身高度"动画到原始位置）都是
    对照 `hugohe3/ppt-master`（本项目调研动画可行性时找到的高星开源
    项目）实际打包在它仓库里的 `pptx_animation_presets.json`（真实
    PowerPoint 生成过的 XML 行）核对过的，不是自己拍的数字。范围刻意
    收得很窄，只有这两种效果，且只支持"点击触发的进入动画"这一种
    最常见场景，不做退出/强调动画、不做动作路径编辑。写入方式也刻意
    保守：先存到临时文件、重新用 `python-pptx` 打开、结构化校验刚加的
    动画确实挂在目标 shape 上、语义元素（`animEffect`/`anim`）确实存在，
    校验通过才 `replace()` 覆盖用户的真实文件，校验失败就删掉临时文件、
    原文件保持不变并报错——**这只是结构校验，不是视觉校验**：这个沙盒
    环境只有 LibreOffice、没有真正的 PowerPoint，LibreOffice 打得开、
    转得了 PDF 不代表 PowerPoint 自己那套更严格的损坏检测也会放行，
    工具的 docstring 和返回值里都明确写了这一层"未经真机验证"的免责声明。
- `tools/scripts.py` 的 `run_python_script`——之前讨论过 xlsx/docx/pptx
  跟 Anthropic 官方 skill 的差距时，结论是差距是结构性的：Anthropic 那边
  靠真跑 Python/Node 脚本（pandas 处理大数据、任意单元格编辑、多步迭代
  调试），coscribe 之前完全没有代码执行能力。这个工具补上了这块，
  但架构决定是"完全不做沙盒，纯靠审批"——原话依据是 Claude Code 自己
  官方文档："This option does not support native Windows. On Windows
  hosts, use WSL2 or one of the container or VM approaches."，也就是说
  Claude Code 自己在没配 WSL2 的原生 Windows 上，跑 Bash 工具时也是零
  沙盒、纯靠审批，coscribe 只是把这个真实存在的信任模型搬过来，不是
  自己发明了一个更弱的方案。`run_python_script` 是这个包里唯一
  `risk_level="high"` 的工具，也是唯一没有 `WorkspaceScope` 路径校验的
  写工具——脚本能读写 coscribe 进程本身能碰到的任何文件、能发任何
  网络请求，审批弹窗里完整展示脚本原文＋模型给的一句话描述是唯一的安全
  机制，不是"沙盒之外的第二道防线"。故意不做 import 白名单/黑名单，
  理由跟 Claude Code 的 Bash 工具一样：`__import__`/
  `subprocess.run(["pip","install",...])` 随手就能绕开，加了只会给人
  一种假的安全感。真正做了限制的是运行环境本身：`tools/script_env.py`
  管理一个独立 venv（默认 `state_dir/script-env/`），跟 coscribe
  自己跑的那个解释器彻底分开——这不是安全考虑，是稳定性考虑：这个项目
  自己的开发 sandbox 这一轮里已经三次因为 pip 包被环境重置清空，把用户
  脚本要装的包跟 coscribe 自己的运行依赖混在一起，只会让这类问题
  更容易发生。首次使用时自动创建并预装 `openpyxl`/`python-docx`/
  `python-pptx`/`pandas`/`pdfplumber`（coscribe 自己内置工具用的
  同一批库），用户可以在 Settings → Environment 面板里再装别的——这个
  面板故意做成"输入库名、点加号"的极简列表，不是 Claude Code 那种自由
  文本 Setup script 输入框：coscribe 面向的是不太懂技术的办公用户，
  一个要手写 bash 脚本的框架对这批用户不友好。网络访问这块没做任何限制，
  也没打算做——Claude Code 云端环境的 None/Trusted/Full 下拉框能生效
  是因为有容器网络策略在背后强制执行，coscribe 是本地桌面应用，没有
  对应的基础设施，唯一可能的软方案（起本地代理、给子进程设
  `HTTP_PROXY`/`HTTPS_PROXY` 环境变量做域名白名单）只对遵守代理变量的
  库生效、绕不过存心绕过的脚本，属于独立的一块工作量，这轮没做，README
  里如实写了这个限制。
- **`run_node_script`**（`tools/node_scripts.py`+`tools/node_env.py`）
  ——用户实测过 PPTX 效果后明确反馈"生成的还是太简单，跟外部工具完全不
  一样"，追问下来，用户直接要求做 Anthropic 官方 pptx skill 那种真实
  的 pptxgenjs 脚本生成路线（此前评估过这个方向、因为觉得"先做代码执行
  工具"投入太大而搁置，`run_python_script` 后来因为别的需求真的做出来
  了，这轮用户主动要求把同样的能力扩展到 Node.js）。跟 `run_python_script`
  同一个安全模型（`risk_level="high"`、无沙盒、审批弹窗是唯一防线、不做
  import 白名单），但隔离机制不能照搬：Python venv 能把"固定解释器＋
  site-packages 路径"焊死在一个二进制里，不管脚本文件本身放哪、`cwd`
  是什么都能 resolve；Node.js 没有等价物，`require()` 的模块查找是按
  目录树往上找的。实测验证过（脚本文件和 `cwd` 都放在
  `state_dir/node-env` 之外的临时目录，靠 `NODE_PATH` 环境变量指向
  `node-env/node_modules`）`require("pptxgenjs")` 依然能正确解析、
  真的能写出合法的 `.pptx` 文件（用 python-pptx 读回来验证过内容）——
  这个机制在写工具之前先用一个独立脚本证实过，不是照着 Node.js 文档
  猜的。隔离的理由也和 Python 侧不完全一样：coscribe 后端自己没有任何
  Node.js 运行时依赖（`frontend/` 是完全独立的开发期构建工具链，跟
  这里无关），真正要隔离开的是"装的包别弄脏 `frontend/` 自己的
  `node_modules`"，不是"别把 coscribe 自己的依赖搞坏"。基线只预装
  `pptxgenjs` 一个包（不是 Python 侧那五个），跟 Settings → Environment
  面板新增的第二个列表（同一套极简"输入名字、点加号"UI，抽成了
  `PackageListSection` 复用组件）配合，用户/模型需要别的包（比如渲染
  图标要用的库）自己加。Node.js/npm 本身是继 LibreOffice 之后第二个
  "非 pip 安装、系统级、允许缺失"的可选依赖——没装的话
  `ensure_node_env` 会抛一个明确说清楚缺什么的 `RuntimeError`，不是
  裸的 `FileNotFoundError`，其余所有工具（包括 `write_pptx` 本身）不受
  影响。PPTX skill 里新加的 pptxgenjs 具体指南（默认画布尺寸
  10in×5.625in、颜色格式必须是 6 位十六进制、`#` 前缀会被自动去掉、
  8 位带 alpha 的十六进制会静默 fallback 成默认色而不是报错）都是直接
  读装在这个环境里的 pptxgenjs 4.0.1 真实源码验证过的，跟参考资料里的
  说法不完全一致（比如常见说法是"绝对不能带 #"，但这个版本的源码明确
  会自动 strip 掉 # 号）——这也是为什么这轮没有照抄任何参考资料里的
  gotcha 列表，只写了亲自验证过的这两条。
- `tools/documents.py` 的 `write_docx` 补了四个 python-docx 原生 API
  没有、但 Anthropic 官方 docx skill 明确覆盖的能力——TOC、批注、修订模式
  （tracked changes）、页面尺寸/方向。跟前面 pptx 动画同理，凡是
  python-docx 没有高层 API 的地方都是手写 OOXML，不是拍脑袋写的：
  - **TOC**：`_add_toc` 是社区里公认的标准写法（`fldChar` begin/
    separate/end + `instrText` 里塞 `TOC \o "1-3" \h \z \u` 域代码），
    python-docx 完全没有域代码 API。占位文字（"Right-click and choose
    Update Field..."）在真正打开 Word 前不会变成真实目录——`settings.xml`
    里补一个 `<w:updateFields w:val="true"/>`（`_enable_update_fields`）
    让 Word 打开时自动重算所有域，不用户手动按 F9。`content` 里一行
    单独的 `[TOC]` 触发，复用现有的按行 `parse_blocks`。
  - **批注（comments）**：docx skill 自己的做法是"unzip → 编辑
    document.xml → 手动加 comments.xml/关系/content-type → rezip"，
    是给一个已解压好、正在改的文档准备的流程。这里的做法不一样，也更
    可靠：批注锚点（`commentRangeStart`/`commentRangeEnd`/
    `commentReference`）在 python-docx 自己的内存树上直接插（用每个
    block 对应的 `paragraph._p`，不是存完盘再重新解析 document.xml 去
    按下标猜哪个 `<w:p>` 是哪个——下标对不上是这类代码最容易犯、也最难
    发现的错），`document.save()` 一次性把这些锚点跟着正文一起写对。
    `comments.xml`/`[Content_Types].xml` 的 Override/关系这三个部分
    python-docx 确实没有添加"未知 part 类型"的公开 API，只能在
    `document.save()` 之后做一次最小化的"解压→加 part→重新打包"
    （`_write_comments_part`）——但只碰这三个新增的部分，不重新解析/
    改写 `document.xml`，避免了下标对不上的那类风险。`content` 里一行
    末尾的 `{{comment: 具体文字}}` 触发，只在 heading/paragraph/bullet/
    numbered item 上识别，表格单元格里的原样保留（v1 明确不做，写进了
    docstring）。
  - **修订模式（track_changes）**：docx skill 的修订模式章节讲的是"改
    一个已有文档时怎么标记"，`write_docx` 是从零生成，语义不一样，这里
    按"生成的内容整体作为可审阅的建议"重新设计：`track_changes=True`
    时，这次调用新写的每个 heading/paragraph/bullet/numbered item 的
    `<w:r>` 都被包进 `<w:ins>`（`_mark_paragraph_inserted`）；配合
    `template_path` 时，模板原有的段落不再是 `_clear_body` 直接删掉，
    而是 `_mark_body_deleted`→`_mark_paragraph_deleted` 把每个
    `<w:t>` 转成 `<w:delText>`、包进 `<w:del>`，段落标记本身也标记
    删除（`pPr/rPr/w:del`，对应 docx skill 文档里"删除整段"的 XML
    形状：接受修订时会把这段并入下一段，不会留下空行）。表格在
    `track_changes` 下始终按"最终内容"处理，不生成表格级的修订标记——
    表格级 tracked deletion 需要行/单元格粒度的标记，工作量明显更大，
    v1 明确跳过，docstring 里写清楚了。所有 `w:ins`/`w:del` 的
    `w:id` 由一个贯穿整次 `write_docx` 调用的计数器
    （`_ChangeIdCounter`）分配，保证同一文档内唯一——Word 对重复 id
    的容错程度没有验证过，不赌。
  - 四个功能都不是"结构校验就算过"——除了 XML 结构检查（`w:comment`/
    `w:ins`/`w:del`/`w:pgSz` 等元素确实按预期出现），还每个都真的过了
    一遍 LibreOffice headless 转 PDF（`soffice --convert-to pdf`）
    确认文件本身没有损坏、能被真实办公软件打开，而不只是"Python 没
    抛异常"。
- `web_search`（`tools/websearch.py`）是内置工具，不是走 MCP——用于
  grounding（查时效性信息、核实模型训练数据之外的事实），不是 RAG，跟
  本地文件检索（`search_files`/`search_pdf`）是两回事。底层用 `ddgs`
  这个包查询多个免费、不需要 API key 的搜索引擎（`duckduckgo`/`brave`/
  `google`/`mojeek`/`startpage`/`wikipedia`/`yahoo`/`yandex`，用的是
  `ddgs` 自带的 `"auto"` 模式，并发查询多个、汇总能返回结果的那些），
  跟这个项目一贯的"本地优先、不强制额外付费依赖"路线一致（对比 Tavily
  这类专给 agent 用的搜索 API，效果更好但要付费 key）。**实测踩过一次**：
  最初曾把这个工具写死成只查 DuckDuckGo 一家，真实用户实测时刚好遇到
  DuckDuckGo 自己的反爬机制把请求当成 202（软性拦截，不是真的结果页），
  单一后端直接导致整次搜索返回空——已经改成不写死后端，让 `ddgs` 自动
  在多个引擎间选、失败一个不耽误整体。
- `tools/images.py`（`search_images`/`download_image`）+
  `set_pptx_background_image`（`tools/presentations.py`）——PPTX 设计质量
  这轮加的，用户在"raw DuckDuckGo 图片搜索 / 接入 Unsplash 等免费商用
  图库 API / 完全不接网络取图"三个选项里明确选了第一个，接受了随之而来的
  真实风险，这里如实记录，不是事后才发现的问题：
  - `ddgs` 的 `.images()`（图片搜索）跟 `.text()`（网页搜索）不一样，
    **只有一个后端**（通过 DuckDuckGo 自己的接口代理 Bing 图片索引），
    没有 `web_search` 那种"一个引擎被拦截、自动换下一个"的容错。这轮
    联调时**实测踩到了**：`.images()` 拿到 DuckDuckGo 自己的 202 软性
    反爬拦截，整次搜索直接超时失败，重试几次后才成功——这不是我们代码的
    bug，是这个功能真实、需要接受的限制，`search_images` 的 docstring
    和 coordinator 的 `INSTRUCTIONS` 都如实告诉了模型："搜不到就换个
    query 或重试一次，不代表功能坏了"。
  - **不做许可证/版权筛选**——`search_images` 返回 DuckDuckGo 索引到的
    任何图片，没有"免费商用"这类过滤，这是用户在三个选项里明确选择的
    结果，不是疏漏。`download_image`/`set_pptx_background_image` 的
    docstring、coordinator 指令、README 都用同样直白的措辞写清楚：
    生成的 deck 带着网上找来的图，应该当草稿看，真要对外发布/发给客户，
    需要用户自己另外核实图片版权。
  - `download_image` 的安全处理：只接受 `http`/`https`（拒绝
    `file://` 等 scheme）；流式下载、超过 20MB 中途放弃（不会先把整个
    响应缓冲到内存才检查大小）；**Content-Type 头不可信**（可能缺失/
    错误，或者服务器对一个 404/登录页也返回 200），真正的校验是下载完
    之后用 Pillow 实际解码——这一步**实测踩到**了 Wikimedia Commons 对
    默认 `httpx` User-Agent 返回 403 的情况，加了一个如实的自定义
    User-Agent 头之后才通过，不是凭空加的。
  - `set_pptx_background_image`：`python-pptx` 的 `slide.background.fill`
    只支持纯色/渐变，没有图片填充 API，所以背景图是手动 `add_picture`
    铺满整个幻灯片尺寸，再做两件 `python-pptx` 原生支持、但需要自己算
    的事——（1）**cover 裁剪而非拉伸变形**：按图片自己的真实宽高比
    （用 Pillow 在调用时现读，不依赖 `download_image` 记住的元数据，
    这样不管图片是下载来的还是用户自己放进 workspace 的都一样能用）
    跟幻灯片宽高比对比，裁剪较长的那条边，用 `Picture.crop_left/right/
    top/bottom`（`python-pptx` 原生支持这几个属性，裁剪部分不用手写
    XML）；（2）**z-order**：`add_picture` 默认把新图片元素追加到
    `spTree` 末尾（最上层，会挡住标题/正文），加之前先用一个独立脚本
    实测确认了这一点，再把生成的 `<p:pic>` 元素移到 `spTree` 里第一个
    形状子元素的位置（紧跟固定的 `nvGrpSpPr`/`grpSpPr` 之后），让背景图
    真正显示在已有形状后面。跟这轮 docx OOXML 的工作同一个纪律：每个
    手写/手动挪动 XML 的地方都过了真实 LibreOffice `--convert-to pdf`
    转换 + 渲染截图肉眼确认，不只是结构校验。
- `add_pptx_scrim`（`tools/presentations.py`）——上面那张"秋天森林"背景图
  实测暴露的真实问题（深色标题文字压在深色树叶照片上，看不清）催生的功能：
  `python-pptx` 的 `FillFormat` 完全没有透明度 API，跟背景图的裁剪同一类
  "python-pptx 没有、手写 OOXML"的情况——`fill.solid()` 建出
  `<a:solidFill><a:srgbClr>` 结构后，手动在 `<a:srgbClr>` 下面加一个
  `<a:alpha val="35000"/>`（35%）子元素，这个具体写法**没有凭记忆瞎写**：
  先用一个独立脚本存盘、转 LibreOffice PDF、渲染成图肉眼确认这个
  半透明矩形真的是灰色（35% 黑叠加在白底上应该是灰，不是纯黑），确认
  alpha 真的生效了才写进工具里。z-order 复用背景图那次已经验证过的
  "把新形状挪到 spTree 里正确位置"技巧，但目标位置不同：挪到最后一个
  `<p:pic>`（如果有背景图的话）后面、其余内容前面，让遮罩层在背景图之上、
  正文之下。**颜色选不对遮罩基本没用**——这轮实测对比过：黑色遮罩配黑色
  标题文字，对比度几乎没有改善（深色压深色）；换成白色遮罩才让同一张图
  上的深色文字清晰可读，代价是照片本身褪色变淡——这是真实的设计取舍，
  不是遮罩层"加了就完事"，所以刻意不在 `set_pptx_background_image` 里
  自动套用，是否要加、加什么颜色，留给下面的 review_work 闭环判断。
- `review_work`（`runtime_lg/subagents.py`）从"空工具列表、纯文字审查"
  升级成有真实读工具、能看图的审查——用户明确要求参照 WorkBuddy 那张图
  的"Content Reviewer - Self-Verification"环节做的。之前的版本只能审查
  执行 agent 自己写的文字摘要，审查者没有 `read_docx`/`read_pptx` 这类
  工具去独立核实，更别提看渲染图——这轮补上：`build_review_work_tool`
  现在接收调用方（`web/session.py`）传入的 `reviewer_tools`（过滤
  `category=="documents"` 且 `requires_approval=False` 的工具，正好是
  `read_docx`/`read_pdf`/`search_pdf`/`read_xlsx`/`read_pptx` 这五个——
  全部只读，所以复用原来"审查者没有会暂停的工具，不需要 spawn_agent 那套
  嵌套中断桥接"的简化逻辑仍然成立，只是从"没有工具"改成"没有*会暂停*的
  工具"）；新增 `preview_name` 参数时，把 `state_dir/previews/` 下的
  真实缩略图文件读出来、转 base64，跟 `web/session.py` 用户上传图片时
  已经在生产环境跑着的同一种 `image_url` 多模态消息格式拼在一起传给
  reviewer——不是自己发明了一套新的多模态输入机制。**真机验证，不只是
  跑通不报错**：拿这轮"秋天森林"标题遮挡的真实截图，配一把真实 Gemini
  key（这个沙盒环境本来就有 `GEMINI_API_KEY`），让升级后的 review_work
  去看——第一次跑就发现了一个真实的、跟这次功能改动完全无关的旧 bug：
  `review_work`（和它的兄弟 `spawn_agent`）从来没有真正被 `.invoke()`
  跑过，`build_langgraph_agent` 默认挂的 `InMemorySaver` checkpointer
  要求 `.invoke()` 的 config 里带 `thread_id`，之前的代码完全没传，一调
  就报 `ValueError`——修成每次 `review_work` 调用生成一个新
  `uuid4().hex` 当 thread_id（每次调用本来就是一次性的，不需要跨调用
  保留状态）。修完之后 reviewer 的真实回复：正确指出了标题文字对比度
  不够，还额外发现了一个我们自己都没注意到的、正文前面多了个项目符号
  `•` 的问题；后来再喂给它加了遮罩之后的版本，reviewer 依然给出了有
  真实设计判断力的意见（遮罩太满、把照片颜色洗淡了，建议改用渐变或局部
  卡片式遮罩；标题和副标题对齐方式不一致）——不是无脑说好或无脑挑刺，
  证明这条视觉闭环真的能提供除"跑通了"之外的、真实的审美判断信号。这个
  过程还顺带挖出第二个真实 bug：Gemini 的"思考"回复 `.content` 是一个
  含巨大不透明 signature 字段的 block 列表，不是纯字符串，`review_work`/
  `spawn_agent` 原来都是 `getattr(last, "content", None)` 直接返回，会把
  这个巨大 blob 原样泄漏进调用方的对话——改用
  `runtime_lg/messages.py` 里现成的 `extract_text()`（专门处理"content
  可能是字符串也可能是 block 列表"这种情况的函数）修好，两处都改了。
  **一个明确没做、如实说明的设计取舍**：WorkBuddy 那张图里 Content
  Reviewer 更像流水线上强制的一环；这里做成"指令驱动、模型自己判断
  要不要调用"（coordinator 的 `INSTRUCTIONS` 里建议在生成多页文档/多张
  幻灯片/套了背景图/开了修订模式之后调用，但不强制），理由是强制的话哪怕
  用户只要一份三行字的备忘录也要多付一次完整 LLM 调用的延迟和 token
  成本，跟"本地办公 agent"这个定位不划算——真实代价是这条路径依赖模型
  自己记得遵守指令，不是 100% 可靠，这轮没有再往"检测到超过 N 张幻灯片就
  强制触发"这类启发式方向做，留给以后如果发现模型经常不遵守再收紧。
  **跨 provider 的多模态支持**：后续这轮专门补了这一块的真机验证（之前
  只测过 Gemini）。`review_work` 发的图片内容块固定是 LangChain 的
  OpenAI 风格格式 `{"type": "image_url", "image_url": {"url":
  "data:image/png;base64,..."}}`。先读了这个环境里实际装的三个 provider
  库（`langchain-anthropic` 1.5.4 / `langchain-google-genai` 3.2.0 /
  `langchain-openai` 1.1.9）的源码，确认三个都有明确代码把这个格式转成
  各自原生格式——`langchain_anthropic.chat_models._format_image()` 用
  正则精确匹配 `^data:(image/.+);base64,(.+)$`，转成 Anthropic 原生的
  `{"type": "base64", "media_type": ..., "data": ...}`；Gemini/OpenAI
  原生就认这个格式。真机验证覆盖了两条路径：Gemini（前文已述）；以及
  `runtime_lg/providers.py` 里 `custom_providers`/原生 `openai:` 共用的
  同一个 `ChatOpenAI` 代码路径——用沙盒里现成的 `GLM_API_KEY` 真调用了
  `glm-4v-flash`，模型对真实图片给出了跟图片相关的回答（颜色判断不准，
  但那是模型能力问题，不是链路把图片当文本乱码处理）。
  **Anthropic 原生路径没有真实 key，但补了一次有意义的间接验证**：
  用户提出用 DeepSeek 的 Claude-SDK 兼容端点（`https://api.deepseek.com/
  anthropic`，`DEEPSEEK_API_KEY` 当 `anthropic_api_key` 用）顶替，因为
  这条端点走的正是 `langchain_anthropic.ChatAnthropic` 这同一个类、同一套
  `_format_image` 转换代码，不是另开一条路径。结果：请求本身完整走通、
  不报错；对比"发一张图 + 问'有没有图片'"和"不发图 + 问同样问题"两次
  调用，模型能正确区分"有/没有图片附件"（有图时答"是"，没图时答"否"）——
  说明图片内容块确实原样送达了模型这一层，不是被静默丢弃。但图片的具体
  内容被模型完全编造（1x1 红色像素被描述成"向日葵特写"）——这说明
  DeepSeek 在这条端点背后的模型本身大概率不具备真正的视觉理解能力，
  是模型能力的边界，不是 coscribe 这边转换代码的 bug。**如实的结论**：
  格式转换代码本身（OpenAI 风格 `image_url` → Anthropic 原生 `base64`
  格式）已经过真机验证，图片内容块确实会送达模型；但"一个真正支持视觉
  的 Claude 模型看到 `review_work` 传来的截图会不会给出真实、有用的
  审美判断"——这一点仍未验证，因为这个沙盒环境接触不到真正的 Anthropic
  API/真实 Claude 模型。
- **用户可选、按会话多选的 built-in Skills**（PPTX Slides / Excel
  Spreadsheets / Word Documents）——起因是用户提出"非技术办公室用户觉得
  配置 Connector 太陌生了，能不能像 Claude Code 一样有个 skill 开关"，
  讨论中先后收窄了两次：① 开关形态定成"设置面板里可同时勾选多个、随时
  切换"（用户明确类比 Claude Code，不是"会话开始时二选一"的 Persona
  弹窗那种）；② 用户主动用 Playwright MCP 的"配置出来的自愈循环"和
  Browser Use"内置的自主决策系统"这组对比，反推到 `review_work` 该怎么
  接进来——最终结论是"审查是否触发交给模型自己判断，就像 web_search 一样"，
  也就是**不**在 skill 开关背后挂一个强制调用 `review_work` 的机制。这一步
  在调研阶段找到了真实可行的实现方式（`runtime_lg/agent.py` 的
  `AgentMiddleware.wrap_tool_call` 钩子，`_CatchToolErrorsMiddleware` 已经
  在用同一套机制）但最终没有采用——如实记录在这里，不是没调研到，是用户
  明确决定不要。
  技术实现上有一个容易踩的坑：`tools/skills.py` 原有的 `load_skills`
  只扫 `settings.skills_dir`，这个目录在根 `.gitignore` 里被整个忽略
  （`/skills/`），是刻意设计成"用户/部署本地、不随仓库分发"的——如果直接
  把三个内置 skill 的 `SKILL.md` 塞进这个目录，装到别人机器上就是空的，
  完全违背"非技术用户不用配置就能用"的初衷。解法是新增一个独立的
  `load_builtin_skills()`，扫描包内固定路径 `src/coscribe/builtin_skills/`
  （hatchling 的 `packages = ["src/coscribe"]` 默认打包这个子目录下所有
  文件，不需要额外 `package-data` 配置，确认过 `.gitignore` 里没有覆盖
  这条路径），跟本地 `skills_dir` 是两个独立来源，`coordinator.py` 合并
  两者。三份 `SKILL.md` 的内容是重新写的，不是照抄——沿用这轮会话里已经
  验证过的纪律：Anthropic 官方 pptx/xlsx/docx skill 文档 source-available
  但不是开源，只能参考其设计思路（配色板、字号规范、QA 清单这类品类），
  具体文字要对着 coscribe 自己真实的工具签名（`add_pptx_scrim`、
  `format_xlsx_cells`、`write_docx` 的 `[TOC]`/`{{comment:}}` 语法）重新写。
  `build_coordinator_agent` 新增 `skill_names: set[str] | None` 参数，
  `None`（默认）保持"两个来源全部无条件拼接"的旧行为不变——`/api/tools`
  探针 agent 之类不知道按会话状态的调用方不受影响；只有
  `web/session.py` 的 `ChatSessionLG` 会传具体集合。按会话的开关状态
  复用了 Persona 已经建立的"sidecar 文件 + WS 消息实时重建"模式
  （`{thread_id}.skills`，仿照 `{thread_id}.persona`），但刻意去掉了
  Persona 那个"整个会话只能设置一次"的限制——`select_skills` 每次都是
  全量替换当前启用集合，可以来回开关任意次。
- 这四类内置工具（file/document/spreadsheet/presentation）共用同一个
  `tools/_workspace.py` 里的 `WorkspaceScope`：默认只能读写
  `COSCRIBE_WORKSPACE_ROOT` 之下的路径。用户可以额外配置
  `COSCRIBE_EXTRA_READABLE_DIRS`（只读）/`COSCRIBE_EXTRA_WRITABLE_DIRS`
  （可读可写，隐含只读）两组目录，让这些工具直接触达工作区之外的特定文件夹
  （典型场景：浏览器自动化 MCP 下载到 Downloads 的文件，不用先手动复制进
  workspace 才能编辑）。这两组目录**不是**放开到任意路径——只有显式列出的
  目录（及其子目录）才允许，路径穿越（`../`）和未列出的绝对路径依然按
  `PermissionError` 拒绝，跟工作区根目录本身的沙箱边界是同一套 `resolve()`
  校验逻辑，只是多了几个受信任的额外根。
- 不手写浏览器自动化 — 走 MCP（如 browser 类 MCP server）
- 不做邮件/日历集成 — 走用户自己配置的 MCP server
- 不做 OAuth 一键授权基础设施
- 不做显式工作流可视化编辑器
- 不做 OpenAPI 自动工具生成、A2A 协议支持
- ROADMAP.md Phase 4 的挂起/恢复原语（`sleep_until`/`sleep_for`/`wake_on`/
  `wake_on_event`）落地前先调研过整个 runtime_lg 的执行模型：一个"turn"
  就是 `ChatSessionLG._stream_turn` 的一次 `agent.astream(...)` 调用，
  `HumanInTheLoopMiddleware` 的审批中断虽然靠 checkpointer durable 保存，
  但**没有任何东西**会在无人连接的情况下自己调用 `Command(resume=...)`
  ——恢复审批只能靠一次真实的 WS 消息或重连。这意味着"按时间/按事件恢复"
  跟审批中断是两种形状的暂停，不能复用同一套机制：`tools/selfwake.py`
  只负责创建/查询/取消一条持久化的 `WakeRequest`（不需要活的 LLM
  client/checkpointer），真正"恢复"是 `runtime_lg/selfwake.py` 的
  `poll_due_wakes`——一个周期性轮询函数，判定到期后用一条合成的
  "Scheduled wake-up reached" 消息重新驱动 `ChatSessionLG.
  handle_user_message`，本质是把 README 里本来就有的"外部 cron 重新
  调用整个进程"模式自动化，而不是让 approval 中断长出一个新用途。
  轮询驱动方有两处：`web/app.py` 的 `lifespan` 起一个后台
  `asyncio.create_task` 循环（web 服务进程活着时零配置自动生效，间隔
  `COSCRIBE_WAKE_POLL_SECONDS`），以及 `coscribe --check-wakes`（给不想
  常驻 web 服务、只用外部 cron/systemd 的场景）。`wake_on(job_id)`/
  `wake_on_event(event_key)` 刻意只接真实存在的东西，不建空中楼阁：
  `job_id` 必须是 `tools/workflows.py` 里一个真实的 `WorkflowRun.run_id`
  （目前代码库里唯一"可轮询的后台任务"概念）；`wake_on_event` 配一个新
  `signal_event(event_key)` 工具，任何一次对话都能调用来触发某个 key——
  这是 Phase 5c（Slack 审批渠道）自己在 ROADMAP 里写的"依赖 Phase 4"
  这句话真正落地的那个挂钩点，不是提前建一个还没人调用的 webhook 接收端。
  **v1 明确留下、没有解决的一个限制**：如果某个线程当时正好被一个浏览器
  标签页实时打开着，后台轮询触发的恢复会正确写入 checkpointer（消息历史
  没问题），但回复只会发到轮询自己创建的、静默丢弃一切的 `_SilentSocket`
  上，不会推送进那个已经打开的真实 WebSocket——用户要刷新或重连才能看到，
  不是实时出现。修这个需要一套这个代码库目前没有的多写者广播机制，本轮
  刻意不做，只在这里和 README.md 里如实标注，不是没考虑到。
- ROADMAP.md Phase 4 收尾的 secrets hardening：动手前先摸清了现状——
  `.env`（内置 provider 的 API key）、`providers.json`（自定义 provider 的
  `api_key`）、`mcp.json`（MCP server 的 `env`/`headers`，包括 GitHub
  device flow 拿到的 OAuth bearer token）三处明文存储，全仓库 grep
  `os.chmod` 零命中——写文件时权限完全交给系统 umask 默认值。展示层的
  脱敏（`_mask()`）本来就有，缺的只是落盘这一步。两层、都可独立生效、
  互不依赖：(1) 无条件的文件权限加固——`runtime/secrets.py` 的
  `harden_file_permissions` 在每次写入这三个文件后 `chmod 0o600`，零新增
  依赖、没有失败模式；(2) 可选的 OS 钥匙串存储——新依赖 `keyring`
  （`store_secret`/`resolve_secret`/`delete_secret`），真有可用 backend
  时把真实密钥值存进钥匙串，磁盘上只留一个 `{"keyring_ref": ...}`
  引用；backend 不可用（这个沙盒自己的真实状态——headless Linux，没有
  Secret Service，`keyring.get_keyring()` 返回的就是
  `keyring.backends.fail.Keyring`）或任何一次调用失败，一律优雅降级回
  明文（仍然吃第(1)层的权限加固），保存动作本身永远不因为钥匙串不可用而
  失败——跟 `write_pptx` 对 LibreOffice 可选依赖的处理是同一个姿态。
  `.env` 是扁平 `KEY=value` 文本，存不了 `{"keyring_ref": ...}` 这种
  dict，所以单独有一套 sentinel 字符串前缀的编码方式
  （`env_value_for_storage`/`env_resolve_secret_for_display`/
  `resolve_env_keyring_refs`），在 `cli.py`/`web/app.py` 启动时、
  `load_dotenv(...)` 之后立刻把 `os.environ` 里的 sentinel 换成真实值，
  这样 anthropic/openai/google-genai 各自 SDK 自己那句 `os.getenv(...)`
  读到的永远是真实密钥，不用改 SDK 调用点。一个刻意的诚实取舍：钥匙串存的
  密钥不会跟着一份复制出去的 `.env`/`state_dir` 一起搬家——换机器或钥匙串
  不可用时，`resolve_secret` 直接抛出明确的 `RuntimeError`（"re-enter it
  via Settings"），不会假装成功或悄悄吞掉。旧版本已经落盘的明文、以及
  钥匙串不可用时新写入的明文，都用同一种"就是个普通字符串"的形状处理，
  不强制迁移。
- Scheduled Tasks（`tools/scheduled_tasks.py`/`runtime_lg/scheduled_tasks.py`）
  刻意新起一个 `ScheduledTrigger` 概念，而不是给 `tools/selfwake.py` 的
  `WakeRequest` 加一个"周期性"字段：`WakeRequest` 的语义是"暂停*这个*
  对话，之后恢复它"——线程内、临时的（`list_wakes`/`cancel_wake` 只看
  调用方自己那个线程的挂起项），而 `ScheduledTrigger` 是一个独立的、
  全局可见、可管理的实体，不需要先有一个正在进行的对话才能创建——这跟
  `tools/workflows.py` 的 `Workflow`（全局、具名、持久化）更像，而不是
  `WakeRequest`。因此 `build_scheduled_task_tools(state_dir)` 跟
  `build_workflow_tools`一样不接 `thread_id` 参数（对比
  `build_selfwake_tools(thread_id, state_dir)`），`list_scheduled_tasks`
  返回的是全部触发器，不只是当前线程创建的那些。真正复用的是模式而不是
  数据模型：`runtime_lg/scheduled_tasks.py` 的 `poll_due_scheduled_tasks`
  是 `poll_due_wakes` 的直接同构（同一个 `get_session` 回调签名、直接
  import 复用 `runtime_lg/selfwake.py` 的 `_SilentSocket`，同样"一次
  触发失败不影响本轮其余到期项"的防御姿态），`web/app.py` 的后台轮询
  循环也只是在原有那一个 `asyncio.create_task` 里多调一次
  `poll_due_scheduled_tasks`，不是起第二个循环；`cli.py` 的
  `--check-wakes` 同理，一次性把两类"到期项"都检查一遍，不新增一个 flag。
  每个 trigger 都拿到自己独立、持久化的 thread（`scheduled-<trigger_id>`，
  而不是创建它时所在的那个线程）——这样一来周期性触发永远不会把消息插进
  用户当时正在用的对话里，而 `prompt` 型 trigger 的历史会在这个专属线程里
  真正跨轮次累积，复用已有的 `COSCRIBE_AUTO_COMPACT_THRESHOLD` 防止无限
  增长，不需要新写压缩逻辑。为了让 `runtime_lg/scheduled_tasks.py`（不能
  直接 import `ChatSessionLG`，会循环 import）有个真正的公开入口去跑一个
  已保存的 workflow，`web/session.py` 新增了一个薄的公开方法
  `run_saved_workflow`，直接转调原本私有的 `_run_workflow_lg`（`/runworkflow`
  斜杠命令背后那个），没有 UI 相关的副作用（不发 `workflow_run_started`/
  `tasks_changed` 这些 WS 消息，因为调用方压根没有真实 WebSocket）。
  跟 selfwake 完全一样的**限制、没有解决**：`ScheduleRule.at` 里的
  `"HH:MM"`/完整时间戳解析成的是不带时区的本地 `datetime`（纯 stdlib
  `datetime`/`calendar` 运算，不引入 cron 解析依赖），因为 coscribe 是
  单用户、本地优先的软件，没有"每个用户自己的时区"这个概念——机器所在
  时区就是唯一的时区。如果 coscribe 未来真的被远程托管、给不同时区的用户
  用，这里需要重新设计，本轮如实标注，不假装已经支持。
- Scheduled Tasks 落地后针对一个真实运行的 `coscribe-web` + 真实 Gemini
  做 live check 时，发现并修复了一个货真价实的 bug，而不是本轮新引入的：
  一个走 `_SilentSocket`（`runtime_lg/selfwake.py`，selfwake 和
  Scheduled Tasks 共用）驱动的无人值守 turn，一旦碰到需要审批的 gated
  工具调用（`write_file` 这类 `WRITE_LOCAL`），会把调用它的协程**永久
  挂起**，而不是像 README/本文件之前描述的那样"审批中断持久化在
  checkpointer 里，等用户下次打开那个线程再处理"。根因在
  `web/session.py` 的 `_resolve_pending_approvals`：无论调用方是不是真实
  连接，它都无条件创建一个 `asyncio.Future` 并 `await` 它，这个 future
  只有真实的、通过 WebSocket 发来的 `approval_response` 消息才能
  resolve——`_SilentSocket` 什么都不会发，于是 `await` 永远不返回。真实
  影响比"这一个 trigger 卡住"更严重：`poll_due_wakes`/
  `poll_due_scheduled_tasks` 在 `web/app.py` 唯一那个后台轮询循环里是
  顺序 `await` 的，第一个撞上 gated 工具调用的无人值守 fire 就会把
  **整个轮询器**永久卡死，之后所有其他 wake/scheduled task 都不会再触发，
  且没有任何报错——这是实测出来的，不是从代码读出来的猜测（用
  `asyncio.wait_for(..., timeout=90)` 包一层复现，确认 90 秒内不返回）。
  修法：新增 `web/session.py` 的 `_can_resolve_approvals(websocket)`——
  鸭子类型检查 `websocket` 是否有 `can_resolve_approvals` 这个属性（不能
  用 isinstance，`web/session.py` 不能反向 import
  `runtime_lg/selfwake.py`，会循环 import），真实的 fastapi
  `WebSocket`/cli.py 的 `_CliSocket` 都没有这个属性，`getattr` 默认
  `True`，行为不变；`_SilentSocket` 新增
  `can_resolve_approvals = False`。两处无条件调用
  `_resolve_pending_approvals` 的地方（`_handle_user_message_locked` 的
  主 turn 逻辑、`_run_workflow_agent_mode`）现在先查这个标记，为 False 时
  完全跳过调用——中断照样留在 checkpointer 里持久暂停（`
  resume_after_reconnect` 本来就是设计来处理这种"稍后来一个真实连接"的
  情形），只是不再傻等一个不可能来的回答。`_run_workflow_agent_mode`
  额外做了一步：跳过后重新 `aget_state` 检查是否真的还卡在中断上，是的话
  返回 `status="failed"` 加一句解释（而不是像原来那样谎报
  `"completed"`），因为实际的写入动作根本没执行，谎报成功比明确报失败
  更危险。`handle_user_message`（prompt 型 trigger/wake 走的路径）没有
  返回值给调用方读，沿用 `poll_due_wakes` 早就有的"不管实际结果，直接标
  `woken`/`completed`"这个简化，本轮没有改——跟 workflow 型 trigger的
  精确状态上报不对称，README 里如实写明了这个不对称，不是遗漏。回归测试：
  `tests/test_selfwake_resume.py`/`tests/test_scheduled_tasks_resume.py`
  各加了一个用 `asyncio.wait_for(..., timeout=5)` 包住调用的测试，让"又
  卡住了"这种回归会明确抛 `TimeoutError` 失败，而不是把整个测试套件也
  一起挂起。
- 前端"Nav rail, Create/Run split"重设计：起因是产品方向讨论里提出现在的
  配色（橙棕 `--accent`）不像 Claude 家族，导航也堆了太多东西——顶栏一个
  "Sessions"下拉塞了新建会话/历史会话/Workflows/Recent Runs 四类不相关
  的东西，Scheduled Tasks 又单独藏在 Settings 弹窗里，跟 Workflows 概念
  上明明是一类("不需要你盯着的自动化")却分散在两个完全不同的入口。
  设计方案（Claude design 出的稿，参考真实文件路径/token 值验证过，不是
  凭空生成）：(1) 配色换成 Modernist 红色调色板（`frontend/src/index.css`
  的 `:root`/`@media (prefers-color-scheme: dark)` 两块），`--border`
  改用 `color-mix(in srgb, var(--fg) N%, transparent)` 算出来，不再是
  写死的十六进制，`--danger` 复用 `--accent` 的色阶，没有单独发明一个绿色
  "success"色——那类状态改用中性文字配一个 check 图标表示；
  `--user-bubble`/`--agent-bubble`/`--shadow` 不在这次调色板范围内（早于
  这次改动、纯聊天气泡相关），把原来偏蓝紫的 user bubble 改成中性的
  `--card-bg`，避免跟新调色板本身的红/中性色系冲突，没有另外发明一个新
  hex 值。(2) 顶栏的"Sessions"下拉 + 齿轮图标，换成新的
  `components/NavRail.tsx`——默认收起成一条 48px 宽只有一个图标的窄栏，
  鼠标悬浮时展开成 272px 宽的完整导航面板（`position: absolute` 悬浮在
  内容区上层，不挤压布局，跟设计稿里"展开时盖住 Workflows 网格"的效果
  一致），面板顶部是 Create/Run 二态切换（`App.tsx` 新增的 `navMode`
  state），Create 态下面是原来 `SessionMenu.tsx` 的"+ New Session"/
  Recent Session 逻辑照搬过来的，Run 态下面是 Workflows/Scheduled/
  History 三个子 tab（`RunTab` state，`NavRail`/`RunPanel` 两边共享，
  从 `App.tsx` 往下传）。面板底部固定一个 Settings 行，取代原来顶栏齿轮
  图标的位置去开 `SettingsModal`。(3) 新增 `components/RunPanel.tsx`
  作为 Run 态下真正的主内容区（`NavRail` 展开时那份是给快速预览/直接
  触发用的精简镜像，数据各自独立 fetch，不是同一份状态——跟 Settings
  各个 tab"各自管各自那块数据"的既有惯例一致，不是新发明的模式）：
  Workflows 用两栏卡片网格（名字/summary/"Run now"按钮，直接复用
  `onRunWorkflow`，点击后 `App.tsx` 会自动切回 Create 态，不然你根本
  看不到它在跑）；Scheduled 把原来独立成一个 Settings tab 的
  `ScheduledTasksTab.tsx`整个搬过来（该文件已删除），列表项换成设计稿
  里的开关（`ToggleSwitch`，一个纯 CSS 小组件）取代原来的 Pause/Resume
  文字按钮，创建表单默认收起、点"+ New scheduled task"才展开，逻辑（校验/
  `createScheduledTask`调用）原样保留；History 合并了原来
  `WorkflowsTab.tsx`的"Recent runs"区块，状态改成图标（
  `CheckCircleIcon`/`ClockIcon`/`XCircleIcon`）而不是文字徽章，
  设计稿里的"N outputs"徽章因为后端 `WorkflowRun` 并没有真的记录输出
  文件列表，如实改成了"N steps"（数 `run.steps`），没有假装后端有一个
  实际不存在的 outputs 能力。`SettingsModal.tsx` 里的 Workflows tab
  刻意保留没删——设计稿的 mapping 表只写了"minus the Scheduled Tasks
  tab"，Settings > Workflows 侧重管理（能删除 workflow、看 chain 步骤
  详情），Run > Workflows 侧重执行（跑一下），两者定位不同，不是同一个
  视图的重复。`SessionMenu.tsx`本身因为逻辑全部搬进了 `NavRail.tsx`，
  确认没有任何地方还 import 它之后整个删除，不留死代码。
