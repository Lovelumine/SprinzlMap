// Shared wording for the run-process panel used by both the numbering page and
// the curation page.  The stage, route and disposition names come from the agent
// orchestrator, so this file is the single translation layer between those
// records and the text a reviewer reads.

// Coded values (dispositions, reasons, tiers) share one vocabulary with the
// curation page, so the timeline and the detail panel never disagree.
import { codeLabel, factLabel, formatFactValue } from "./agentProcess";

export type ProcessLanguage = "en" | "zh-CN" | "de" | "fr";

export interface ProcessEventLike {
  stage?: string;
  [key: string]: unknown;
}

export function humanizeCode(value: unknown): string {
  const text = String(value || "").trim();
  if (!text) return "";
  return text
    .replace(/:/g, ": ")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ");
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function routeLabel(route: unknown, language: ProcessLanguage): string {
  const key = String(route || "canonical").trim().toLowerCase();
  const labels: Record<ProcessLanguage, Record<string, string>> = {
    en: { canonical: "Canonical", mitochondrial: "Mitochondrial", selenocysteine: "Selenocysteine", archaeal_ncintron: "Archaeal intron", specialized: "Specialized", out_of_scope: "Out of scope" },
    "zh-CN": { canonical: "经典路线", mitochondrial: "线粒体路线", selenocysteine: "硒代半胱氨酸路线", archaeal_ncintron: "古菌内含子路线", specialized: "专用路线", out_of_scope: "范围外" },
    de: { canonical: "Kanonisch", mitochondrial: "Mitochondrial", selenocysteine: "Selenocystein", archaeal_ncintron: "Archäales Intron", specialized: "Spezialisiert", out_of_scope: "Außerhalb" },
    fr: { canonical: "Canonique", mitochondrial: "Mitochondrial", selenocysteine: "Sélénocystéine", archaeal_ncintron: "Intron archéen", specialized: "Spécialisé", out_of_scope: "Hors périmètre" },
  };
  if (labels[language][key]) return labels[language][key];
  const value = key.replace(/_/g, " ");
  return value.charAt(0).toUpperCase() + value.slice(1);
}

// Tool and action codes are what the orchestrator records, but a reviewer reads
// a step list.  Each entry gives the shortest phrase that still says what the
// tool did; anything absent from the table falls back to the recorded code.
const TOOL_COPY: Record<string, { en: string; "zh-CN": string }> = {
  align_numbering_cm: { en: "CM numbering", "zh-CN": "CM 编号" },
  score_numbering_models: { en: "CM model comparison", "zh-CN": "CM 模型比较" },
  align_legacy_reference: { en: "template alignment", "zh-CN": "模板比对" },
  reconcile_variable_boundary_with_structure: { en: "variable boundary", "zh-CN": "可变环边界调整" },
  repair_acceptor_tail_with_structure: { en: "acceptor tail repair", "zh-CN": "氨酰臂末端修复" },
  reconcile_acceptor_tail_with_template_consensus: { en: "acceptor tail consensus", "zh-CN": "氨酰臂末端核对" },
  assign_structure_homologous_spacing: { en: "homologous spacing", "zh-CN": "结构同源间距" },
  deterministic_terminal_extension_reassignment: { en: "terminal reassignment", "zh-CN": "末端重排" },
  compact_insertions: { en: "compact insertions", "zh-CN": "压缩插入位" },
  repair_region_with_dp_only: { en: "DP-only region repair", "zh-CN": "DP 区域修复" },
  select_template: { en: "template selection", "zh-CN": "模板选择" },
  refine_boundary: { en: "boundary refinement", "zh-CN": "边界微调" },
  reanchor_anticodon: { en: "anticodon reanchor", "zh-CN": "反密码子重锚" },
  switch_template_topk: { en: "template switch", "zh-CN": "切换模板" },
  search_literature: { en: "literature note", "zh-CN": "文献检索" },
  propose_coordinate_revision: { en: "model coordinate edit", "zh-CN": "模型坐标修订" },
};

export function toolLabel(value: unknown, language: ProcessLanguage): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  // Recorded actions are often prefixed with the phase that issued them.
  const tail = (raw.split(":").pop() || raw).trim().replace(/\s+/g, "_").toLowerCase();
  const entry = TOOL_COPY[tail];
  if (entry) return language === "zh-CN" ? entry["zh-CN"] : entry.en;
  return language === "zh-CN" ? raw : humanizeCode(raw);
}

const GROUP_COPY: Record<string, { en: string; "zh-CN": string }> = {
  arch: { en: "archaeal model", "zh-CN": "古菌模型" },
  bact: { en: "bacterial model", "zh-CN": "细菌模型" },
  euk: { en: "eukaryotic model", "zh-CN": "真核模型" },
  all: { en: "cross-domain model", "zh-CN": "跨域模型" },
};

function groupLabel(group: unknown, language: ProcessLanguage): string {
  const raw = String(group ?? "").trim();
  if (!raw) return "";
  const entry = GROUP_COPY[raw.toLowerCase()];
  if (!entry) return humanizeCode(raw);
  return language === "zh-CN" ? entry["zh-CN"] : entry.en;
}

export function processStageLabel(stage: unknown, language: ProcessLanguage): string {
  const key = String(stage || "").trim();
  const labels: Record<string, string> = {
    input_classified: "Input metadata",
    scope_gate_evaluated: "Available tools determined",
    agent_decision: "Agent selected action",
    model_alignment: "CM model comparison",
    candidate_built: "Numbering candidate generated",
    canonical_route_selected: "Canonical route selected",
    root_snapshot_built: "Numbering candidate generated",
    agent_disposition_requested: "Agent requested disposition",
    candidate_disposition: "Candidate disposition",
    soft_risk_review_scheduled: "Soft risk review scheduled",
    terminal_coordinate_policy: "Terminal coordinate policy",
    initial_tool_failed: "Initial tool failed",
    legacy_root_failed: "Legacy root failed",
    primary_tool_failed: "Primary tool failed",
    fallback_tool_called: "Fallback tool called",
    fallback_tool_failed: "Fallback tool failed",
    difficulty_assessed: "Case difficulty assessed",
    route_assessed: "Biological route assessed",
    special_route_selected: "Special route selected",
    special_route_abstained: "Special route abstained",
    special_tool_observation: "Special model observed",
    special_tool_failed: "Special model failed",
    special_canonical_like_tool_failed: "Canonical-like model failed",
    canonical_like_structure_assessed: "Canonical-like structure assessed",
    root_recovery_tool_called: "Recovery tool called",
    root_recovery_abstained: "Recovery abstained",
    harness_fast_path_accepted: "Accepted by the fixed rules",
    planning: "Agent planned",
    tool_started: "Tool started",
    tool_observation: "Tool observed",
    deterministic_validation: "Deterministic validation",
    action_executed: "Action evaluated",
    action_failed: "Action failed",
    guard_evaluated: "Hard guards evaluated",
    search_stopped: "Run stopped",
    scientific_interpretation: "Scientific interpretation",
  };
  const translated: Record<Exclude<ProcessLanguage, "en">, Record<string, string>> = {
    "zh-CN": {
      input_classified: "输入元数据",
      scope_gate_evaluated: "确定可用工具",
      agent_decision: "智能体选择动作",
      model_alignment: "CM 模型比对",
      candidate_built: "生成编号候选",
      canonical_route_selected: "选定经典路线",
      root_snapshot_built: "生成编号候选",
      agent_disposition_requested: "智能体给出处置建议",
      candidate_disposition: "候选处置",
      soft_risk_review_scheduled: "软风险复核排期",
      terminal_coordinate_policy: "末端坐标策略",
      initial_tool_failed: "初始工具失败",
      legacy_root_failed: "旧版初始路线失败",
      primary_tool_failed: "主要工具失败",
      fallback_tool_called: "调用备用工具",
      fallback_tool_failed: "备用工具失败",
      difficulty_assessed: "评估样本难度",
      route_assessed: "评估生物学路线",
      special_route_selected: "选择专用路线",
      special_route_abstained: "专用路线转人工",
      special_tool_observation: "专用模型观察",
      special_tool_failed: "专用模型失败",
      special_canonical_like_tool_failed: "经典结构模型失败",
      canonical_like_structure_assessed: "评估经典结构特征",
      root_recovery_tool_called: "调用恢复工具",
      root_recovery_abstained: "恢复路线转人工",
      harness_fast_path_accepted: "规则直接接受",
      planning: "智能体规划",
      tool_started: "工具开始运行",
      tool_observation: "工具返回观察",
      deterministic_validation: "确定性验证",
      action_executed: "动作评估",
      action_failed: "动作失败",
      guard_evaluated: "硬性规则检查",
      search_stopped: "运行停止",
      scientific_interpretation: "科学解释",
    },
    de: {
      input_classified: "Eingabe klassifiziert",
      scope_gate_evaluated: "Geltungsbereich geprüft",
      agent_decision: "Agent wählte Aktion",
      agent_disposition_requested: "Agent empfahl Entscheidung",
      canonical_route_selected: "Primärroute gewählt",
      candidate_disposition: "Kandidatenbehandlung",
      soft_risk_review_scheduled: "Weiche Risikoüberprüfung geplant",
      terminal_coordinate_policy: "Endpositionspolitik",
      root_snapshot_built: "Primärkandidat erstellt",
      fallback_tool_called: "Ersatzwerkzeug aufgerufen",
      special_route_selected: "Spezialroute gewählt",
      special_tool_observation: "Spezialmodell beobachtet",
      canonical_like_structure_assessed: "Kanonische Struktur bewertet",
      harness_fast_path_accepted: "Direkt durch Regeln akzeptiert",
      planning: "Agentenplanung",
      tool_started: "Werkzeug gestartet",
      tool_observation: "Werkzeugbeobachtung",
      deterministic_validation: "Deterministische Validierung",
      action_executed: "Aktion bewertet",
      action_failed: "Aktion fehlgeschlagen",
      guard_evaluated: "Harte Regeln geprüft",
      search_stopped: "Lauf beendet",
    },
    fr: {
      input_classified: "Entrée classée",
      scope_gate_evaluated: "Portée vérifiée",
      agent_decision: "Action choisie par l'agent",
      agent_disposition_requested: "Décision proposée par l'agent",
      canonical_route_selected: "Voie principale choisie",
      candidate_disposition: "Traitement du candidat",
      soft_risk_review_scheduled: "Révision de risque douce programmée",
      terminal_coordinate_policy: "Politique de position terminale",
      root_snapshot_built: "Candidat principal construit",
      fallback_tool_called: "Outil de secours appelé",
      special_route_selected: "Voie spécialisée choisie",
      special_tool_observation: "Observation du modèle spécialisé",
      canonical_like_structure_assessed: "Structure canonique évaluée",
      harness_fast_path_accepted: "Accepté directement par les règles",
      planning: "Planification de l'agent",
      tool_started: "Outil lancé",
      tool_observation: "Observation de l'outil",
      deterministic_validation: "Validation déterministe",
      action_executed: "Action évaluée",
      action_failed: "Échec de l'action",
      guard_evaluated: "Règles strictes vérifiées",
      search_stopped: "Analyse arrêtée",
    },
  };
  if (language !== "en" && translated[language][key]) {
    return translated[language][key];
  }
  return labels[key] || key.replace(/_/g, " ");
}

// The CCA tail answer is one of three states, and it shows up in two panels.
const CCA_COPY = {
  confirmed: { en: "CCA 74-76 confirmed", "zh-CN": "CCA 74-76 已确认", de: "CCA 74-76 bestätigt", fr: "CCA 74-76 confirmé" },
  unconfirmed: { en: "CCA-like tail seen, coordinates not confirmed", "zh-CN": "有 CCA 样末端，坐标未确认", de: "CCA-artiges Ende gesehen, Zuordnung offen", fr: "Extrémité de type CCA, attribution non confirmée" },
  absent: { en: "no terminal CCA seen", "zh-CN": "未见末端 CCA", de: "kein terminales CCA gesehen", fr: "pas d'extrémité CCA" },
} as const;

function ccaState(terminalCca: boolean, confirmed: boolean, language: ProcessLanguage): string {
  if (!terminalCca) return CCA_COPY.absent[language];
  return confirmed ? CCA_COPY.confirmed[language] : CCA_COPY.unconfirmed[language];
}

export function processEventDetail(event: ProcessEventLike, language: ProcessLanguage): string {
  const stage = String(event.stage || "");
  if (stage === "scientific_interpretation") {
    const status = String(event.status || "running");
    const copies: Record<ProcessLanguage, [string, string, string]> = {
      en: ["Writing the interpretation", "Interpretation recorded", "Interpretation unavailable"],
      "zh-CN": ["正在撰写科学解读", "已生成科学解读", "解读未生成"],
      de: ["Deutung wird erstellt", "Deutung aufgezeichnet", "Keine Deutung"],
      fr: ["Rédaction de l'interprétation", "Interprétation enregistrée", "Interprétation indisponible"],
    };
    const [running, completed, failed] = copies[language];
    if (status === "running") return running;
    if (status === "failed" || status === "error") return failed;
    return completed;
  }
  if (stage === "input_classified") {
    const missing: Record<ProcessLanguage, string> = {
      en: "not provided",
      "zh-CN": "未提供",
      de: "nicht angegeben",
      fr: "non fourni",
    };
    const valueOrMissing = (value: unknown) => {
      const normalized = String(value || "").trim();
      return !normalized || normalized === "unknown" ? missing[language] : normalized;
    };
    const inputType = formatFactValue("input_type", valueOrMissing(event.input_type), language);
    const source = formatFactValue("domain_hint", valueOrMissing(event.domain_hint), language);
    const trnaClass = formatFactValue("trna_class", valueOrMissing(event.trna_class), language);
    const contract = asRecord(event.input_contract);
    const sequenceObservations = asRecord(contract.sequence_observations);
    const ccaEvidence = asRecord(contract.cca_evidence);
    const intronEvidence = asRecord(contract.intron_evidence);
    const terminalCca = sequenceObservations.terminal_cca_observed === true;
    const ccaConfirmed = ccaEvidence.coordinate_assignment_allowed === true;
    const ccaLabel = ccaState(terminalCca, ccaConfirmed, language);
    const intronStatus = formatFactValue("intron_status", valueOrMissing(intronEvidence.status_claim || event.intron_status), language);
    const labels: Record<ProcessLanguage, [string, string, string, string, string]> = {
      en: ["Input", "Source", "tRNA class", "CCA", "intron"],
      "zh-CN": ["输入类型", "来源域", "tRNA 类别", "CCA", "内含子"],
      de: ["Eingabe", "Herkunft", "tRNA-Klasse", "CCA", "Intron"],
      fr: ["Entrée", "Origine", "Classe d'ARNt", "CCA", "intron"],
    };
    const [inputLabel, sourceLabel, classLabel, ccaFieldLabel, intronLabel] = labels[language];
    return `${inputLabel}: ${inputType} · ${sourceLabel}: ${source} · ${classLabel}: ${trnaClass} · ${ccaFieldLabel}: ${ccaLabel} · ${intronLabel}: ${intronStatus}`;
  }
  const recorded: Record<ProcessLanguage, string> = {
    en: "Step completed; no additional detail was emitted.",
    "zh-CN": "步骤已完成，后端没有发送更多细节。",
    de: "Schritt abgeschlossen; es wurden keine weiteren Details ausgegeben.",
    fr: "Étape terminée ; aucun détail supplémentaire n'a été transmis.",
  };
  if (stage === "scope_gate_evaluated") {
    const tools = Array.isArray(event.allowed_tools) ? event.allowed_tools : [];
    const descriptions: Record<ProcessLanguage, string> = {
      en: "Available tools",
      "zh-CN": "可用工具",
      de: "Verfügbare Optionen (nur später ausgewählte Werkzeuge werden ausgeführt)",
      fr: "Options disponibles (seuls les outils choisis ensuite sont exécutés)",
    };
    return tools.length
      ? `${descriptions[language]}: ${tools.map((item) => toolLabel(item, language)).join(" · ")}`
      : descriptions[language];
  }
  if (stage === "agent_decision") {
    const selectedTool = toolLabel(event.action_id, language);
    const reasonCode = String(event.reason_code || "");
    const reason = reasonCode === "harness_evidence_first"
      ? (language === "zh-CN" ? "按证据优先规则" : "selected by the evidence-first policy")
      : reasonCode ? humanizeCode(reasonCode) : "";
    const planner = String(event.planner_source || "");
    const plannerNote = planner === "harness_fast_path"
      ? (language === "zh-CN" ? "规则直选，未调用大模型" : "the rules chose the tool, no LLM call")
      : planner === "llm" ? (language === "zh-CN" ? "由模型选工具" : "the model chose the tool") : "";
    const selected = language === "zh-CN" ? `选择：${selectedTool}` : `Selected: ${selectedTool}`;
    return [selected, reason, plannerNote].filter(Boolean).join(" · ");
  }
  if (stage === "model_alignment") {
    const group = String(event.selected_group || event.model_group || "");
    const parts: string[] = [];
    if (group) parts.push(groupLabel(group, language));
    if (event.bit_score !== undefined && event.bit_score !== null) parts.push(`${factLabel("bit_score", language)} ${Number(event.bit_score).toFixed(2)}`);
    if (event.average_posterior !== undefined && event.average_posterior !== null) parts.push(`${factLabel("average_posterior", language)} ${Number(event.average_posterior).toFixed(3)}`);
    if (event.residue_count !== undefined) parts.push(language === "zh-CN" ? `${Number(event.residue_count)} 个残基已编号` : `${Number(event.residue_count)} residues numbered`);
    if (!parts.length) return language === "zh-CN" ? "正在比较模型并生成结构比对候选。" : "Comparing models and generating a structure-based alignment candidate.";
    return parts.join(" · ");
  }
  if (stage === "deterministic_validation") {
    const checks: string[] = [];
    if (event.base_preserved === true) checks.push(language === "zh-CN" ? "输入碱基顺序完整保留" : "input bases preserved in order");
    if (event.base_preserved === false) checks.push(language === "zh-CN" ? "输入碱基保留检查失败" : "input-base preservation failed");
    if (event.labels_monotonic === true) checks.push(language === "zh-CN" ? "坐标顺序单调" : "coordinates are monotonic");
    if (event.labels_monotonic === false) checks.push(language === "zh-CN" ? "坐标顺序检查失败" : "coordinate ordering failed");
    if (event.warning_count !== undefined) checks.push(language === "zh-CN" ? `${Number(event.warning_count)} 条警告` : `${Number(event.warning_count)} warning(s)`);
    const disposition = String(event.status || event.disposition || "");
    if (disposition) checks.push(formatFactValue("disposition", disposition, language));
    return checks.length
      ? checks.join(" · ")
      : language === "zh-CN" ? "验证事件未附带检查结果。" : "Validation event did not include check outcomes.";
  }
  if (stage === "candidate_built" || stage === "root_snapshot_built") {
    const parts: string[] = [];
    if (event.template_name) parts.push(`${language === "zh-CN" ? "参考模板" : "reference"}: ${event.template_name}`);
    if (Number(event.label_count || 0) > 0) parts.push(language === "zh-CN" ? `${Number(event.label_count)} 个坐标列` : `${Number(event.label_count)} coordinate columns`);
    if (event.root_source) {
      parts.push(language === "zh-CN"
        ? formatFactValue("root_source", event.root_source, language)
        : humanizeCode(event.root_source));
    }
    return parts.length ? parts.join(" · ") : (language === "zh-CN" ? "已生成待验证的编号候选。" : "A numbering candidate was generated for evaluation.");
  }
  if (stage === "difficulty_assessed") {
    const parts: string[] = [];
    if (event.tier) parts.push(language === "zh-CN" ? `难度等级：${formatFactValue("tier", event.tier, language)}` : `tier: ${formatFactValue("tier", event.tier, language)}`);
    if (event.difficulty_score !== undefined) parts.push(`${language === "zh-CN" ? "难度分数" : "difficulty score"} ${Number(event.difficulty_score).toFixed(2)}`);
    if (event.hard_case === true) parts.push(language === "zh-CN" ? "标记为困难样本" : "flagged as a hard case");
    if (Array.isArray(event.reasons) && event.reasons.length) parts.push(event.reasons.map((item) => codeLabel(item, language)).join(" · "));
    return parts.join(" · ") || (language === "zh-CN" ? "未记录难度评估结果。" : "Difficulty assessment was not recorded.");
  }
  if (stage === "route_assessed") {
    const parts = [routeLabel(event.route, language)];
    if (event.confidence !== undefined && event.confidence !== null) parts.push(`${language === "zh-CN" ? "置信度" : "confidence"} ${Number(event.confidence).toFixed(2)}`);
    if (event.out_of_scope === true) parts.push(language === "zh-CN" ? "超出适用范围" : "out of scope");
    return parts.join(" · ");
  }
  if (stage === "harness_fast_path_accepted") {
    const messages: Record<ProcessLanguage, string> = {
      en: "Candidate passed the checks · no LLM call",
      "zh-CN": "候选已通过检查 · 未调用大模型",
      de: "Kandidat bestanden · kein LLM-Aufruf",
      fr: "Candidat validé · aucun appel de LLM",
    };
    if (event.candidate_valid === true || event.llm_called === false) return messages[language];
    return language === "zh-CN"
      ? "系统停止继续搜索；查看候选和规则验证步骤了解处理结果。"
      : "Search ended; see the candidate and validation steps for the recorded outcome.";
  }
  if (stage === "agent_disposition_requested") {
    const model = String(event.model || event.planner_source || "");
    return [formatFactValue("disposition", event.disposition || event.decision, language), model]
      .filter(Boolean)
      .join(" · ");
  }
  if (event.error) return `${event.error_type || "Error"}: ${event.error}`;
  if (stage === "special_route_selected") {
    return `${routeLabel(event.route, language)} · ${formatFactValue("disposition", event.disposition || event.status || "reported", language)}`;
  }
  if (stage === "canonical_route_selected") {
    const strategy = String(event.strategy || "").trim().toLowerCase() === "agent_toolbox"
      ? (language === "zh-CN" ? "智能体工具箱" : "agent toolbox")
      : humanizeCode(event.strategy || "agent toolbox");
    return `${strategy} · ${toolLabel(event.tool, language) || (language === "zh-CN" ? "科学工具" : "scientific tool")}`;
  }
  if (stage === "fallback_tool_called") {
    return `${toolLabel(event.tool || "align_legacy_reference", language)} · ${codeLabel(event.reason, language)}`;
  }
  if (stage === "root_recovery_tool_called") {
    return `${toolLabel(event.tool || "align_numbering_cm", language)} · ${codeLabel(event.scope_reason, language)}`;
  }
  if (stage === "tool_started") {
    const tool = toolLabel(event.tool || event.tool_name, language);
    const model = groupLabel(event.model_group || event.selected_group, language);
    return model ? `${tool} · ${model}` : tool;
  }
  if (stage === "tool_observation") {
    const parts: string[] = [];
    if (event.selected_group) parts.push(groupLabel(event.selected_group, language));
    if (event.bit_score !== undefined && event.bit_score !== null) {
      parts.push(`${factLabel("bit_score", language)} ${Number(event.bit_score).toFixed(2)}`);
    }
    if (event.average_posterior !== undefined && event.average_posterior !== null) {
      parts.push(`${factLabel("average_posterior", language)} ${Number(event.average_posterior).toFixed(3)}`);
    }
    if (event.disposition) parts.push(formatFactValue("disposition", event.disposition, language));
    return parts.join(" · ") || toolLabel(event.tool, language) || (language === "zh-CN" ? "已记录观察" : "Observation recorded");
  }
  if (stage === "llm_delta") {
    const content = String(event.content || "").trim();
    return content || (language === "zh-CN" ? "已收到模型输出" : "LLM output received");
  }
  if (stage === "special_tool_observation") {
    const parts = [toolLabel(event.tool || "special model", language)];
    const zh = language === "zh-CN";
    if (event.hit_count !== undefined) parts.push(zh ? `${Number(event.hit_count)} 处命中` : `${Number(event.hit_count)} hit(s)`);
    if (event.detected !== undefined) parts.push(event.detected ? (zh ? "已检出" : "detected") : (zh ? "未检出" : "not detected"));
    const removed = asRecord(event.removed_interval);
    if (removed.start !== undefined && removed.end !== undefined) {
      parts.push(zh ? `已移除 ${removed.start}-${removed.end}` : `removed ${removed.start}-${removed.end}`);
    }
    if (event.automatic_detection === true) parts.push(zh ? "自动检测" : "automatic inspection");
    if (event.disposition) parts.push(formatFactValue("disposition", event.disposition, language));
    return parts.join(" · ");
  }
  if (stage === "canonical_like_structure_assessed") {
    const counts = asRecord(event.pair_counts);
    const zh = language === "zh-CN";
    const pairs = [
      `${zh ? "氨酰臂" : "acceptor"} ${Number(counts.acceptor_stem || 0)}`,
      `${zh ? "D 臂" : "D"} ${Number(counts.d_stem || 0)}`,
      `${zh ? "反密码子臂" : "anticodon"} ${Number(counts.anticodon_stem || 0)}`,
      `${zh ? "T 臂" : "T"} ${Number(counts.t_stem || 0)}`,
    ];
    const score = Number(event.cm_bit_score);
    if (Number.isFinite(score)) pairs.push(`${factLabel("bit_score", language)} ${score.toFixed(2)}`);
    pairs.push(event.qualified
      ? (zh ? "符合典型结构（含范围警告）" : "qualified with scope warning")
      : (zh ? "需复核" : "needs review"));
    return pairs.join(" · ");
  }
  if (stage === "guard_evaluated") {
    const reasons = Array.isArray(event.reasons) ? event.reasons : [];
    const passed: Record<ProcessLanguage, string> = {
      en: "Passed", "zh-CN": "通过", de: "Bestanden", fr: "Validé",
    };
    const failed: Record<ProcessLanguage, string> = {
      en: "Did not pass", "zh-CN": "未通过", de: "Nicht bestanden", fr: "Non validé",
    };
    return event.valid ? passed[language] : reasons.map((item) => codeLabel(item, language)).join(" · ") || failed[language];
  }
  if (stage === "action_executed") {
    const action = toolLabel(event.action_type, language) || (language === "zh-CN" ? "工具动作" : "action");
    const contract = asRecord(event.scientific_contract);
    const disposition = String(event.disposition || "evaluated");
    const resolver = String(contract.resolver || "");
    if (resolver) {
      const count = Number(contract.candidate_count);
      const candidateLabel = Number.isFinite(count) && count > 0
        ? (language === "zh-CN" ? `${count} 个候选` : `${count} candidates`)
        : (language === "zh-CN" ? "边界无改动" : "no boundary change");
      return [
        action,
        language === "zh-CN" ? "确定性边界" : "deterministic boundary",
        candidateLabel,
        language === "zh-CN" ? "未调用大模型" : "no LLM",
        formatFactValue("disposition", disposition, language),
      ].join(" · ");
    }
    if (String(contract.role || "") === "bounded_regional_candidate_generator") {
      const attempted = Array.isArray(contract.llm_attempted_regions)
        ? contract.llm_attempted_regions.map((item) => formatFactValue("target_region", item, language))
        : [];
      const llmLabel = attempted.length
        ? (language === "zh-CN" ? `模型建议 ${attempted.join("、")}` : `LLM proposed ${attempted.join(", ")}`)
        : (language === "zh-CN" ? "未调用大模型" : "LLM not called");
      return [
        action,
        language === "zh-CN" ? "局部区域建议" : "regional proposal",
        llmLabel,
        formatFactValue("disposition", disposition, language),
      ].join(" · ");
    }
    return `${action} · ${formatFactValue("disposition", disposition, language)}`;
  }
  if (stage === "planning") {
    const decision = asRecord(event.agent_decision);
    const action = toolLabel(decision.action_id, language)
      || (decision.decision
        ? formatFactValue("decision", decision.decision, language)
        : (language === "zh-CN" ? "已复核证据" : "reviewed evidence"));
    const model = String(decision.model || decision.planner_source || "");
    const hitCount = Number(event.memory_hit_count || 0);
    const memory = hitCount > 0
      ? (language === "zh-CN" ? `${hitCount} 条已验证记忆模式` : `${hitCount} verified memory pattern(s)`)
      : "";
    return [action, model, memory].filter(Boolean).join(" · ");
  }
  if (event.reason) return codeLabel(event.reason, language);
  if (event.route) return routeLabel(event.route, language);
  if (event.status) return formatFactValue("status", event.status, language);
  if (event.disposition) return formatFactValue("disposition", event.disposition, language);
  return recorded[language];
}

export function inputMetadataFields(
  event: ProcessEventLike,
  language: ProcessLanguage,
): Array<{ label: string; value: string }> {
  const missing: Record<ProcessLanguage, string> = {
    en: "Not provided", "zh-CN": "未提供", de: "Nicht angegeben", fr: "Non fourni",
  };
  const valueOrMissing = (value: unknown) => {
    const normalized = String(value || "").trim();
    return !normalized || normalized === "unknown" ? missing[language] : humanizeCode(normalized);
  };
  const contract = asRecord(event.input_contract);
  const sequenceObservations = asRecord(contract.sequence_observations);
  const ccaEvidence = asRecord(contract.cca_evidence);
  const intronEvidence = asRecord(contract.intron_evidence);
  const terminalCca = sequenceObservations.terminal_cca_observed === true;
  const ccaConfirmed = ccaEvidence.coordinate_assignment_allowed === true;
  const ccaValue = ccaState(terminalCca, ccaConfirmed, language);
  const labelSets: Record<ProcessLanguage, string[]> = {
    en: ["Input type", "Source domain", "tRNA class", "CCA tail", "Intron"],
    "zh-CN": ["输入类型", "来源域", "tRNA 类别", "CCA 末端", "内含子"],
    de: ["Eingabetyp", "Herkunftsdomäne", "tRNA-Klasse", "CCA-Ende", "Intron"],
    fr: ["Type d'entrée", "Domaine d'origine", "Classe d'ARNt", "Extrémité CCA", "Intron"],
  };
  const [inputLabel, sourceLabel, classLabel, ccaLabel, intronLabel] = labelSets[language];
  return [
    { label: inputLabel, value: valueOrMissing(event.input_type) },
    { label: sourceLabel, value: valueOrMissing(event.domain_hint) },
    { label: classLabel, value: valueOrMissing(event.trna_class) },
    { label: ccaLabel, value: ccaValue },
    { label: intronLabel, value: valueOrMissing(intronEvidence.status_claim || event.intron_status) },
  ];
}

export interface PanelStrings {
  agentProcess: string;
  guardReasons: string;
  warnings: string;
  noStepDetail: string;
  interpretationIdle: string;
  interpretationLoading: string;
  interpretationDone: string;
  interpretationFailed: string;
  referencesHeading: string;
  referencesNote: string;
  referenceSourceLocal: string;
  referenceSourcePublic: string;
  loadingTrace: string;
  noTrace: string;
  summaryFallback: string;
  actions: string;
  action: string;
  accepted: string;
  validBefore: string;
  validAfter: string;
  targetRegions: string;
  result: string;
  planning: string;
  cmCalls: string;
  llmCalls: string;
  promptTokens: string;
  completionTokens: string;
  totalTokens: string;
  elapsed: string;
  stopCondition: string;
  yes: string;
  no: string;
  technicalProvenance: string;
  domainProbabilities: string;
  calibrationDecision: string;
  numberingModel: string;
  modelHash: string;
  cmObservation: string;
  numberingBackend: string;
  template: string;
  model: string;
  provider: string;
  plannerPolicy: string;
  plannerSources: string;
  guardAudit: string;
  guardChallenges: string;
  guardRejected: string;
  guardEscaped: string;
  guardBaseline: string;
  stepOf: (position: number, total: number) => string;
}

const PANEL_EN: PanelStrings = {
  agentProcess: "Agent process",
  guardReasons: "Hard-rule failures",
  warnings: "Warnings",
  noStepDetail: "This step recorded no additional fields.",
  interpretationIdle: "Not written yet · click this step",
  interpretationLoading: "Writing from the recorded evidence...",
  interpretationDone: "Written from the recorded evidence",
  interpretationFailed: "Writing failed · click this step again",
  referencesHeading: "References",
  referencesNote: "Selected from what this run recorded; not used to write the text above.",
  referenceSourceLocal: "project library",
  referenceSourcePublic: "public index",
  loadingTrace: "Loading the recorded process...",
  noTrace: "No process record was stored for this case.",
  summaryFallback: "Steps, decisions and cost",
  actions: "Agent actions",
  action: "Action",
  accepted: "Accepted",
  validBefore: "Valid before",
  validAfter: "Valid after",
  targetRegions: "Target regions",
  result: "Result",
  planning: "planning",
  cmCalls: "CM",
  llmCalls: "LLM calls",
  promptTokens: "prompt tokens",
  completionTokens: "completion tokens",
  totalTokens: "total tokens",
  elapsed: "elapsed",
  stopCondition: "stop condition",
  yes: "Yes",
  no: "No",
  technicalProvenance: "Technical provenance",
  domainProbabilities: "Calibrated domain probabilities",
  calibrationDecision: "Calibration decision",
  numberingModel: "Numbering model",
  modelHash: "Model SHA-256",
  cmObservation: "CM observation",
  numberingBackend: "Numbering backend",
  template: "Template",
  model: "Model",
  provider: "Provider",
  plannerPolicy: "Planner policy",
  plannerSources: "Planner sources",
  guardAudit: "Hard guards",
  guardChallenges: "challenges",
  guardRejected: "rejected",
  guardEscaped: "escaped",
  guardBaseline: "baseline valid",
  stepOf: (position, total) => `Step ${position} of ${total}`,
};

const PANEL_ZH: PanelStrings = {
  ...PANEL_EN,
  agentProcess: "智能体过程",
  guardReasons: "硬规则未通过原因",
  warnings: "警告",
  noStepDetail: "该步骤没有记录额外字段。",
  interpretationIdle: "尚未生成 · 点这一步",
  interpretationLoading: "正依据记录生成...",
  interpretationDone: "依据记录生成",
  interpretationFailed: "生成失败 · 再点这一步",
  referencesHeading: "参考文献",
  referencesNote: "按本次运行记录到的观测检索，未用于生成上面的文字。",
  referenceSourceLocal: "项目文献库",
  referenceSourcePublic: "公开索引",
  loadingTrace: "正在载入运行过程...",
  noTrace: "这条样本没有留存过程记录。",
  summaryFallback: "步骤、判定与开销",
  actions: "智能体动作",
  action: "动作",
  accepted: "采纳",
  validBefore: "动作前合法",
  validAfter: "动作后合法",
  targetRegions: "目标区域",
  result: "结果说明",
  planning: "规划",
  cmCalls: "CM",
  llmCalls: "大模型调用",
  promptTokens: "输入 tokens",
  completionTokens: "输出 tokens",
  totalTokens: "合计 tokens",
  elapsed: "耗时",
  stopCondition: "停止条件",
  yes: "是",
  no: "否",
  technicalProvenance: "技术溯源",
  domainProbabilities: "校准后的生物域概率",
  calibrationDecision: "校准结论",
  numberingModel: "编号模型",
  modelHash: "模型 SHA-256",
  cmObservation: "CM 观察",
  numberingBackend: "编号后端",
  template: "模板",
  model: "模型",
  provider: "提供方",
  plannerPolicy: "规划策略",
  plannerSources: "规划来源",
  guardAudit: "硬规则守卫",
  guardChallenges: "挑战",
  guardRejected: "拒绝",
  guardEscaped: "放行",
  guardBaseline: "基线合法",
  stepOf: (position, total) => `第 ${position} / ${total} 步`,
};

const PANEL_DE: PanelStrings = {
  ...PANEL_EN,
  agentProcess: "Agentenprozess",
  guardReasons: "Verstöße gegen harte Regeln",
  warnings: "Warnungen",
  noStepDetail: "Dieser Schritt hat keine weiteren Felder aufgezeichnet.",
  interpretationIdle: "Noch nicht verfasst · Schritt anklicken",
  interpretationLoading: "Wird aus den aufgezeichneten Belegen verfasst...",
  interpretationDone: "Aus den aufgezeichneten Belegen verfasst",
  interpretationFailed: "Verfassung fehlgeschlagen · Schritt erneut anklicken",
  referencesHeading: "Literatur",
  referencesNote: "Aus den aufgezeichneten Beobachtungen gesucht; nicht für den Text oben verwendet.",
  referenceSourceLocal: "Projektbibliothek",
  referenceSourcePublic: "öffentlicher Index",
  loadingTrace: "Aufgezeichneter Ablauf wird geladen...",
  noTrace: "Für diesen Fall wurde kein Ablauf gespeichert.",
  summaryFallback: "Schritte, Entscheidungen und Aufwand",
  actions: "Agentenaktionen",
  action: "Aktion",
  accepted: "Angenommen",
  validBefore: "Vorher gültig",
  validAfter: "Nachher gültig",
  targetRegions: "Zielbereiche",
  result: "Ergebnis",
  planning: "Planung",
  llmCalls: "LLM-Aufrufe",
  promptTokens: "Prompt-Tokens",
  completionTokens: "Antwort-Tokens",
  totalTokens: "Tokens gesamt",
  elapsed: "Dauer",
  stopCondition: "Stoppbedingung",
  yes: "Ja",
  no: "Nein",
  technicalProvenance: "Technische Herkunft",
  domainProbabilities: "Kalibrierte Domänenwahrscheinlichkeiten",
  calibrationDecision: "Kalibrierungsentscheidung",
  numberingModel: "Numerierungsmodell",
  modelHash: "Modell-SHA-256",
  cmObservation: "CM-Beobachtung",
  numberingBackend: "Numerierungs-Backend",
  template: "Vorlage",
  model: "Modell",
  provider: "Anbieter",
  plannerPolicy: "Planungsrichtlinie",
  plannerSources: "Planungsquellen",
  guardAudit: "Harte Regeln",
  guardChallenges: "Anfragen",
  guardRejected: "abgelehnt",
  guardEscaped: "durchgelassen",
  guardBaseline: "Baseline gültig",
  stepOf: (position, total) => `Schritt ${position} von ${total}`,
};

const PANEL_FR: PanelStrings = {
  ...PANEL_EN,
  agentProcess: "Processus de l'agent",
  guardReasons: "Échecs des règles strictes",
  warnings: "Avertissements",
  noStepDetail: "Cette étape n'a enregistré aucun champ supplémentaire.",
  interpretationIdle: "Pas encore rédigée · cliquer sur cette étape",
  interpretationLoading: "Rédaction à partir des preuves enregistrées...",
  interpretationDone: "Rédigée à partir des preuves enregistrées",
  interpretationFailed: "Échec de la rédaction · recliquer sur cette étape",
  referencesHeading: "Références",
  referencesNote: "Choisies d’après les observations enregistrées ; non utilisées pour rédiger le texte ci-dessus.",
  referenceSourceLocal: "bibliothèque du projet",
  referenceSourcePublic: "index public",
  loadingTrace: "Chargement du déroulement enregistré...",
  noTrace: "Aucun déroulement n'a été enregistré pour ce cas.",
  summaryFallback: "Étapes, décisions et coût",
  actions: "Actions de l'agent",
  action: "Action",
  accepted: "Acceptée",
  validBefore: "Valide avant",
  validAfter: "Valide après",
  targetRegions: "Régions cibles",
  result: "Résultat",
  planning: "Planification",
  llmCalls: "Appels au LLM",
  promptTokens: "jetons de requête",
  completionTokens: "jetons de réponse",
  totalTokens: "jetons totaux",
  elapsed: "durée",
  stopCondition: "condition d'arrêt",
  yes: "Oui",
  no: "Non",
  technicalProvenance: "Provenance technique",
  domainProbabilities: "Probabilités de domaine calibrées",
  calibrationDecision: "Décision de calibration",
  numberingModel: "Modèle de numérotation",
  modelHash: "SHA-256 du modèle",
  cmObservation: "Observation MC",
  numberingBackend: "Backend de numérotation",
  template: "Modèle",
  model: "Modèle",
  provider: "Fournisseur",
  plannerPolicy: "Politique de planification",
  plannerSources: "Sources de planification",
  guardAudit: "Règles strictes",
  guardChallenges: "sollicitations",
  guardRejected: "rejetées",
  guardEscaped: "laissées passer",
  guardBaseline: "base valide",
  stepOf: (position, total) => `Étape ${position} sur ${total}`,
};

export const PANEL_COPY: Record<ProcessLanguage, PanelStrings> = {
  en: PANEL_EN,
  "zh-CN": PANEL_ZH,
  de: PANEL_DE,
  fr: PANEL_FR,
};

const PROCESS_STATE_COPY: Record<ProcessLanguage, Record<string, string>> = {
  en: { info: "recorded", passed: "passed", warning: "warning", failed: "failed" },
  "zh-CN": { info: "记录", passed: "通过", warning: "警示", failed: "失败" },
  de: { info: "protokolliert", passed: "bestanden", warning: "Warnung", failed: "fehlgeschlagen" },
  fr: { info: "enregistré", passed: "validé", warning: "avertissement", failed: "échec" },
};

const DISPOSITION_COPY: Record<ProcessLanguage, Record<string, string>> = {
  en: { accepted: "Accepted", investigate: "Review", abstained: "Abstained", rejected: "Rejected", reported: "Reported" },
  "zh-CN": { accepted: "已接受", investigate: "需复核", abstained: "已转人工", rejected: "未通过", reported: "已记录" },
  de: { accepted: "Akzeptiert", investigate: "Prüfen", abstained: "Zurückgestellt", rejected: "Abgelehnt", reported: "Erfasst" },
  fr: { accepted: "Accepté", investigate: "À réviser", abstained: "Abstention", rejected: "Rejeté", reported: "Enregistré" },
};

export function stateCopy(state: unknown, language: ProcessLanguage): string {
  const key = String(state || "").trim().toLowerCase();
  return PROCESS_STATE_COPY[language][key] || humanizeCode(key);
}

export function dispositionCopy(disposition: unknown, language: ProcessLanguage): string {
  const key = String(disposition || "").trim().toLowerCase();
  return DISPOSITION_COPY[language][key] || humanizeCode(key);
}
