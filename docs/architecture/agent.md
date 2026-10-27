# 项目 Agent 说明

这份文档描述当前在线 `AgentAlignmentService` 的职责和运行顺序。数据与实验协议见
[SprinzlMap 投稿版系统与实验协议](./sprinzlmap-submission-v2.md)。

## 它是什么

当前系统是一个 **确定性 Harness 内的条件式 LLM Agent**，也不是让大模型直接生成三行对齐结果。

- Harness 先执行一个可审计的科学证据工具，高置信候选可以不调用大模型直接返回。
- 不确定样本由 LLM Agent 从 Harness 允许的 action 中选择后续工具或停止处置。
- 科学工具负责生成编号候选或局部证据。
- Harness 负责输入范围、状态、预算、硬规则、候选接受、回滚和最终处置。

默认在线策略名是 `agent_toolbox`，大模型触发模式为 `uncertain_only`。`cm_only` 和
`legacy_only` 用于显式基准或消融。

## 输入和输出

输入由 `AlignmentRequest` 表示，至少包含目标序列、反密码子提示、输入类型、来源范围、CCA、内含子、tRNA 类别、模型模式和运行覆盖项。

输出是最终候选及其可审计元数据：

- Sprinzl 编号行、模板行和目标行；
- 结构视图、警告和科学工具 provenance；
- Agent 每轮允许的 action、模型选择和 reason code；
- 每个工具候选的硬规则结果、分数、接受或回滚；
- 最终 `accepted`、`investigate` 或 `abstained` 处置。

系统不返回或展示模型的私有思维链。

## 运行顺序

### 1. 建立输入契约

系统先保存输入类型和来源范围。特殊类型预检属于 Harness scope gate：线粒体、病毒、Sec tRNA 或含内含子的前体序列，只有在存在经过验证的专用工具和坐标映射时才能继续 canonical numbering，否则返回结构化 abstention。

### 2. 执行首个证据工具

对 canonical mature tRNA，Harness 建立包含以下能力的工具箱：

- `align_numbering_cm`；
- `align_legacy_reference`。

前者用 numbering CM 生成结构同源坐标证据；后者用模板检索、结构分区和六区段 DP 生成
参考候选。默认先执行适配域的 numbering CM，候选合法稳定时走确定性短路径。

`use_llm=false` 的网页 `DP only` 模式默认只暴露 legacy/DP 工具，保证该对照没有 LLM planner 和 LLM postcheck 调用。显式 `cm_first` 或 `cm_only` 用于 CM 基准。

### 3. 不确定时由 LLM Agent 选择 action

首个候选失败、置信度不足或证据冲突时，`ConstrainedLLMActionPlanner` 才接收结构化
observation 和 action allowlist。模型只能返回：

```json
{
  "decision": "tool",
  "action_id": "initial:align_numbering_cm",
  "reason_code": "request_structure_homology_evidence"
}
```

或者返回 Harness 本轮允许的 `accept`、`investigate`、`abstain`。不存在于 allowlist 的 action 会被拒绝，并记录为确定性 fallback；模型不能通过 JSON 参数写入任意坐标。

### 4. 工具生成候选

`BranchExecutor` 执行一个 typed action。当前工具包括 numbering CM，模板检索、结构分区和六区段 DP，模板切换，边界细化，结构优先或模板软先验的局部复查，区段 DP 重算，可变区拓扑整理，反密码子重锚定，插入列整理，以及受 scope gate 约束的特殊类型证据工具。

工具只能返回候选，不能自行宣布结果正确。

### 5. Harness 验证和回滚

每个候选立即经过 `HardGuards` 和 `OnlineRanker`。硬规则检查至少覆盖输入碱基保持、Sprinzl 标签合法性、标签顺序、固定臂配对、可变区拓扑和特殊路线状态。

非法候选不会替换当前状态。合法候选只有在效用更高时才成为当前或历史最优版本。所有接受和回滚都进入 `AgentWorkState.action_history`。

### 6. Observation 返回 Agent

Agent 下一轮看到的是压缩后的结构化状态，而不是隐藏的执行对象：当前候选来源和 numbering backend，CM bit score、posterior、模型 margin 和校准结果，结构区域证据和配对数量，hard-guard 原因、开放问题和当前分数，已尝试 action 及其结果，以及剩余动作、规划和局部复查预算。

Agent 再选择一个允许的工具，或提出停止处置。

### 7. Harness 决定最终状态

- `accepted`：最终候选通过硬规则；
- `investigate`：存在编号候选，但仍有证据冲突或规则失败；
- `abstained`：没有可辩护候选、输入越界或工具不可用。

LLM 可以提出处置，但 Harness 仍拥有最终裁决权。例如，未通过硬规则的候选不能因为模型请求 `accept` 而被接受。

## 大模型发挥什么作用

大模型当前有两个受限角色。

第一，LLM Agent 在运行层选择科学工具和停止时机。这是 Agentic AI 架构中的主要模型作用。

第二，只有当 Agent 明确选择局部复查 action 时，大模型才可在指定结构区段提出受限候选。候选仍必须保持碱基顺序、限定编辑区域并通过硬规则，否则回滚。

因此，大模型不负责定义 Sprinzl 科学规则，也不替代 CM、DP 或结构模型。它负责在工具能力、冲突证据和预算之间做连续决策。

## 当前研究口径

代码接入 LLM planner 只能证明模型真实参与了系统，不能自动证明它提高准确率。正式投稿仍需在同一提交、同一 toolbox 和同一预算下比较：LLM planner、固定 planner、CM-only 和 legacy-only，并报告准确率、覆盖率、选择性风险、成本和失败类型。

策略开发只使用 calibration split。冻结 test split 不用于调 prompt、action policy 或阈值。
