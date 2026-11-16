# GitHub 开源测试工作台选型调研报告

> 数据来源：GitHub REST API 实测（star/fork/最近提交/协议），检索时间 2026-11-10
> 调研范围：测试需求管理 / 用例生成 / 用例执行（功能 / 接口 / 自动化）相关开源项目

---

## 1. 全景分层图（按 star 排序）

```
基础设施层   playwright 99.7k★ | hoppscotch 80.8k★ | bruno 48.3k★ | pytest 14.7k★ | allure2 5.6k★
AI 执行层    midscene 15.6k★ | keploy 19.6k★ | qodo-cover 5.7k★
综合平台     metersphere 13.6k★ | angustester 1.8k★ | testsigma 1.2k★
用例管理     testlink 1.6k★ | kiwi TCMS 1.3k★ | AgileTC 728★(停更) | nitrate 307★
AI 测试平台  qa-agent 1.3k★ | hercules 1.2k★ | WHartTest 1.1k★ | AI-TCG 23★
接口自动化   httprunner 4.3k★ | gauge 3.2k★ | postcat 5.5k★ | LunarLink 542★
```

## 2. 核心对比表（GitHub API 实测）

| 项目 | Star | Fork | 最近提交 | 协议 | 技术栈 | 定位 |
|------|-----:|-----:|---------|------|--------|------|
| microsoft/playwright | 99,698 | 6,829 | 2026-11-10 | Apache-2.0 | TS | UI 自动化引擎（事实标准） |
| hoppscotch/hoppscotch | 80,769 | 6,234 | 2026-11-05 | MIT | TS | 开源 Postman，API 调试 |
| usebruno/bruno | 48,311 | 3,090 | 2026-11-09 | MIT | JS | 离线优先 API 客户端 |
| keploy/keploy | 19,615 | 2,450 | 2026-11-10 | Apache-2.0 | Go | 流量录制回放式 API 测试 |
| web-infra-dev/midscene | 15,563 | 1,218 | 2026-11-10 | MIT | TS | 字节出品，AI 视觉驱动 E2E（叠在 Playwright 上） |
| metersphere/metersphere | 13,620 | 2,847 | 2026-11-09 | GPL-3 类* | Java+Vue | 最成熟一站式测试平台 |
| pytest-dev/pytest | 14,677 | 3,614 | 2026-11-09 | MIT | Python | 测试框架事实标准 |
| qodo-ai/qodo-cover | 5,685 | 573 | 2026-04-05 | AGPL-3.0 | Python | AI 单元测试生成/覆盖率 |
| Postcatlab/postcat | 5,531 | 794 | 2026-02-27 | Apache-2.0 | JS | 可扩展 API 工具平台 |
| allure-framework/allure2 | 5,578 | 781 | 2026-11-10 | Apache-2.0 | Java | 测试报告事实标准 |
| httprunner/httprunner | 4,308 | 1,284 | 2025-12-11 | Apache-2.0 | Python | YAML 驱动接口测试 |
| getgauge/gauge | 3,199 | 357 | 2026-11-09 | Apache-2.0 | Go | 轻量跨平台测试自动化 |
| TestLinkOpenSourceTRMS/testlink | 1,626 | 631 | 2025-12-08 | GPL-2.0 | PHP | 老牌用例管理（衰退期） |
| AngusKit/AngusTester | 1,777 | 201 | 2026-10-25 | GPL-3.0 | YAML/Go | AI-native 测试平台（API/Web/性能） |
| bug0inc/qa-agent | 1,318 | 191 | 2026-09-25 | 自定义* | TS | Playwright AI 增强，回归自愈 |
| kiwitcms/Kiwi | 1,291 | 371 | 2026-11-09 | GPL-2.0 | Python/Django | 老牌 TCMS，持续维护 16.x |
| testzeus-ai/hercules | 1,217 | 202 | 2026-10-29 | AGPL-3.0 | Python | 开源测试 Agent（Gherkin 进结果出） |
| testsigmahq/testsigma | 1,230 | 260 | 2026-08-05 | Apache-2.0 | Java | AI-first 测试平台 |
| MGdaasLab/WHartTest | 1,080 | 181 | 2026-11-06 | 自定义* | Django+Vue+LangGraph | AI 全流程测试平台（需求→用例→执行） |
| tahitimoon/LunarLink | 542 | 68 | 2026-10-10 | MIT | Python+Vue | HttpRunner+Django 接口自动化平台 |
| didi/AgileTC | 728 | 283 | 2024-09-06 | Apache-2.0 | JS | 滴滴脑图协同用例管理（已停更） |
| Nitrate/Nitrate | 307 | 122 | 2026-11-09 | GPL-2.0 | Python/Django | Kiwi 的前身，仍在维护 |
| ChiufungLee/AI-Test-Case-Generator | 23 | 7 | 2026-10-24 | MIT | FastAPI+LangGraph+RAG | 需求→评审→用例生成→接口测试 |
| agentlabs-dev/auto-inspector | 262 | 23 | 2025-02-14 | Apache-2.0 | TS | 规格驱动轻量测试 Agent（停滞） |

\* NOASSERTION = 自定义协议或未标准标注，使用前需读 LICENSE 原文。

## 3. 分层分析

### 3.1 综合平台型（认可度最高，但不是我们的答案）

**MeterSphere（13.6k★）** 是该领域绝对的头部：用例管理/接口测试/UI 测试/缺陷/报告全都有，AI 用例生成也已内置。但 Java + Vue 重栈、GPL v3 协议、社区版功能边界收紧，二开成本高。**结论：只抄它的数据模型与模块划分（我们已在原型文档中这么做了），不引代码。**

**AngusTester（1.8k★）/ Testsigma（1.2k★）**：一个主打 YAML 一门语言覆盖 API/Web/性能，一个主打 AI-first 自然语言用例。理念值得借鉴（YAML 中间格式 = 用例与脚本解耦），但栈不匹配且协议偏紧（GPL-3 / Java 栈）。

### 3.2 用例管理型（流程参考价值）

- **Kiwi TCMS（1.3k★，GPL-2，持续维护）**：Test Plan / Test Run / 执行记录遥测的数据模型最经典，Python 栈可读性好。
- **AgileTC（728★，已停更 2 年）**：脑图协同用例管理的交互设计仍然领先，脑图视图需求可参考它的实现思路。
- **TestLink（1.6k★，衰退）**：不推荐。

### 3.3 AI 测试平台型（与我们的愿景同方向）

- **Midscene.js（15.6k★，MIT，字节，日更）** — 本轮调研**认可度最高的 AI 测试项目**。自然语言 aiAct/aiAssert、视觉定位不依赖选择器、模型可换（DeepSeek/Qwen）、MIT 干净。它不管理用例只管执行，与我们的工作台是**完美互补**关系，建议把「P3 可选集成」升级为「P3 正式集成」。
- **WHartTest（1.1k★，2026-10 自荐后快速起量）**：功能覆盖与我们规划几乎 1:1（需求评审→AI 用例→接口→UI→Agent/MCP），增速最快，但协议 NOASSERTION 且官方自述 Skills 权限大、仅限内网。**作为竞品跟踪对象，每月复盘一次它的架构演进。**
- **qa-agent（1.3k★）/ Hercules（1.2k★）**：执行层 Agent 思路（自愈定位、Gherkin 进结果出）可借鉴，不建议直接依赖。
- **AI-Test-Case-Generator（23★）**：认可度最低但与 MVP 契合度最高。低 star 是真实风险（社区验证少、可能停更），**两项缓解**：① P0 先本地验证代码质量；② MIT 协议允许我们 fork 后完全自主演进，即使上游停更也不受影响。

### 3.4 接口/自动化专项（可单点借力）

- **Keploy（19.6k★，Apache-2.0）**：流量录制→自动生成接口用例+数据，思路独特。若后端服务有真实流量，可作为接口用例的「冷启动加速器」备选。
- **HttpRunner（4.3k★）+ LunarLink（542★，MIT）**：YAML 用例格式 + Django 平台化的国产实践，LunarLink 规模小但正是「轻量平台」路线的活样本。
- **Allure（5.6k★）**：pytest 执行器出报告可直接对接 Allure 结果格式，工作台报告模块兼容它可白嫖生态。

## 4. 认可度 × 契合度矩阵

```
认可度
 高 ┤  MeterSphere●          Midscene●
    │  Hoppscotch●  Playwright●
 中 ┤  Kiwi●  AngusTester●   WHartTest○  Keploy●
    │  Hercules○  qa-agent○
 低 ┤                        AI-TCG◎(底座)  LunarLink○
    └─────────────────────────────────────────────
        低          中           高        契合度

  ● 参考借鉴   ○ 跟踪观察   ◎ 当前底座（P0 验证后定去留）
```

## 5. 对本项目的结论

1. **维持路线 A 不变**，但加两条修正：
   - **P0 验证权重提高**：AI-TCG 认可度仅 23★，必须先验证代码质量与 Python 3.13 兼容性；同时把 WHartTest 作为 Plan B 底座候选。
   - **Midscene.js 从"可选"升级为自动化层正式组件**（15.6k★ + MIT + 字节维护 + 模型可换，与多模型决策天然契合）。
2. **借鉴清单**：MeterSphere（模块/数据模型）、Kiwi（执行记录模型）、AgileTC（脑图视图）、AngusTester（YAML 中间格式）、Keploy（流量生成用例，备选）、Allure（报告格式兼容）。
3. **协议红线**：GPL 系（MeterSphere/Kiwi/TestLink/AngusTester/Nitrate）只看设计不抄代码；AGPL 系（Hercules/qodo-cover）连服务端集成都要规避；MIT/Apache 系（AI-TCG/Midscene/LunarLink/Keploy）可放心使用。
4. **观察机制**：WHartTest、qa-agent 每月复盘一次；AI-TCG 若 3 个月无提交则触发 Plan B 切换评估。

## 附：原始检索数据

- 14 + 9 个仓库的 GitHub API 实测记录（star/fork/issues/pushed_at/license/archived），检索时间 2026-11-10 11:39 GMT+8
- 搜索关键词：测试平台 / test case management / AI test generation / api test platform / test automation platform
