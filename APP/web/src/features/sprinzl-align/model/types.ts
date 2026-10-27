export interface SeqEntry {
  name: string;
  seq: string;
  anticode: string;
}

export interface AlignmentResult {
  run_process?: SequenceRunProcess;
  seq_name?: string;
  seq_index?: number;
  pending?: boolean;
  template_name?: string;
  nums?: string[];
  seq?: string[];
  refNums?: string[];
  ref?: string[];
  warnings?: string[];
  structure?: StructureOverlay;
  llm_trace?: Record<string, unknown>;
  agent?: AgentProcess;
  scientific_provenance?: ScientificProvenance;
  error?: string;
}

export interface RunProcessEvent {
  stage: string;
  at: string;
  source: "agent" | "tool" | "rule" | "system";
  content?: string;
  [key: string]: unknown;
}

export interface SequenceRunProcess {
  state: "queued" | "running" | "completed" | "error";
  stage: string;
  started_at?: string;
  finished_at?: string;
  events: RunProcessEvent[];
}

export interface AgentTraceEvent {
  stage?: string;
  reason?: string;
  route?: string;
  status?: string;
  disposition?: string;
  tool?: string;
  tool_name?: string;
  phase?: string;
  decision?: string;
  action_id?: string;
  planner_source?: string;
  llm_called?: boolean;
  provider?: string;
  model?: string;
  allowed_tools?: string[];
  selected_group?: string;
  selection_policy?: string;
  valid?: boolean;
  error?: string;
  error_type?: string;
  bit_score?: number | null;
  average_posterior?: number | null;
  calibrated_probabilities?: Record<string, number>;
  reasons?: string[];
  [key: string]: unknown;
}

export interface AgentProcess {
  route?: string;
  route_confidence?: number | null;
  hard_guard_passed?: boolean | null;
  best_source_action?: string;
  trace_events?: AgentTraceEvent[];
  work_state_summary?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ScientificProvenance {
  numbering_backend?: string | null;
  numbering_model?: Record<string, unknown>;
  cm_score?: Record<string, unknown>;
  cm_portfolio?: Record<string, unknown>;
  cm_numbering?: Record<string, unknown>;
  cm_tool?: Record<string, unknown>;
  specialized_route?: Record<string, unknown>;
  input_transform?: Record<string, unknown>;
  root_recovery?: Record<string, unknown>;
  legacy_root_failure?: Record<string, unknown>;
  agent_strategy?: Record<string, unknown>;
  legacy_reference_tool?: Record<string, unknown>;
  boundary_refinement_contract?: Record<string, unknown>;
  regional_candidate_contract?: Record<string, unknown>;
  structural_region_evidence?: Record<string, unknown>;
  variable_bridge_evidence?: Record<string, unknown>;
  variable_boundary_reconciliation?: Record<string, unknown>;
  acceptor_tail_reconciliation?: Record<string, unknown>;
  trnascan?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface StructurePairLink {
  left_id: string;
  right_id: string;
  left_index?: number;
  right_index?: number;
}

export interface StructureOverlay {
  annotation_source?: string;
  secondary_structure?: string;
  residue_count?: number;
  pair_count?: number;
  id_pairs?: StructurePairLink[];
}

export interface SessionListItem {
  request_id: string;
  expires_at?: string;
}
