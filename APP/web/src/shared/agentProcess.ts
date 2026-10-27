// Vocabulary for the derivation trace that a benchmark run stores per case.
// The stage and code names come from the agent orchestrator, so this table is
// the translation layer between those records and the reviewer-facing page.
// Stage names, routes and dispositions are shared with the numbering page, so
// they are read from runProcessCopy instead of being repeated here.

import {
  humanizeCode,
  PANEL_COPY,
  stateCopy,
  toolLabel as sharedToolLabel,
  type ProcessLanguage,
} from "./runProcessCopy";

// Tool and action wording lives with the rest of the recorded-code wording, so both
// pages read the same short names.
export const toolLabel = sharedToolLabel;

export type ProcessState = "info" | "passed" | "warning" | "failed";

export interface ProcessStep {
  position: number;
  stage: string;
  state: ProcessState;
  facts: Record<string, unknown>;
}

export interface ProcessAction {
  action_id?: string;
  action_type?: string;
  accepted?: boolean;
  valid_before?: boolean;
  valid_after?: boolean;
  score_before?: number | null;
  score_after?: number | null;
  target_regions?: string[];
  notes?: string[];
  [key: string]: unknown;
}

export interface ProcessLlmCall {
  source?: string;
  model?: string;
  provider?: string;
  status_code?: number | null;
  elapsed_sec?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface CaseProcess {
  available: boolean;
  run_id: string;
  seqname: string;
  model: string;
  provider: string;
  method_backend: string;
  planner_policy: string;
  planner_sources: string[];
  template_name: string;
  status: string;
  error: string;
  elapsed_sec: number | null;
  route: string;
  disposition: string;
  difficulty_tier: string;
  difficulty_score: number | null;
  route_confidence: number | null;
  hard_guard_passed: boolean | null;
  base_preserved: boolean | null;
  legality_passed: boolean | null;
  legality_reasons: string[];
  warnings: string[];
  stop_reason: string;
  counts: Record<string, number | null>;
  tokens: Record<string, number | null>;
  guard: Record<string, unknown>;
  provenance: Record<string, unknown>;
  steps: ProcessStep[];
  actions: ProcessAction[];
  llm_calls: ProcessLlmCall[];
}

// Values these keys take are themselves code names, so they need a table too.
const VALUE_LABELS: Record<string, Record<string, string>> = {
  alignment_backend: {
    cmalign: "cmalign",
    template_regional_dp: "区域动态规划",
  },
  bridge_status: {
    conforming: "符合约定",
    nonconforming: "不符合约定",
    unresolved: "未定",
  },
  calibration_disposition: {
    accepted: "已接受",
    investigate: "需复核",
  },
  candidate_backend: {
    cmalign: "cmalign",
    template_regional_dp: "区域动态规划",
  },
  coordinate_assertion: {
    none: "无",
  },
  disposition: {
    abstained: "已转人工",
    evidence_only: "只读证据",
    accept: "已接受",
    accepted: "已接受",
    investigate: "需复核",
    rejected: "未通过",
    evaluated: "已评估",
    reported: "已记录",
  },
  decision: {
    accept: "接受",
    investigate: "复核",
    tool: "工具",
  },
  domain_hint: {
    archaea: "古菌",
    bacteria: "细菌",
    eukaryota: "真核",
    unknown: "未知",
  },
  effect: {
    request_acceptor_stem_endpoint_reconstruction: "重建氨酰臂茎末端",
    request_independent_trnascan_variable_boundary_projection: "独立投影可变区边界",
  },
  effective_domain_hint: {
    archaea: "古菌",
    bacteria: "细菌",
    eukaryota: "真核",
    unknown: "未知",
  },
  execution_class: {
    deterministic_scientific_tool: "确定性科学工具",
    reference_lookup_evidence_only: "文献检索（只读证据）",
    bounded_llm_coordinate_editor: "模型改写坐标（受规则约束）",
  },
  group: {
    all: "跨域",
    arch: "古菌",
    bact: "细菌",
    euk: "真核",
  },
  input_type: {
    mature: "成熟序列",
  },
  intron_status: {
    unknown: "未知",
  },
  left_arm_status: {
    histidine_g_minus_1_and_positions_1_7_mapped: "组氨酸 G-1 与 1-7 已映射",
    mapped_from_complete_structure_hit: "由完整结构命中映射",
    single_structural_5prime_extension_mapped_to_minus_1: "5' 端单残基延伸映射为 -1",
  },
  mitochondrial_subtype: {
    unknown: "未知",
  },
  mode: {
    long_variable_arm_from_independent_pairs: "独立配对得到的长可变臂",
  },
  model_group: {
    all: "跨域",
    arch: "古菌",
    bact: "细菌",
    euk: "真核",
  },
  model_groups: {
    all: "跨域",
    arch: "古菌",
    bact: "细菌",
    euk: "真核",
  },
  numbering_backend: {
    cmalign: "cmalign",
    template_regional_dp: "区域动态规划",
  },
  parent_backend: {
    cmalign: "cmalign",
    template_regional_dp: "区域动态规划",
  },
  phase: {
    evidence_review: "证据复核",
    initial_tool_selection: "初始工具选择",
    soft_risk_review: "软风险复核",
  },
  planner_source: {
    harness_fast_path: "规则直选",
    llm: "模型判断",
  },
  policy: {
    distinct_template_acceptor_tail_consensus_v1: "独立模板末端共识",
    escalation_only_with_original_candidate_rollback: "仅升级并回滚原候选",
    sequential_evidence_review_with_original_candidate_rollback: "逐项证据复核并回滚原候选",
    trnascan_acceptor_stem_endpoint_v1: "tRNAscan 氨酰臂茎末端",
    trnascan_plus_distinct_template_consensus_v1: "tRNAscan 加独立模板共识",
    trnascan_variable_boundary_projection_v1: "tRNAscan 可变区边界投影",
    validated_candidate_score_margin_v1: "候选得分增益门槛校验",
  },
  purpose: {
    assign_structure_homologous_sprinzl_coordinates: "按结构同源分配 Sprinzl 坐标",
    compare_numbering_cm_candidates: "比较 CM 编号候选",
  },
  region: {
    "Aminoacyl arm 3' end": "氨酰臂3'",
    "Aminoacyl arm 5' end": "氨酰臂5'",
    "Anticodon loop + Anticodon stem": "反密码子区",
    "D loop + D stem": "D区",
    "T loop + T stem": "T区",
    "Variable loop": "可变区",
  },
  requested_domain_hint: {
    archaea: "古菌",
    bacteria: "细菌",
    eukaryota: "真核",
    unknown: "未知",
  },
  root_source: {
    cm_tool: "CM 工具",
  },
  route: {
    canonical: "经典",
  },
  scope: {
    "archaeal canonical tRNA": "古菌经典 tRNA",
    "bacterial canonical tRNA": "细菌经典 tRNA",
    "eukaryotic canonical tRNA": "真核经典 tRNA",
    region: "局部区域",
    snapshot: "整条快照",
  },
  search_mode: {
    A: "古菌",
    B: "细菌",
    E: "真核",
  },
  selected_group: {
    all: "跨域",
    arch: "古菌",
    bact: "细菌",
    euk: "真核",
  },
  selection_mode: {
    portfolio: "模型组",
  },
  selection_policy: {
    calibrated_domain_selector: "校准域选择器",
    calibrated_domain_selector_abstained: "校准域选择器弃权",
  },
  source: {
    cm_calibrated_domain_selector: "CM 校准域选择器",
    unknown: "未知",
  },
  specialist: {
    acceptor_endpoint_reviewer: "氨酰臂末端复核",
    cm_numbering_specialist: "CM 编号",
    legacy_reference_specialist: "模板检索",
    recovery_specialist: "回退恢复",
    soft_risk_reviewer: "软风险复核",
    template_specialist: "模板比对",
    variable_boundary_reviewer: "可变区边界复核",
    variable_boundary_specialist: "可变区边界",
  },
  status: {
    calibrated: "已校准",
    conflict: "冲突",
    resolved: "已解决",
    unresolved: "未定",
  },
  strategy: {
    agent_toolbox: "智能体工具箱",
  },
  structural_check_reason: {
    independent_structure_sequence_and_shape_length_mismatch: "独立结构的序列与形状长度不一致",
  },
  structure_view_policy: {
    replace_acceptor_pairs_and_preserve_parent_other_regions: "替换氨酰臂配对，其余保留",
    replace_variable_pairs_and_preserve_parent_other_regions: "替换可变区配对，其余保留",
  },
  tail_assignment_basis: {
    structure_endpoint_observed_complete_cca: "结构末端含完整 CCA",
    structure_endpoint_observed_extension: "结构末端有延伸",
    structure_endpoint_observed_incomplete_terminal: "结构末端不完整",
  },
  target_region: {
    "Aminoacyl arm 3' end": "氨酰臂3'",
    "Aminoacyl arm 5' end": "氨酰臂5'",
    "Anticodon loop + Anticodon stem": "反密码子区",
    "D loop + D stem": "D区",
    "T loop + T stem": "T区",
    "Variable loop": "可变区",
  },
  terminal_motif_observation: {
    incomplete_terminal: "末端不完整",
    no_extension: "无延伸",
    non_cca_extension: "非 CCA 延伸",
  },
  tier: {
    high: "高",
    low: "低",
    moderate: "中",
  },
  trigger_basis: {
    narrow_acceptor_tail_label_pattern: "氨酰臂末端编号过窄",
    terminal_cca_prefix_not_mapped_to_74_75: "CCA 前缀未映射到 74-75",
  },
  trna_class: {
    canonical: "经典",
  },
  validator: {
    shared_hard_guards: "共享硬规则",
    sprinzl_variable_bridge: "Sprinzl 可变桥",
  },
  variable_loop_dialect_policy: {
    preserve_existing_ordered_v1_v5_subset_without_new_homology_claim: "保留已有 V1-V5 子集，不作新的同源判断",
  },
};

const CODE_LABELS: Record<string, string> = {
  accepted_by_shared_hard_guards: "通过共享硬规则",
  acceptor_endpoint_reviewer: "氨酰臂末端复核",
  acceptor_tail_boundary_unresolved: "受体尾边界未定",
  acceptor_tail_reconciliation_applied: "受体尾协调已应用",
  acceptor_tail_reconciliation_unresolved: "受体尾协调未定",
  acceptor_tail_template_consensus_not_established_parent_retained: "受体尾模板共识未成立，保留父方案",
  action_contract_missing_regions: "动作契约缺少目标区域",
  agent_accept: "智能体判定接受",
  cca_74_76_require_discriminator_73: "74-76 需要 73 判别位",
  cm_acceptor_tail_boundary_ambiguity: "CM 受体尾边界歧义",
  cm_average_posterior_below_threshold: "CM 平均后验低于阈值",
  cm_candidate_not_accepted: "CM 候选未被接受",
  cm_domain_calibration_abstained: "CM 域校准弃权",
  cm_domain_selection_requires_investigation: "CM 域选择需复核",
  cm_numbering_candidate_generated: "生成 CM 编号候选",
  cm_portfolio_low_raw_score_margin: "CM 组合原始得分边际偏低",
  cm_variable_boundary_structure_ambiguity: "CM 可变环边界歧义",
  compact_insertions_no_change: "插入位压缩无改动",
  consecutive_no_gain: "连续无增益",
  evidence_sufficient: "证据已充分",
  harness_evidence_first: "按证据优先规则",
  harness_fast_path: "规则直选",
  llm: "模型判断",
  independent_structure_has_no_discriminator_candidate: "独立结构未给出判别残基候选",
  independent_variable_structure_projection_ready: "独立可变区结构投影已就绪",
  insertion_without_parent: "插入位缺少父位置",
  legacy_reference_candidate_generated: "生成模板检索候选",
  legacy_reference_specialist: "模板检索",
  legal_and_no_open_issues: "合法且无未决问题",
  long_variable_arm_requires_reference_consensus: "长可变臂需参考共识",
  no_actions_available: "无可选动作",
  no_strong_family_candidates: "无强同类候选",
  non_cca_terminal_extension_reassigned_after_73: "73 之后非 CCA 末端被重排",
  non_cca_terminal_extension_requires_review: "非 CCA 末端延伸需复核",
  region_dp_repair_missing_focus_regions: "DP 修复缺少焦点区域",
  rejected_by_shared_hard_guards: "未通过共享硬规则",
  soft_risk_reviewer: "软风险复核",
  template_family_mismatch: "模板家族不匹配",
  tool_error: "工具错误",
  valid_candidate: "候选合法",
  valid_candidate_no_actions_available: "候选合法且无可选动作",
  valid_cm_evidence: "CM 证据有效",
  variable_arm_loop_length_outside_v1_v5_capacity: "可变臂长度超出 V1-V5 容量",
  variable_boundary_multi_evidence_consensus_established: "可变环多证据共识成立",
  variable_boundary_multi_evidence_consensus_not_established: "可变环多证据共识未成立",
  variable_boundary_reconciliation_applied: "可变环边界协调已应用",
  variable_boundary_reconciliation_unresolved: "可变环边界协调未定",
  variable_boundary_reference_search_failed_parent_retained: "可变环参考检索失败，保留父方案",
  variable_boundary_reviewer: "可变区边界复核",
  variable_boundary_structure_stage_completed: "可变环结构阶段完成",
  variable_boundary_structure_unresolved: "可变环边界结构未定",
  variable_bridge_sprinzl_convention_violation: "可变桥违反 Sprinzl 约定",
  variable_bridge_structure_review_required: "可变桥需结构复核",
  variable_singleton_occupied: "可变区单占位被占用",
  warning_count_high: "警告计数偏高",
  warning_count_nonzero: "警告计数非零",
};

const FACT_LABELS: Record<string, string> = {
  accepted_as_current: "作为当前结果",
  acceptor_endpoint_precedence: "氨酰臂末端优先",
  acceptor_tail_reconciliation: "氨酰臂末端核对",
  acceptor_tail_template_consensus: "氨酰臂模板共识",
  acceptor_template_precedence: "氨酰臂模板优先",
  action_id: "动作",
  action_type: "动作类型",
  agent_decision: "智能体判定",
  alignment_backend: "比对后端",
  allowed_action_ids: "可选动作",
  allowed_tools: "可用工具",
  answer_free: "无需作答",
  anticodon: "反密码子",
  arch: "古菌",
  available: "可用",
  average_posterior: "平均后验",
  backend: "后端",
  bact: "细菌",
  base_preserved: "序列保持",
  bit_score: "bit 得分",
  bridge_expected: "桥接应有",
  bridge_observed: "桥接实测",
  bridge_status: "桥接状态",
  calibrated_probabilities: "校准域概率",
  calibration_disposition: "校准判定",
  candidate_backend: "候选后端",
  candidate_change_summary: "候选改动",
  candidate_comparison: "候选比较",
  candidate_count: "候选数",
  candidate_labels: "候选编号",
  candidate_reasons: "候选理由",
  candidate_right_arm_starts: "候选右臂起点",
  candidate_score: "候选得分",
  candidate_snapshot_id: "候选快照",
  candidate_template_name: "候选模板",
  candidate_valid: "候选合法",
  canonical_pair_count: "经典配对数",
  changed_residue_indices: "变化残基位置",
  code: "代码",
  command_options: "命令参数",
  confidence: "置信度",
  coordinate_assertion: "坐标断言",
  cpu_per_case: "单序列 CPU",
  decision: "决策",
  deterministic: "确定性",
  difficulty_score: "难度分",
  discriminator_residue_index: "判别残基位置",
  disposition: "处置",
  distinct_sequence_support: "独立序列支持",
  domain_hint: "域提示",
  domain_probabilities_calibrated: "域概率已校准",
  domain_routing: "域路由",
  effect: "作用",
  effective_domain_hint: "生效域提示",
  elapsed_ms: "耗时（毫秒）",
  elapsed_sec: "耗时（秒）",
  eligible_support: "合格支持数",
  endpoint_is_ambiguous: "末端存在歧义",
  error: "错误",
  error_type: "错误类型",
  established: "已确立",
  euk: "真核",
  exact_input_templates_excluded: "已排除同源模板",
  executable: "可执行程序",
  execution_class: "执行类别",
  expected_labels: "应有坐标",
  fallback_reason: "回退原因",
  gold_coordinates_used: "使用金标准坐标",
  group: "模型组",
  hard_case: "困难样本",
  hard_guard_reasons: "硬规则原因",
  hard_valid: "硬规则通过",
  hit_end_residue_index: "命中末端残基位置",
  hit_start_residue_index: "命中起始残基位置",
  independent_structure_terminal_suffix: "独立结构末端后缀",
  independent_structure_terminal_suffix_appended: "已附加独立结构末端后缀",
  input_type: "输入类型",
  intron_status: "内含子状态",
  isotype: "同工受体",
  labels_monotonic: "编号单调",
  left_arm_reconciled: "左臂已核对",
  left_arm_status: "左臂状态",
  llm_called: "调用大模型",
  llm_used: "调用大模型",
  loop_length: "环长",
  mature_cca_claim: "成熟 CCA 判定",
  memory_hit_count: "记忆命中",
  minimum_score_gain: "最小得分增益",
  minimum_support: "最小支持数",
  missing_discriminator_73: "缺少 73 位判别残基",
  mitochondrial_subtype: "线粒体亚型",
  mode: "模式",
  model: "模型",
  model_group: "模型组",
  model_groups: "模型组",
  model_sha256: "模型哈希",
  multi_template_reference_search: "多模板检索",
  name: "名称",
  notes: "备注",
  numbering_backend: "编号后端",
  observed: "实测值",
  observed_labels: "实际坐标",
  observed_left_run_length: "实测左侧连续长度",
  observed_loop_labels: "实测环区编号",
  observed_tail_labels: "实测末端编号",
  options: "参数",
  original_candidate_preserved: "保留原候选",
  out_of_scope: "超出范围",
  package: "软件包",
  pair_count: "配对数",
  parent_backend: "父候选后端",
  parent_cm_model: "父协方差模型",
  parent_cm_portfolio: "父模型组",
  parent_snapshot_id: "父快照",
  parent_template_name: "父模板",
  passed: "通过",
  path: "文件路径",
  phase: "阶段",
  planned_actions: "计划动作",
  planner_source: "规划来源",
  policy: "策略",
  posterior_codes: "后验码",
  prefix_length: "前缀长度",
  provider: "提供方",
  purpose: "用途",
  raw_bit_score_margin: "原始得分边际",
  reason: "原因",
  reason_code: "原因码",
  reason_count: "原因数",
  reasons: "原因",
  reference_consensus: "参考共识",
  reference_consensus_established: "参考共识已确立",
  reference_consensus_support: "参考共识支持数",
  region: "区域",
  requested_domain_hint: "请求域提示",
  required: "必需",
  residue_count: "残基数",
  resolved: "已解析",
  result: "结果",
  review_index: "复核序号",
  role: "依据",
  root_source: "基线来源",
  round: "轮次",
  route: "路线",
  rule: "规则",
  schema_version: "记录版本",
  scientific_contract: "科学约束",
  scope: "适用范围",
  score_after: "动作后得分",
  score_before: "动作前得分",
  score_gain: "得分增益",
  score_margin_calibrated: "得分边际已校准",
  search_mode: "检索模式",
  selected_group: "选定组",
  selected_model: "选定模型",
  selection_policy: "选择策略",
  sha256: "哈希",
  soft_risk_review: "软风险复核",
  source: "来源",
  specialist: "专用复核器",
  status: "状态",
  stop_reason: "停止原因",
  strategy: "策略",
  structural_check_reason: "结构核查原因",
  structure_hit_start_residue_index: "结构命中起始残基位置",
  structure_length: "结构长度",
  structure_pair_count: "结构配对数",
  structure_proposal: "结构提案",
  structure_sha256: "结构哈希",
  structure_view_policy: "结构视图策略",
  suspicious_insertion_labels: "可疑插入编号",
  t_stem_49_65_anchor_confirmed: "T 茎 49-65 锚点已确认",
  tail_assignment_basis: "末端赋值依据",
  tail_coordinate_signature: "末端坐标特征",
  tail_length: "末端长度",
  target_region: "目标区域",
  target_regions: "目标区域",
  template_name: "模板",
  template_sequence_sha256: "模板序列哈希",
  terminal_coordinate_labels: "末端坐标编号",
  terminal_extension_residue_indices: "末端延伸残基位置",
  terminal_extension_sequence: "末端延伸序列",
  terminal_motif_observation: "末端模体观察",
  terminal_prefix: "末端前缀",
  threshold: "阈值",
  tier: "难度层级",
  tool: "工具",
  tool_name: "工具",
  tool_observation: "工具观察",
  tool_observations: "工具观察",
  tool_provenance: "工具来源",
  top_k: "前 K 个",
  trigger_basis: "触发依据",
  trigger_reasons: "触发原因",
  trna_class: "tRNA 类别",
  valid_after: "动作后合法",
  valid_before: "动作前合法",
  validator: "校验器",
  variable_boundary_precedence: "可变区边界优先",
  variable_boundary_reconciliation: "可变环边界核对",
  variable_labels_after: "调整后可变区编号",
  variable_labels_before: "调整前可变区编号",
  variable_loop_dialect_policy: "可变环方言策略",
  variable_residue_count: "可变区残基数",
  variable_span_residue_indices: "可变区跨度残基位置",
  version: "版本",
  warning_count: "警告数",
};

// Codes and recorded keys carry Chinese wording only; other languages read the
// identifier itself as words, which is what the numbering page already does.
export function stateLabel(state: ProcessState, language: ProcessLanguage): string {
  return stateCopy(String(state), language);
}

export function factLabel(key: string, language: ProcessLanguage): string {
  if (language === "zh-CN") return FACT_LABELS[key] || key;
  return humanizeCode(key);
}

export function codeLabel(raw: unknown, language: ProcessLanguage): string {
  const code = String(raw ?? "").trim();
  if (!code) return "";
  const head = code.split(":")[0];
  const detail = code.slice(head.length + 1);
  const label = language === "zh-CN" ? CODE_LABELS[head] || head : humanizeCode(head);
  if (!detail) return label;
  return language === "zh-CN" ? `${label}（${detail}）` : `${label} (${detail})`;
}

const TOOL_VALUE_KEYS = new Set([
  "tool",
  "tool_name",
  "action_type",
  "allowed_tools",
  "available_tools",
  "selected_tool",
  "fallback_tool",
  "primary_tool",
]);

function valueLabel(key: string, value: string, language: ProcessLanguage): string {
  if (TOOL_VALUE_KEYS.has(key)) return toolLabel(value, language);
  if (["notes", "reasons", "warnings", "reason", "reason_code", "stop_reason"].includes(key)) return codeLabel(value, language);
  if (language !== "zh-CN") return value;
  const table = VALUE_LABELS[key];
  return table && table[value] ? table[value] : value;
}

function numberText(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return Math.abs(value) >= 1 ? value.toFixed(2) : value.toFixed(3);
}

export function formatFactValue(key: string, value: unknown, language: ProcessLanguage): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? PANEL_COPY[language].yes : PANEL_COPY[language].no;
  if (typeof value === "number") return numberText(value);
  if (Array.isArray(value)) {
    const parts = value.map((item) => {
      if (typeof item === "string") return valueLabel(key, item, language);
      if (item && typeof item === "object") return compactObject(item as Record<string, unknown>, language);
      return String(item);
    });
    return parts.join(language === "zh-CN" ? "、" : ", ");
  }
  if (typeof value === "object") return compactObject(value as Record<string, unknown>, language);
  return valueLabel(key, String(value), language);
}

function compactObject(value: Record<string, unknown>, language: ProcessLanguage): string {
  return Object.entries(value)
    .map(([key, item]) => `${factLabel(key, language)} ${formatFactValue(key, item, language)}`)
    .join(language === "zh-CN" ? "，" : ", ");
}

export function formatSeconds(value: unknown, language: ProcessLanguage): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return language === "zh-CN" ? `${num.toFixed(1)} 秒` : `${num.toFixed(1)} s`;
}

export function formatTokens(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString("en-US") : "-";
}
