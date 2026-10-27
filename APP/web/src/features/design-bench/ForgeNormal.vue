<template>
  <div class="bench-page">
    <div class="bench-shell" :style="{ '--progress': running ? progress : 100 }">
      <header class="bench-header">
        <h1 class="title">Reference-Guided tRNA Comparison</h1>
        <p class="subtitle">
          Compare engineered tRNA sequences against a shared reference in Sprinzl coordinates.
        </p>
      </header>

      <div v-if="toastMessage" class="request-toast">
        <span>{{ toastMessage }}</span>
        <button
          v-if="requestId"
          type="button"
          class="btn-secondary"
          @click="copyRequestId"
        >
          Copy
        </button>
      </div>

      <div class="bench-grid">
      <section class="bench-panel sequence-panel">
        <div class="section-title">Sequences to align</div>
        <SeqInputList
          v-model="seqList"
          :default-seq="DEFAULT_SEQ"
          :default-fasta="DEFAULT_FASTA"
          default-mode="fasta"
        />
      </section>

      <aside class="bench-side">
      <section class="bench-panel reference-card">
        <div class="section-header">
          <div class="section-title">Reference</div>
          <div class="reference-summary">{{ referenceSummary }}</div>
        </div>

        <div class="field-row">
          <span class="field-label">Mode</span>
          <div class="mode-toggle" role="group" aria-label="Reference mode">
            <button
              type="button"
              class="mode-btn"
              :class="{ active: mode === 'template' }"
              @click="mode = 'template'"
            >
              Template Reference
            </button>
            <button
              type="button"
              class="mode-btn"
              :class="{ active: mode === 'custom' }"
              @click="mode = 'custom'"
            >
              Custom Reference
            </button>
          </div>
        </div>

        <div v-if="mode === 'template'">
          <div class="field-row">
            <span class="field-label">Template source</span>
            <div class="mode-toggle compact">
              <button
                type="button"
                class="mode-btn"
                :class="{ active: templateMode === 'auto' }"
                @click="templateMode = 'auto'"
              >
                Auto
              </button>
              <button
                type="button"
                class="mode-btn"
                :class="{ active: templateMode === 'manual' }"
                @click="templateMode = 'manual'"
              >
                Manual
              </button>
            </div>
          </div>

          <div v-if="templateMode === 'auto'" class="field-note">
            Auto template selection will be applied using the first input sequence.
            <span v-if="resolvedTemplate" class="auto-picked">
              Auto-selected: {{ resolvedTemplate }}
            </span>
          </div>

          <div v-else class="template-select-panel">
            <div class="row-group">
              <div class="select-group">
                <label class="label">Domain</label>
                <div class="select-wrapper">
                  <select
                    v-model="selectedDomain"
                    @change="onDomainChange"
                    class="styled-select"
                  >
                    <option value="2157">Archaea</option>
                    <option value="2">Bacteria</option>
                    <option value="2759">Eukarya</option>
                  </select>
                  <div class="select-arrow">▼</div>
                </div>
              </div>
              <div class="select-group">
                <label class="label">Species</label>
                <div class="select-with-button">
                  <div class="select-wrapper">
                    <select
                      v-model="selectedSpecies"
                      class="styled-select"
                      :disabled="filteredSpecies.length === 0"
                      @change="onSpeciesChange"
                    >
                      <option value="" disabled>Select a species...</option>
                      <option
                        v-for="species in filteredSpecies"
                        :key="species.dbname"
                        :value="species.dbname"
                      >
                        {{ species.name }}
                      </option>
                    </select>
                    <div class="select-arrow">▼</div>
                  </div>
                  <button
                    class="random-button"
                    @click="selectRandomSpecies"
                    :disabled="filteredSpecies.length === 0"
                    title="Select random species"
                  >
                    🎲 Random
                  </button>
                </div>
              </div>
            </div>

            <div class="field-note" v-if="templateLoading">Loading templates…</div>
            <div class="field-error" v-if="templateError">{{ templateError }}</div>

            <div class="row-group">
              <div class="select-group">
                <label class="label">
                  Isotype
                  <small class="muted">(from original sequences)</small>
                </label>
                <div class="select-wrapper">
                  <select
                    v-model="selectedIsotype"
                    class="styled-select"
                    :disabled="!isotypeOptions.length"
                    @change="onIsotypeChange"
                  >
                    <option value="" disabled>Select an isotype...</option>
                    <option
                      v-for="isotype in isotypeOptions"
                      :key="isotype"
                      :value="isotype"
                    >
                      {{ isotype }}
                    </option>
                  </select>
                  <div class="select-arrow">▼</div>
                </div>
              </div>
              <div class="select-group">
                <label class="label">
                  Anticodon
                  <small class="muted">(from original sequences)</small>
                </label>
                <div class="select-wrapper">
                  <select
                    v-model="selectedAnticodon"
                    class="styled-select"
                    :disabled="!selectedIsotype || !anticodonOptions.length"
                    @change="onAnticodonChange"
                  >
                    <option value="" disabled>Select an anticodon...</option>
                    <option
                      v-for="anticodon in anticodonOptions"
                      :key="anticodon"
                      :value="anticodon"
                    >
                      {{ anticodon }}
                    </option>
                  </select>
                  <div class="select-arrow">▼</div>
                </div>
              </div>
            </div>

            <div v-if="candidateTemplates.length" class="candidate-table-container">
              <label class="label">Candidate Template</label>
              <div class="table-wrapper">
                <table class="candidate-table">
                  <thead>
                    <tr>
                      <th class="table-checkbox">Select</th>
                      <th class="table-seqname">Template Name</th>
                      <th class="table-length">Length</th>
                      <th class="table-gc">GC Content</th>
                      <th class="table-sequence">Sequence</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(candidate, index) in candidateTemplates"
                      :key="candidate.seqname"
                      :class="{ selected: selectedTemplateIndex === index }"
                      @click="
                        selectTemplateCandidate(
                          index,
                          candidate.sequence,
                          candidate.seqname
                        )
                      "
                    >
                      <td class="table-checkbox">
                        <div class="checkbox-wrapper">
                          <input
                            type="radio"
                            :name="`candidate-template-${selectedAnticodon}`"
                            :value="index"
                            v-model="selectedTemplateIndex"
                            @change="
                              selectTemplateCandidate(
                                index,
                                candidate.sequence,
                                candidate.seqname
                              )
                            "
                            class="styled-radio"
                          />
                          <span class="checkmark"></span>
                        </div>
                      </td>
                      <td class="table-seqname">
                        {{ candidate.seqname || `Template ${index + 1}` }}
                      </td>
                      <td class="table-length">{{ candidate.length }}</td>
                      <td class="table-gc">{{ candidate.gcContent }}%</td>
                      <td class="table-sequence">
                        <span class="sequence-full" :title="candidate.sequence">{{
                          candidate.sequence
                        }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="template-preview">
            <div class="ref-meta">
              {{ referencePreviewLabel }} · {{ referencePreview.length || 0 }} nt
            </div>
            <details v-if="referencePreview" class="ref-details">
              <summary>Show template sequence</summary>
              <div class="ref-seq">{{ referencePreview }}</div>
            </details>
          </div>
        </div>

        <div v-else class="ref-grid">
          <textarea
            v-model="referenceSeq"
            class="ref-input"
            placeholder="Paste reference in FASTA format (single sequence)"
            @input="onReferenceInput"
          />
          <div v-if="referenceWarning" class="field-error">
            {{ referenceWarning }}
          </div>
        </div>
      </section>

      <section class="bench-panel run-panel">
        <div class="section-title">Run setup</div>
        <div class="action-bar">
          <div class="action-left">
            <span class="switch-label">Alignment Mode</span>
            <div class="mode-segment" role="radiogroup" aria-label="Alignment Mode">
              <button
                type="button"
                class="mode-chip"
                :class="{ active: llmMode === 'dp' }"
                :disabled="running"
                @click="llmMode = 'dp'"
              >
                DP only
              </button>
              <button
                type="button"
                class="mode-chip"
                :class="{ active: llmMode === 'llm_fast' }"
                :disabled="running"
                @click="llmMode = 'llm_fast'"
              >
                Agent
              </button>
            </div>
          </div>
          <div class="action-right">
            <div class="run-meta">
              <span class="field-label">Email (optional)</span>
              <input
                v-model="requestEmail"
                class="field-input"
                placeholder="you@example.com"
              />
            </div>
            <div class="run-cta">
              <span class="save-note">Results are saved for 7 days.</span>
              <button class="btn-run" :disabled="running" @click="runAlign">
                {{ running ? "Aligning…" : "Run Alignment" }}
              </button>
              <label class="cache-toggle">
                <input v-model="bypassCache" type="checkbox" :disabled="running" />
                <span>Run fresh (skip cache and replace it)</span>
              </label>
            </div>
          </div>
        </div>

        <div v-if="requestId" class="request-card">
          <div class="request-dot"></div>
          <div class="request-content">
            <div class="request-label">Request ID</div>
            <div class="request-id">{{ requestId }}</div>
            <div v-if="requestExpiresAt" class="request-expiry">
              Expires {{ formatTime(requestExpiresAt) }}
            </div>
          </div>
          <div class="request-actions">
            <button type="button" class="btn-secondary" @click="copyRequestId">
              Copy
            </button>
            <button type="button" class="btn-secondary" @click="useRequestId">
              Use to Query
            </button>
          </div>
        </div>
      </section>
      </aside>
      </div>

      <div class="bench-bottom-grid">
        <section class="bench-panel results-panel">
          <div class="results-head">
            <div class="request-title">Results</div>
            <div v-if="running || results.length" class="summary-bar">
              <span v-if="running" class="progress-chip progress-chip-stage">{{ runStage }}</span>
              <span v-if="running" class="progress-chip progress-chip-live">{{ doneCount }}/{{ totalCount }}</span>
              <span class="summary-pill">{{ summaryText }}</span>
            </div>
          </div>
          <div class="progress-wrapper" v-show="running || progress > 0">
            <div class="progress-bar">
              <div class="progress-inner" :style="{ width: progress + '%' }"></div>
            </div>
            <span class="progress-text">{{ progress }}%</span>
          </div>
          <div v-if="results.length || running" class="results-wrap">
            <ResultsPanel :results="results" :editable="true" />
          </div>
          <div v-else class="empty-results">
            Run alignment to populate the Sprinzl comparison and structure views.
          </div>
        </section>

        <section class="bench-panel request-panel lookup-panel">
          <div class="request-title">Saved runs & lookup</div>
          <div class="request-grid">
            <div class="field-row">
              <span class="field-label">Query by ID</span>
              <div class="request-query">
                <input
                  v-model="queryId"
                  class="field-input"
                  placeholder="Enter request ID"
                />
                <button
                  type="button"
                  class="btn-secondary"
                  @click="loadByRequestId()"
                >
                  Load
                </button>
              </div>
            </div>
            <div class="field-row">
              <span class="field-label">Query by Email</span>
              <div class="request-query">
                <input
                  v-model="queryEmail"
                  class="field-input"
                  placeholder="you@example.com"
                />
                <button type="button" class="btn-secondary" @click="loadByEmail">
                  Find IDs
                </button>
              </div>
            </div>
            <div v-if="emailHistory.length" class="request-history">
              <div class="request-history-title">Recent IDs</div>
              <div class="request-history-list">
                <button
                  v-for="item in emailHistory"
                  :key="item.request_id"
                  type="button"
                  class="request-history-item"
                  @click="loadByRequestId(item.request_id)"
                >
                  <span>{{ item.request_id }}</span>
                  <small v-if="item.expires_at">
                    · Expires {{ formatTime(item.expires_at) }}
                  </small>
                </button>
              </div>
            </div>
            <div v-if="requestMessage" class="field-note">
              {{ requestMessage }}
            </div>
            <div v-if="requestError" class="field-error">
              {{ requestError }}
            </div>
          </div>
        </section>

      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import SeqInputList from "@/features/sprinzl-align/components/SeqInputList.vue";
import ResultsPanel from "@/features/sprinzl-align/components/ResultsPanel.vue";
import { apiFetch } from "@/shared/api";

interface SeqEntry {
  name: string;
  seq: string;
  anticode: string;
}
interface AlignmentResult {
  seq_name?: string;
  seq_index?: number;
  template_name?: string;
  nums?: string[];
  seq?: string[];
  refNums?: string[];
  ref?: string[];
  warnings?: string[];
  structure?: {
    annotation_source?: string;
    secondary_structure?: string;
    residue_count?: number;
    pair_count?: number;
    id_pairs?: Array<{
      left_id: string;
      right_id: string;
      left_index?: number;
      right_index?: number;
    }>;
  };
  llm_trace?: Record<string, any>;
  agent?: Record<string, any>;
  scientific_provenance?: Record<string, any>;
  error?: string;
}

type StructurePair = {
  left_id: string;
  right_id: string;
  left_index?: number;
  right_index?: number;
};

type Mode = "template" | "custom";
type LlmMode = "dp" | "llm_fast" | "llm_thinking";
type TemplateCandidate = {
  seqname: string;
  sequence: string;
  length: number;
  gcContent: string;
};

const DEFAULT_SEQ =
  "GCGGGGAUGCCCGAGCUAGGACGAAGGGGGUAGGCUUAGGCCCUACUGGCGAAGGCCUUCGCGGGUUCAAAUCCCGCUCCCCGCA";
const DEFAULT_FASTA = `>Leu|TAG|Ref
GCGGGGAUGCCCGAGCUAGGACGAAGGGGGUAGGCUUAGGCCCUACUGGCGAAGGCCUUCGCGGGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var1
GCGGGGAUGCACGAGCUAAGACGAAGGGGGUAAGCUUAGGCCCUACUGGCGAAGGCCUUCGCGGGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var2
GCGGGGAUGCCCAAGCUAGGACGAUGGGGGUAGGCUUAGGCCCUACUGGCGAAGGACUUCGCGGGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var3
GCGGGGAUACCCGAGCUAGGACGAAGGGGGUAGGCUUAGGACCUACUGGCGAAGGCCUUCGCGAGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var4
GCGGGGAUGCCCGAGAUAGGACGAAGGGGAUAGGCUUAGGCCCUACUGGCGAAGGCCUUCGCGGGUUCAAUUCCCGCUCCCCGCA
>Leu|TAG|Var5
GCGGGGAUGCCCGAGCUAGGACAAAGGGGGUAGGCUUUGGCCCUACUAGCGAAGGCCUUCGCGGGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var6
GCGGGGUUGCCCGAGCUAGGACGAAGAGGGUAGGCUUAGGCCCUACUGGCGAAGGCCUACGCGGGUUCAAAUCCCGCUCCCCGCA
>Leu|TAG|Var7
GCGGGGAUGACCGAGCUAGGACGAAGGGGGUAGGAUUAGGCCCUACUGGCGAAGGCCUUCGCGGGUACAAAUCCCGCUCCCCGCA
>Leu|TAG|Var8
GCGGGGAUGCCCGUGCUAGGACGAAGGGGGUAGGCUUAGGCCCUAAUGGCGAAGGCCUUCGCGGGUUCAAAUACCGCUCCCCGCA
>Leu|TAG|Var9
GCGGGGAUGCCCGAGCUAGAACGAAGGGGGUAGGCUUAGGCCCUACUGGCGAAAGCCUUCACGGGUUCAAAUCCCGCUCCCCGCA`;
const DEFAULT_REFERENCE_NAME = "Leu|TAG|Ref";
const DEFAULT_REFERENCE_FASTA = `>${DEFAULT_REFERENCE_NAME}
${DEFAULT_SEQ}`;


const mode = ref<Mode>("template");
const templateMode = ref<"auto" | "manual">("auto");
const templateName = ref("");
const templateLoading = ref(false);
const templateError = ref("");

const selectedDomain = ref("2157");
const selectedSpecies = ref("");
const allSpecies = ref<{ dbname: string; name: string; domain: number }[]>([]);
const isotypeOptions = ref<string[]>([]);
const anticodonOptions = ref<string[]>([]);
const selectedIsotype = ref("");
const selectedAnticodon = ref("");
const candidateTemplates = ref<TemplateCandidate[]>([]);
const selectedTemplateIndex = ref(-1);
const templatePreviewSeq = ref<string[]>([]);
const templatePreviewNums = ref<string[]>([]);
const templatePreviewText = computed(() => templatePreviewSeq.value.join(""));
const resolvedTemplate = ref("");

const referenceName = ref(DEFAULT_REFERENCE_NAME);
const referenceSeq = ref(DEFAULT_REFERENCE_FASTA);

const seqList = ref<SeqEntry[]>([]);

const llmMode = ref<LlmMode>("llm_fast");
const use_llm = computed(() => llmMode.value !== "dp");
const llm_reasoning = computed(() => llmMode.value === "llm_thinking");
const running = ref(false);
const results = ref<AlignmentResult[]>([]);
const requestEmail = ref("");
const requestId = ref("");
const requestExpiresAt = ref("");
const requestMessage = ref("");
const requestError = ref("");
const queryId = ref("");
const queryEmail = ref("");
const emailHistory = ref<{ request_id: string; expires_at?: string }[]>([]);
const toastMessage = ref("");
let toastTimer: any = null;

const progress = ref(0);
const bypassCache = ref(false);
let fakeTimer: any = null;
const doneCount = ref(0);
const totalCount = ref(0);
const successCount = ref(0);
const failedCount = ref(0);
const runStage = ref("Idle");
const transportMode = ref<"serial" | "batch" | "sse" | "unknown">("unknown");
const currentSequence = ref("");
const currentIndex = ref<number | null>(null);
const backendStatus = ref("");
const backendPending = ref(0);
const backendWorkers = ref(0);
const elapsedSeconds = ref(0);
const silenceSeconds = ref(0);
let statusTimer: any = null;
let startedAtMs = 0;
let lastEventAtMs = 0;


const referencePreview = computed(() => {
  if (mode.value === "template") return templatePreviewText.value;
  return referenceSeq.value;
});

const referencePreviewLabel = computed(() => {
  if (mode.value === "template") {
    return resolvedTemplate.value || templateName.value || "Template reference";
  }
  return referenceName.value || "Reference";
});

const referenceSummary = computed(() => {
  if (mode.value === "template") {
    const name = resolvedTemplate.value || templateName.value || "Template reference";
    const len = referencePreview.value.length || 0;
    const modeLabel = templateMode.value === "auto" ? "Auto" : "Manual";
    return `${modeLabel} · ${name} · ${len} nt`;
  }
  const parsed = parseFastaInput(referenceSeq.value);
  const name = parsed.name || referenceName.value || "Custom reference";
  const len = parsed.seq.length || 0;
  return `Custom · ${name} · ${len} nt`;
});

const isTemplateLike = computed(() => mode.value === "template");

const summaryText = computed(() => {
  const modeLabel =
    mode.value === "template"
      ? "Template reference"
      : mode.value === "custom"
      ? "Custom reference"
      : "Template reference";
  let refLabel = "";
  if (mode.value === "custom") {
    refLabel = referenceName.value || "Custom reference";
  } else if (resolvedTemplate.value || templateName.value) {
    refLabel = resolvedTemplate.value || templateName.value;
  } else if (mode.value === "template" && templateMode.value === "manual") {
    refLabel = "Manual select";
  } else {
    refLabel = "Auto-select";
  }
  const seqCount = seqList.value.length || 0;
  const llmLabel =
    llmMode.value === "dp"
      ? "Only Use DP"
      : llmMode.value === "llm_fast"
        ? "Agent"
        : "Agent Deep";
  return `${modeLabel} · ${refLabel} · ${seqCount} seq${seqCount === 1 ? "" : "s"} · ${llmLabel}`;
});

watch(mode, (next) => {
  if (next !== "template") {
    templateMode.value = "auto";
  }
});

const referenceWarning = computed(() => {
  if (
    mode.value === "template" &&
    templateMode.value === "manual" &&
    !templateName.value
  ) {
    return "Please select a template for reference.";
  }
  if (mode.value === "custom") {
    const parsed = parseFastaInput(referenceSeq.value);
    if (!parsed.hasHeader) {
      return "Reference must be in FASTA format with a header line.";
    }
    if (!parsed.seq) {
      return "Reference sequence is required.";
    }
  }
  if (isTemplateLike.value && templateMode.value === "auto") {
    const probe = seqList.value[0];
    if (!probe || !probe.seq) {
      return "Provide at least one sequence to auto-select a template.";
    }
  }
  return "";
});




watch(templateName, async (next) => {
  if (!next) {
    templatePreviewSeq.value = [];
    templatePreviewNums.value = [];
    return;
  }
  await loadTemplatePreview(next);
});

function normalizeSeq(raw: string): string {
  return (raw || "")
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/T/g, "U")
    .replace(/[^AUGC]/g, "");
}

function denormalizeSeq(raw: string): string {
  return (raw || "").toUpperCase().replace(/U/g, "T");
}

function calculateGCContent(sequence: string): string {
  if (!sequence) return "0.00";
  const gcCount = (sequence.match(/[GC]/g) || []).length;
  return ((gcCount / sequence.length) * 100).toFixed(2);
}

function formatTime(raw: string) {
  if (!raw) return "";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return raw;
  return dt.toLocaleString();
}

function clearProgressTimer() {
  clearInterval(fakeTimer);
  fakeTimer = null;
}

function clearStatusTimer() {
  clearInterval(statusTimer);
  statusTimer = null;
}

function touchEvent() {
  lastEventAtMs = Date.now();
}

function setCurrent(idx: number | null, name: string) {
  currentIndex.value = idx;
  currentSequence.value = String(name || "").trim();
  touchEvent();
}

function syncProgress() {
  const total = Math.max(1, totalCount.value);
  const realPercent = Math.floor((doneCount.value / total) * 100);
  progress.value = Math.max(progress.value, realPercent);
}

function setStage(stage: string, backendMessage = "", backendMeta?: {
  pending?: number;
  workers?: number;
  currentIndex?: number | null;
  currentSequence?: string;
  transportMode?: "serial" | "batch" | "sse" | "unknown";
}) {
  runStage.value = stage;
  if (backendMessage) backendStatus.value = backendMessage;
  if (backendMeta?.transportMode) transportMode.value = backendMeta.transportMode;
  if (typeof backendMeta?.workers === "number") backendWorkers.value = backendMeta.workers;
  if (typeof backendMeta?.pending === "number") backendPending.value = backendMeta.pending;
  if (backendMeta && "currentIndex" in backendMeta) {
    currentIndex.value = backendMeta.currentIndex ?? null;
  }
  if (backendMeta && "currentSequence" in backendMeta) {
    currentSequence.value = String(backendMeta.currentSequence || "").trim();
  }
  touchEvent();
}

function startStatusTicker() {
  clearStatusTimer();
  statusTimer = setInterval(() => {
    if (!running.value) return;
    const now = Date.now();
    elapsedSeconds.value = Math.max(0, Math.floor((now - startedAtMs) / 1000));
    silenceSeconds.value = Math.max(0, Math.floor((now - lastEventAtMs) / 1000));
  }, 1000);
}



function showToast(message: string) {
  toastMessage.value = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastMessage.value = "";
  }, 3000);
}

function copyRequestId() {
  const rid = requestId.value;
  if (!rid) return;
  navigator.clipboard
    ?.writeText(rid)
    .then(() => {
      showToast("Request ID copied.");
    })
    .catch(() => {
      showToast("Copy failed. Please copy manually.");
    });
}

function useRequestId() {
  if (!requestId.value) return;
  queryId.value = requestId.value;
  showToast("Request ID ready to query.");
}

function buildPayload() {
  const parsedRef = parseFastaInput(referenceSeq.value);
  return {
    mode: mode.value,
    llm_mode: llmMode.value,
    templateMode: mode.value === "template" ? templateMode.value : "auto",
    templateName:
      mode.value === "template"
        ? resolvedTemplate.value || templateName.value || null
        : null,
    reference: {
      source: mode.value === "custom" ? "custom" : "template",
      name: referenceName.value || "",
      seq: mode.value === "custom" ? parsedRef.seq : "",
      anticode: "",
    },
    sequences: seqList.value,
    bypass_cache: bypassCache.value,
  };
}

function selectTemplateCandidate(
  index: number,
  sequence: string,
  seqname: string
) {
  selectedTemplateIndex.value = index;
  templateName.value = seqname;
  const normalized = normalizeSeq(sequence);
  if (normalized) {
    loadTemplatePreview(seqname);
  }
}

function parseFastaInput(raw: string) {
  const text = String(raw || "").trim();
  if (!text) return { name: "", seq: "", hasHeader: false };
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return { name: "", seq: "", hasHeader: false };
  if (lines[0].startsWith(">")) {
    const name = lines[0].slice(1).trim() || "";
    const seqRaw = lines.slice(1).join("");
    return { name, seq: normalizeSeq(seqRaw), hasHeader: true };
  }
  return { name: "", seq: normalizeSeq(text), hasHeader: false };
}

function onReferenceInput() {
  const parsed = parseFastaInput(referenceSeq.value);
  if (parsed.name) {
    referenceName.value = parsed.name;
  }
}

async function readErrorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data?.message) return String(data.message);
    return JSON.stringify(data);
  } catch {
    try {
      const text = await res.text();
      return text || `HTTP ${res.status}`;
    } catch {
      return `HTTP ${res.status}`;
    }
  }
}

function parseCsv(csvText: string) {
  const lines = String(csvText || "")
    .trim()
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2) throw new Error("Alignment response missing CSV content.");
  const header = lines[0];
  const target = lines[lines.length - 1];
  return { nums: header.split(","), seq: target.split(",") };
}

function normalizeStructure(raw: unknown): AlignmentResult["structure"] {
  if (!raw || typeof raw !== "object") return undefined;
  const rec = raw as Record<string, unknown>;
  const linksRaw = Array.isArray(rec.id_pairs) ? rec.id_pairs : [];
  const links: StructurePair[] = linksRaw
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const pair = item as Record<string, unknown>;
      const left = String(pair.left_id || "").trim();
      const right = String(pair.right_id || "").trim();
      if (!left || !right) return null;
      const out: StructurePair = { left_id: left, right_id: right };
      const leftIndex = Number(pair.left_index);
      const rightIndex = Number(pair.right_index);
      if (Number.isFinite(leftIndex)) out.left_index = leftIndex;
      if (Number.isFinite(rightIndex)) out.right_index = rightIndex;
      return out;
    })
    .filter((v): v is StructurePair => !!v);
  return {
    annotation_source: String(rec.annotation_source || "").trim() || undefined,
    secondary_structure: String(rec.secondary_structure || "").trim() || undefined,
    residue_count: Number.isFinite(Number(rec.residue_count)) ? Number(rec.residue_count) : undefined,
    pair_count: Number.isFinite(Number(rec.pair_count)) ? Number(rec.pair_count) : undefined,
    id_pairs: links,
  };
}

const filteredSpecies = computed(() =>
  allSpecies.value.filter((s) => s.domain.toString() === selectedDomain.value)
);

async function fetchAllSpecies() {
  templateLoading.value = true;
  templateError.value = "";
  try {
    const response = await apiFetch(`/mysql/get_species`);
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const data = await response.json();
    allSpecies.value = data.species || [];
    if (!selectedSpecies.value && filteredSpecies.value.length > 0) {
      selectedSpecies.value = filteredSpecies.value[0].dbname;
      await fetchTrnaFilters();
    }
  } catch (e) {
    templateError.value = "Failed to fetch species list.";
    allSpecies.value = [];
    selectedSpecies.value = "";
  } finally {
    templateLoading.value = false;
  }
}

function selectRandomSpecies() {
  if (!filteredSpecies.value.length) return;
  const i = Math.floor(Math.random() * filteredSpecies.value.length);
  selectedSpecies.value = filteredSpecies.value[i].dbname;
  onSpeciesChange();
}

function onDomainChange() {
  selectedSpecies.value = "";
  selectedIsotype.value = "";
  selectedAnticodon.value = "";
  isotypeOptions.value = [];
  anticodonOptions.value = [];
  candidateTemplates.value = [];
  selectedTemplateIndex.value = -1;
  if (filteredSpecies.value.length > 0) {
    selectedSpecies.value = filteredSpecies.value[0].dbname;
    onSpeciesChange();
  }
}

function onSpeciesChange() {
  fetchTrnaFilters();
}

async function fetchTrnaFilters() {
  if (!selectedSpecies.value) {
    isotypeOptions.value = [];
    anticodonOptions.value = [];
    candidateTemplates.value = [];
    selectedIsotype.value = "";
    selectedAnticodon.value = "";
    selectedTemplateIndex.value = -1;
    return;
  }
  templateError.value = "";
  try {
    const response = await apiFetch(
      `/mysql/get_trna_filters?species=${selectedSpecies.value}`
    );
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const data = await response.json();
    isotypeOptions.value = data.isotypes || [];
    selectedIsotype.value = "";
    selectedAnticodon.value = "";
    anticodonOptions.value = [];
    candidateTemplates.value = [];
    selectedTemplateIndex.value = -1;
  } catch (e) {
    templateError.value = "Failed to fetch isotypes.";
    isotypeOptions.value = [];
    anticodonOptions.value = [];
    candidateTemplates.value = [];
  }
}

function onIsotypeChange() {
  fetchAnticodonsByIsotype();
}

async function fetchAnticodonsByIsotype() {
  if (!selectedSpecies.value || !selectedIsotype.value) {
    anticodonOptions.value = [];
    candidateTemplates.value = [];
    selectedAnticodon.value = "";
    selectedTemplateIndex.value = -1;
    return;
  }
  templateError.value = "";
  try {
    const response = await apiFetch("/mysql/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        table: "trna_records",
        filters: {
          dbname: selectedSpecies.value,
          isotype: denormalizeSeq(selectedIsotype.value),
        },
        limit: 1000,
      }),
    });
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const data = await response.json();
    anticodonOptions.value = Array.from(
      new Set(
        (data.rows || [])
          .map((r: any) => r.anticodon)
          .filter(Boolean)
          .map((ac: string) => normalizeSeq(ac))
      )
    );
    selectedAnticodon.value = "";
    candidateTemplates.value = [];
    selectedTemplateIndex.value = -1;
  } catch (e) {
    templateError.value = "Failed to fetch anticodons.";
    anticodonOptions.value = [];
    candidateTemplates.value = [];
  }
}

function onAnticodonChange() {
  fetchCandidateTemplates();
}

async function fetchCandidateTemplates() {
  if (!selectedSpecies.value || !selectedIsotype.value || !selectedAnticodon.value) {
    candidateTemplates.value = [];
    selectedTemplateIndex.value = -1;
    return;
  }
  templateError.value = "";
  try {
    const response = await apiFetch("/mysql/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        table: "trna_records",
        filters: {
          dbname: selectedSpecies.value,
          isotype: denormalizeSeq(selectedIsotype.value),
          anticodon: denormalizeSeq(selectedAnticodon.value),
        },
        limit: 50,
      }),
    });
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    const data = await response.json();
    const mapped = (data.rows || [])
      .filter((item: any) => item.sequence)
      .map((item: any) => {
        const seqU = normalizeSeq(item.sequence);
        return {
          ...item,
          sequence: seqU,
          length: seqU.length,
          gcContent: calculateGCContent(seqU),
        } as TemplateCandidate;
      });
    candidateTemplates.value = mapped;
    selectedTemplateIndex.value = -1;
  } catch (e) {
    templateError.value = "Failed to fetch candidate templates.";
    candidateTemplates.value = [];
  }
}

async function loadTemplatePreview(name: string) {
  try {
    const res = await apiFetch(
      `/align/export/?template_name=${encodeURIComponent(name)}`
    );
    if (!res.ok) throw new Error(await readErrorMessage(res));
    const data = await res.json();
    const parsed = parseCsv(data.csv_content);
    templatePreviewNums.value = parsed.nums;
    templatePreviewSeq.value = parsed.seq;
  } catch (err: any) {
    templatePreviewSeq.value = [];
    templatePreviewNums.value = [];
    templateError.value = err.message || "Failed to load template.";
  }
}

async function fetchTemplateLines(name: string) {
  const res = await apiFetch(
    `/align/export/?template_name=${encodeURIComponent(name)}`
  );
  if (!res.ok) throw new Error(await readErrorMessage(res));
  const data = await res.json();
  return parseCsv(data.csv_content);
}

function pushResult(res: AlignmentResult) {
  results.value.push(res);
  doneCount.value += 1;
  if (res.error) failedCount.value += 1;
  else successCount.value += 1;
  backendPending.value = Math.max(0, totalCount.value - doneCount.value);
  touchEvent();
  syncProgress();
}

async function alignSequence(
  seq: SeqEntry,
  seqIndex: number,
  refNums: string[],
  refSeq: string[],
  templateOverride?: string
) {
  const res = await apiFetch("/align/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_seq: seq.seq,
      anticodon: seq.anticode,
      use_llm: use_llm.value,
      llm_reasoning: llm_reasoning.value,
      template_name: templateOverride || undefined,
      bypass_cache: bypassCache.value,
    }),
  });
  if (!res.ok) throw new Error(await readErrorMessage(res));
  const data = await res.json();
  const parsed = parseCsv(data.csv_content);
  return {
    seq_name: seq.name,
    seq_index: seqIndex,
    template_name: data.reference_template_name || data.template_name,
    nums: parsed.nums,
    seq: parsed.seq,
    refNums,
    ref: refSeq,
    warnings: Array.isArray(data.warnings)
      ? data.warnings.map((w: any) => String(w)).filter(Boolean)
      : [],
    structure: normalizeStructure((data as any).structure),
    llm_trace: (data as any).llm_trace || undefined,
    agent: (data as any).agent || undefined,
    scientific_provenance: (data as any).scientific_provenance || undefined,
  } as AlignmentResult;
}

async function alignReference(
  referenceSeqRaw: string,
  anticode: string,
  templateOverride?: string
) {
  const res = await apiFetch("/align/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_seq: referenceSeqRaw,
      anticodon: anticode,
      use_llm: use_llm.value,
      llm_reasoning: llm_reasoning.value,
      template_name: templateOverride || undefined,
      bypass_cache: bypassCache.value,
    }),
  });
  if (!res.ok) throw new Error(await readErrorMessage(res));
  const data = await res.json();
  const parsed = parseCsv(data.csv_content);
  return {
    templateName: (data.reference_template_name || data.template_name) as string,
    nums: parsed.nums,
    seq: parsed.seq,
    warnings: Array.isArray(data.warnings)
      ? data.warnings.map((w: any) => String(w)).filter(Boolean)
      : [],
  };
}

async function saveSession(payload: Record<string, any>, savedResults: AlignmentResult[]) {
  requestError.value = "";
  requestMessage.value = "";
  try {
    const res = await apiFetch("/align/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payload,
        results: savedResults,
        email: requestEmail.value || undefined,
      }),
    });
    if (!res.ok) throw new Error(await readErrorMessage(res));
    const data = await res.json();
    requestId.value = data.request_id || "";
    requestExpiresAt.value = data.expires_at || "";
    if (requestId.value) {
      queryId.value = requestId.value;
    }
    requestMessage.value = requestId.value
      ? `Request ID generated: ${requestId.value}`
      : "Saved, but no request ID returned.";
    if (requestId.value) {
      showToast(`Saved · Request ID: ${requestId.value}`);
    }
    if (requestId.value && requestEmail.value.trim()) {
      const exists = emailHistory.value.some(
        (item) => item.request_id === requestId.value
      );
      if (!exists) {
        emailHistory.value = [
          { request_id: requestId.value, expires_at: requestExpiresAt.value },
          ...emailHistory.value,
        ];
      }
    }
  } catch (err: any) {
    requestError.value = err.message || "Failed to save request ID.";
  }
}

async function loadByRequestId(explicitId?: string) {
  const isEventLike =
    explicitId &&
    typeof explicitId === "object" &&
    "target" in (explicitId as object);
  const raw =
    typeof explicitId === "string"
      ? explicitId
      : explicitId == null || isEventLike
        ? queryId.value
        : String(explicitId);
  const rid = String(raw || "").trim();
  requestError.value = "";
  requestMessage.value = "";
  if (!rid) {
    requestError.value = "Please enter a request ID.";
    return;
  }
  requestMessage.value = "Loading request ID...";
  try {
    const res = await apiFetch(`/align/session/${encodeURIComponent(rid)}`);
    if (!res.ok) throw new Error(await readErrorMessage(res));
    const data = await res.json();
    requestId.value = data.request_id || rid;
    requestExpiresAt.value = data.expires_at || "";
    results.value = Array.isArray(data.results) ? data.results : [];
    requestMessage.value = results.value.length
      ? "Results loaded."
      : "Request loaded, but no results found.";
    queryId.value = rid;
  } catch (err: any) {
    requestError.value = err.message || "Failed to load request ID.";
  }
}

async function loadByEmail() {
  const email = queryEmail.value.trim();
  requestError.value = "";
  requestMessage.value = "";
  emailHistory.value = [];
  if (!email) {
    requestError.value = "Please enter an email.";
    return;
  }
  requestMessage.value = "Loading email history...";
  try {
    const res = await apiFetch(
      `/align/session?email=${encodeURIComponent(email)}`
    );
    if (!res.ok) throw new Error(await readErrorMessage(res));
    const data = await res.json();
    emailHistory.value = Array.isArray(data.items) ? data.items : [];
    if (!emailHistory.value.length) {
      requestMessage.value = "No request IDs found for this email.";
    }
  } catch (err: any) {
    requestError.value = err.message || "Failed to load history by email.";
  }
}

async function runAlign() {
  results.value = [];
  running.value = true;
  progress.value = 0;
  doneCount.value = 0;
  successCount.value = 0;
  failedCount.value = 0;
  resolvedTemplate.value = "";
  // Clear the previous run's session card so a new batch never shows a stale ID.
  requestId.value = "";
  requestExpiresAt.value = "";
  requestMessage.value = "";
  requestError.value = "";
  transportMode.value = "serial";
  backendStatus.value = "";
  backendWorkers.value = 1;
  currentSequence.value = "";
  currentIndex.value = null;
  startedAtMs = Date.now();
  lastEventAtMs = startedAtMs;
  elapsedSeconds.value = 0;
  silenceSeconds.value = 0;

  const listCount = seqList.value.length;
  if (
    mode.value === "template" &&
    templateMode.value === "manual" &&
    !templateName.value
  ) {
    failedCount.value = 1;
    running.value = false;
    results.value = [{ error: "Template mode requires a template selection." }];
    return;
  }
  if (mode.value === "custom") {
    const parsedRef = parseFastaInput(referenceSeq.value);
    if (!parsedRef.hasHeader) {
      failedCount.value = 1;
      running.value = false;
      results.value = [
        { error: "Reference must be in FASTA format with a header line." },
      ];
      return;
    }
    if (!parsedRef.seq) {
      failedCount.value = 1;
      running.value = false;
      results.value = [{ error: "Reference sequence is required." }];
      return;
    }
  }
  if (
    isTemplateLike.value &&
    templateMode.value === "auto" &&
    !seqList.value[0]?.seq
  ) {
    failedCount.value = 1;
    running.value = false;
    results.value = [{ error: "Provide at least one sequence to auto-select a template." }];
    return;
  }

  totalCount.value = Math.max(
    1,
    mode.value === "custom" ? listCount + 1 : listCount
  );
  backendPending.value = totalCount.value;
  setStage(
    `Preparing ${totalCount.value} sequence(s)`,
    "Validating reference setup and preparing serial alignment requests.",
    {
      pending: totalCount.value,
      workers: 1,
      transportMode: "serial",
      currentIndex: null,
      currentSequence: "",
    },
  );
  startStatusTicker();

  clearProgressTimer();
  fakeTimer = setInterval(() => {
    if (running.value) {
      const nextLimit = Math.min(
        99,
        Math.floor(((doneCount.value + 1) / Math.max(1, totalCount.value)) * 100) - 1
      );
      if (progress.value < nextLimit) progress.value += 1;
    }
  }, 300);

  try {
    let refNums: string[] = [];
    let refSeq: string[] = [];
    let templateFinal = templateName.value || "";

    if (isTemplateLike.value) {
      if (templateMode.value === "auto") {
        const probe = seqList.value[0];
        setCurrent(0, probe?.name || "Template probe");
        setStage(
          "Selecting template",
          "Submitting the first sequence to resolve the best reference template.",
          {
            pending: backendPending.value,
            workers: 1,
            currentIndex: 0,
            currentSequence: probe?.name || "Template probe",
            transportMode: "serial",
          },
        );
        const probeAlign = await alignReference(
          probe.seq,
          probe.anticode || "",
          undefined
        );
        templateFinal = probeAlign.templateName;
        resolvedTemplate.value = templateFinal;
        templateName.value = templateFinal;
        setStage(
          "Loading template baseline",
          `Template ${templateFinal} resolved. Loading baseline numbering and reference rows.`,
          {
            pending: backendPending.value,
            workers: 1,
            currentIndex: 0,
            currentSequence: probe?.name || "Template probe",
            transportMode: "serial",
          },
        );
        await loadTemplatePreview(templateFinal);
      } else if (mode.value === "template") {
        templateFinal = templateName.value;
        resolvedTemplate.value = templateFinal;
        setStage(
          "Loading template baseline",
          `Using manually selected template ${templateFinal}. Loading baseline rows.`,
          {
            pending: backendPending.value,
            workers: 1,
            currentIndex: null,
            currentSequence: templateFinal,
            transportMode: "serial",
          },
        );
        await loadTemplatePreview(templateFinal);
      } else {
        templateFinal = "";
      }
      if (!templatePreviewNums.value.length) {
        throw new Error("Template preview is unavailable.");
      }
      refNums = templatePreviewNums.value;
      refSeq = templatePreviewSeq.value;
      doneCount.value = 0;
      backendPending.value = totalCount.value;
    } else {
      const parsedRef = parseFastaInput(referenceSeq.value);
      const refRaw = parsedRef.seq;
      const refName = referenceName.value || "Reference";
      setCurrent(0, refName);
      setStage(
        "Aligning custom reference",
        "Aligning the custom reference first to establish the comparison baseline.",
        {
          pending: backendPending.value,
          workers: 1,
          currentIndex: 0,
          currentSequence: refName,
          transportMode: "serial",
        },
      );
      const refAlign = await alignReference(refRaw, "", undefined);
      templateFinal = refAlign.templateName;
      const refAlignedNums = refAlign.nums;
      const refAlignedSeq = refAlign.seq;

      let templateNums = refAlignedNums;
      let templateSeq = refAlignedSeq;
      try {
        setStage(
          "Loading template baseline",
          `Reference aligned to template ${templateFinal}. Loading canonical baseline rows.`,
          {
            pending: backendPending.value,
            workers: 1,
            currentIndex: 0,
            currentSequence: refName,
            transportMode: "serial",
          },
        );
        const templateLines = await fetchTemplateLines(templateFinal);
        templateNums = templateLines.nums;
        templateSeq = templateLines.seq;
      } catch (err: any) {
        refAlign.warnings = [
          ...(refAlign.warnings || []),
          "Template baseline unavailable; using reference alignment as baseline.",
        ];
      }

      refNums = refAlignedNums;
      refSeq = refAlignedSeq;

      results.value.push({
        seq_name: refName,
        seq_index: 0,
        template_name: templateFinal,
        nums: refAlignedNums,
        seq: refAlignedSeq,
        refNums: templateNums,
        ref: templateSeq,
        warnings: refAlign.warnings,
      });
      doneCount.value = 1;
      successCount.value = 1;
      backendPending.value = Math.max(0, totalCount.value - doneCount.value);
      syncProgress();
      touchEvent();
    }

    const skipFirst = false;
    for (let i = 0; i < seqList.value.length; i += 1) {
      if (skipFirst && i === 0) continue;
      const entry = seqList.value[i];
      const displayIndex = i + 1;
      const currentUiIndex = mode.value === "custom" ? displayIndex : i;
      setCurrent(currentUiIndex, entry.name || `Seq ${displayIndex}`);
      setStage(
        `Aligning sequence ${displayIndex}/${seqList.value.length}`,
        `Submitting serial alignment request for ${entry.name || `Seq ${displayIndex}`}.`,
        {
          pending: Math.max(0, totalCount.value - doneCount.value),
          workers: 1,
          currentIndex: currentUiIndex,
          currentSequence: entry.name || `Seq ${displayIndex}`,
          transportMode: "serial",
        },
      );
      try {
        const res = await alignSequence(
          entry,
          displayIndex,
          refNums,
          refSeq,
          isTemplateLike.value && mode.value !== "custom" ? templateFinal : undefined
        );
        pushResult(res);
      } catch (err: any) {
        pushResult({
          seq_name: entry.name,
          seq_index: displayIndex,
          error: err.message || `Unknown error (#${i + 1})`,
        });
      }
    }

    if (results.value.length) {
      setStage(
        "Saving session metadata",
        "Persisting the request metadata and generated result rows for later lookup.",
        {
          pending: 0,
          workers: 0,
          currentIndex: null,
          currentSequence: "",
          transportMode: "serial",
        },
      );
      await saveSession(buildPayload(), results.value);
    }
    setStage(
      "Completed",
      "Reference comparison finished. All comparison rows are ready.",
      {
        pending: 0,
        workers: 0,
        currentIndex: null,
        currentSequence: "",
        transportMode: "serial",
      },
    );
  } catch (err: any) {
    results.value = [
      {
        error: err.message || "Reference alignment failed.",
      },
    ];
    failedCount.value = Math.max(1, failedCount.value);
    setStage(
      "Completed with errors",
      err.message || "Reference alignment failed.",
      {
        pending: Math.max(0, totalCount.value - doneCount.value),
        workers: 0,
        currentIndex: currentIndex.value,
        currentSequence: currentSequence.value,
        transportMode: "serial",
      },
    );
    if (results.value.length) {
      await saveSession(buildPayload(), results.value);
    }
  } finally {
    progress.value = 100;
    running.value = false;
    clearProgressTimer();
    clearStatusTimer();
    setTimeout(() => (progress.value = 0), 1000);
  }
}

onMounted(() => {
  fetchAllSpecies();
});

onBeforeUnmount(() => {
  clearProgressTimer();
  clearStatusTimer();
  clearTimeout(toastTimer);
});
</script>

<style scoped>
.home-container {
  display: flex;
  justify-content: center;
  padding: 2rem;
}
.card {
  width: 100%;
  background: var(--surface-2);
  backdrop-filter: blur(6px);
  padding: 2rem;
  border-radius: 12px;
  box-shadow: var(--shadow-soft);
  border: 1px solid var(--border-soft);
  position: relative;
  overflow: hidden;
}
.card::after {
  content: "";
  position: absolute;
  inset: 0;
  background: var(--surface-3);
  backdrop-filter: blur(6px) saturate(120%);
  opacity: calc((100 - var(--progress)) / 100 * 0.7);
  transition: opacity 0.35s ease;
  pointer-events: none;
  z-index: 1;
}
.card > * {
  position: relative;
  z-index: 2;
}

.title {
  font-size: 2rem;
  color: var(--text-strong);
  margin-bottom: 0.5rem;
  text-align: center;
}
.subtitle {
  color: var(--text-muted);
  text-align: center;
  margin-bottom: 1.5rem;
}

.section {
  margin-bottom: 1.5rem;
}
.section-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-strong);
  margin-bottom: 0.6rem;
}

.mode-toggle {
  display: inline-flex;
  gap: 6px;
  padding: 4px;
  border-radius: 999px;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
}
.mode-toggle.compact {
  padding: 2px;
}
.mode-btn {
  border: none;
  padding: 0.35rem 0.9rem;
  border-radius: 999px;
  cursor: pointer;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.mode-btn.active {
  background: rgba(var(--success-rgb), 0.2);
  color: var(--text-strong);
  border: 1px solid rgba(var(--success-rgb), 0.55);
}
.mode-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.field-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
.field-label {
  min-width: 120px;
  color: var(--text-soft);
  font-size: 0.85rem;
}
.label {
  display: block;
  font-size: 0.85rem;
  color: var(--text-soft);
  margin-bottom: 0.35rem;
}
.muted {
  color: var(--text-muted);
  font-size: 0.75rem;
  margin-left: 4px;
}
.field-input,
.field-select {
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.85rem;
  min-width: 240px;
}
.field-input.small {
  min-width: 140px;
}
.field-note {
  color: var(--text-muted);
  font-size: 0.85rem;
}
.field-error {
  color: var(--danger);
  font-size: 0.85rem;
}
.auto-picked {
  display: inline-block;
  margin-left: 8px;
  color: var(--success);
  font-weight: 600;
}
.template-preview {
  margin-top: 0.75rem;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  padding: 0.75rem 1rem;
}
.ref-details summary {
  cursor: pointer;
  color: var(--text-soft);
  font-size: 0.85rem;
  margin-top: 0.4rem;
}
.template-select-panel {
  display: grid;
  gap: 0.75rem;
}
.row-group {
  display: flex;
  gap: 1rem;
  width: 100%;
}
.row-group .select-group {
  flex: 1;
  min-width: 0;
}
.select-group {
  margin-bottom: 1rem;
}
.select-wrapper {
  position: relative;
  width: 100%;
  display: inline-block;
}
.styled-select {
  width: 100%;
  padding: 10px 12px;
  padding-right: 40px;
  border: 1px solid var(--card-border);
  border-radius: 6px;
  background-color: var(--field-bg);
  color: var(--field-text);
  font-size: 14px;
  appearance: none;
  cursor: pointer;
  transition: border-color 0.2s;
}
.styled-select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.25);
}
.styled-select:disabled {
  background-color: rgba(var(--field-bg-rgb), 0.5);
  cursor: not-allowed;
  opacity: 0.6;
}
.select-arrow {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: var(--muted);
  font-size: 12px;
}
.select-with-button {
  display: flex;
  gap: 0.5rem;
  width: 100%;
}
.random-button {
  padding: 10px 16px;
  background-color: var(--chip);
  border: 1px solid var(--chip-border);
  border-radius: 6px;
  color: var(--text-strong);
  cursor: pointer;
  font-size: 14px;
  white-space: nowrap;
  transition: all 0.2s;
}
.random-button:hover:not(:disabled) {
  background-color: rgba(var(--chip-rgb), 0.8);
  border-color: var(--accent);
}
.random-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.checkbox-wrapper {
  position: relative;
  display: inline-block;
  width: 20px;
  height: 20px;
}
.styled-radio {
  position: absolute;
  opacity: 0;
  cursor: pointer;
  width: 100%;
  height: 100%;
  z-index: 2;
}
.checkmark {
  position: absolute;
  top: 0;
  left: 0;
  height: 20px;
  width: 20px;
  background-color: var(--field-bg);
  border: 2px solid var(--card-border);
  border-radius: 50%;
  transition: all 0.2s;
}
.styled-radio:checked ~ .checkmark {
  background-color: var(--accent);
  border-color: var(--accent);
}
.checkmark:after {
  content: "";
  position: absolute;
  display: none;
}
.styled-radio:checked ~ .checkmark:after {
  display: block;
  left: 6px;
  top: 2px;
  width: 5px;
  height: 10px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
.candidate-table-container {
  margin-top: 1rem;
}
.table-wrapper {
  border: 1px solid var(--card-border);
  border-radius: 6px;
  overflow: hidden;
  max-height: 400px;
  overflow-y: auto;
  background-color: var(--field-bg);
}
.candidate-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  background-color: var(--field-bg);
  color: var(--field-text);
}
.candidate-table th {
  background-color: var(--chip);
  padding: 12px 8px;
  text-align: left;
  font-weight: 600;
  border-bottom: 1px solid var(--card-border);
  position: sticky;
  top: 0;
  color: var(--text-soft);
}
.candidate-table td {
  padding: 10px 8px;
  border-bottom: 1px solid var(--chip-border);
}
.candidate-table tbody tr:hover {
  background-color: var(--chip);
  cursor: pointer;
}
.candidate-table tbody tr.selected {
  background-color: rgba(var(--accent-rgb), 0.15);
}
.table-checkbox {
  width: 50px;
  text-align: center;
}
.table-seqname {
  width: 180px;
  font-weight: 500;
}
.table-length {
  width: 90px;
}
.table-gc {
  width: 110px;
}
.table-sequence {
  width: auto;
  min-width: 240px;
}
.sequence-full {
  font-family: monospace;
  font-size: 12px;
  word-break: break-all;
}
.btn-secondary {
  border: 1px solid var(--border-soft);
  background: var(--surface-3);
  color: var(--text-strong);
  border-radius: 6px;
  padding: 6px 12px;
  font-size: 0.82rem;
  cursor: pointer;
}

.ref-grid {
  display: grid;
  gap: 0.75rem;
}
.ref-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.request-grid {
  display: grid;
  gap: 0.75rem;
}
.request-panel {
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  padding: 0.5rem 0.9rem;
  background: var(--surface-3);
  margin-top: 0.75rem;
}
.request-hint {
  margin: 0.4rem 0 0.6rem;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.request-title {
  color: var(--text-strong);
  font-weight: 600;
  margin-bottom: 0.6rem;
}
.request-meta {
  color: var(--text-soft);
  font-size: 0.85rem;
}
.request-query {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.request-history {
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  padding: 0.75rem 1rem;
}
.request-history-title {
  font-size: 0.85rem;
  color: var(--text-soft);
  margin-bottom: 0.5rem;
}
.request-history-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.request-history-item {
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
  color: var(--text-strong);
  border-radius: 6px;
  padding: 6px 10px;
  cursor: pointer;
  text-align: left;
}
.request-history-item small {
  color: var(--text-muted);
}
.request-toast {
  display: inline-flex;
  gap: 0.6rem;
  align-items: center;
  background: rgba(var(--success-rgb), 0.18);
  border: 1px solid rgba(var(--success-rgb), 0.45);
  color: var(--text-strong);
  border-radius: 999px;
  padding: 0.35rem 0.8rem;
  margin: 0 auto 1rem;
}
.request-card {
  margin: 0.9rem 0 1.2rem;
  display: flex;
  align-items: center;
  gap: 0.9rem;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 0.75rem 1rem;
}
.request-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(var(--success-rgb), 0.2);
}
.request-content {
  flex: 1;
  min-width: 0;
}
.request-label {
  font-size: 0.8rem;
  color: var(--text-muted);
}
.request-id {
  font-family: "Consolas", monospace;
  font-size: 1rem;
  color: var(--text-strong);
  letter-spacing: 0.5px;
}
.request-expiry {
  font-size: 0.78rem;
  color: var(--text-muted);
}
.request-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.ref-input {
  min-height: 88px;
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 10px;
  font-size: 0.85rem;
  font-family: "Consolas", monospace;
}
.ref-preview {
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  padding: 0.75rem 1rem;
}
.ref-meta {
  color: var(--text-muted);
  font-size: 0.82rem;
  margin-bottom: 0.5rem;
}
.ref-seq {
  font-family: "Consolas", monospace;
  font-size: 0.85rem;
  color: var(--text-strong);
  word-break: break-all;
}

.reference-card {
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 1rem;
}
.reference-card .field-row {
  margin-bottom: 0.75rem;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.75rem;
}
.reference-summary {
  font-size: 0.82rem;
  color: var(--text-muted);
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
  padding: 0.2rem 0.65rem;
  border-radius: 999px;
}


.summary-bar {
  margin: 0.75rem 0 1.25rem;
}
.summary-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.8rem;
  border-radius: 999px;
  border: 1px solid var(--border-soft);
  background: var(--surface-3);
  color: var(--text-soft);
  font-size: 0.85rem;
}
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  margin-top: 1.25rem;
  position: sticky;
  bottom: 1rem;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 0.75rem 1rem;
  z-index: 5;
}
.action-left {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.action-right {
  display: inline-flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.action-right .run-meta {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}
.action-right .run-meta .field-label {
  min-width: auto;
}

.run-cta {
  display: inline-flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.save-note {
  font-size: 0.78rem;
  color: var(--text-muted);
}


.options {
  margin-top: 1rem;
}
.opt-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.switch-label {
  color: var(--text-muted);
  font-size: 0.88rem;
  white-space: nowrap;
}
.mode-segment {
  display: inline-flex;
  gap: 6px;
  padding: 4px;
  border-radius: 999px;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
}
.mode-chip {
  border: none;
  padding: 0.35rem 0.9rem;
  border-radius: 999px;
  cursor: pointer;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.86rem;
  transition: background-color 0.2s ease, color 0.2s ease;
}
.mode-chip:hover:not(:disabled) {
  color: var(--text-strong);
}
.mode-chip.active {
  background: var(--success);
  color: #fff;
}
.mode-chip:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}
.resolved {
  margin-top: 0.6rem;
  color: var(--text-soft);
  font-size: 0.85rem;
}

.progress-wrapper {
  margin: 1.5rem 0;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.progress-bar {
  flex: 1;
  height: 8px;
  background: var(--surface-3);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
  box-shadow: inset 0 0 6px rgba(0, 0, 0, 0.25);
}
.progress-inner {
  height: 100%;
  background: linear-gradient(90deg, var(--success), var(--info));
  transition: width 0.18s linear;
  position: relative;
}
.progress-inner::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    120deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.35) 45%,
    rgba(255, 255, 255, 0) 100%
  );
  transform: translateX(-100%);
  animation: shimmer 1.8s linear infinite;
}
.progress-text {
  width: 48px;
  font-size: 0.8rem;
  color: var(--text-soft);
  text-align: right;
}
.progress-stage {
  filter: blur(calc((100 - var(--progress)) * 0.04px));
  transition: filter 0.35s ease;
}
.progress-meta {
  margin: -0.5rem 0 1.2rem;
  padding: 0.9rem 1rem;
  border-radius: 12px;
  border: 1px solid var(--border-soft);
  background: rgba(var(--nav-text-rgb), 0.05);
  display: grid;
  gap: 0.55rem;
}
.progress-meta-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.progress-badges {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.progress-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0.2rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--border-soft);
  background: var(--surface-3);
  color: var(--text-soft);
  font-size: 0.78rem;
  line-height: 1.2;
}
.progress-chip-stage {
  color: var(--text-strong);
  border-color: rgba(var(--info-rgb), 0.28);
  background: rgba(var(--info-rgb), 0.08);
}
.progress-chip-sse {
  color: var(--info-strong);
}
.progress-chip-batch,
.progress-chip-serial {
  color: var(--warning);
}
.progress-chip-unknown {
  color: var(--text-muted);
}
.progress-chip-live {
  color: var(--success);
  border-color: rgba(var(--success-rgb), 0.4);
  background: rgba(var(--success-rgb), 0.12);
}
.progress-summary {
  color: var(--text-soft);
  font-size: 0.82rem;
}
.progress-details {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 0.8rem;
}
.progress-seq {
  max-width: min(100%, 420px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-strong);
}
.progress-backend {
  display: flex;
  gap: 0.55rem;
  flex-wrap: wrap;
  align-items: baseline;
  color: var(--text-soft);
  font-size: 0.82rem;
  line-height: 1.45;
}
.progress-backend-label {
  color: var(--text-muted);
  font-weight: 600;
}
.progress-note {
  color: var(--text-muted);
  font-size: 0.82rem;
  line-height: 1.45;
}
.progress-note.warning {
  color: var(--warning);
}

@keyframes shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

.actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1.5rem;
}
.run-meta {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.btn-run {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0.8rem 2.2rem;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(var(--accent-rgb), 0.22);
}
.btn-run:hover {
  background: var(--accent-strong);
}

@media (max-width: 768px) {
  .home-container {
    padding: 1rem;
  }
  
  .card {
    padding: 1.5rem;
    border-radius: 8px;
  }
  
  .title {
    font-size: 1.4rem;
    margin-bottom: 0.5rem;
  }
  
  .subtitle {
    font-size: 0.9rem;
    margin-bottom: 1rem;
  }
  
  .section {
    padding: 1rem;
    margin-bottom: 1rem;
  }
  
  .section-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }
  
  .section-title {
    font-size: 0.9rem;
  }
  
  .reference-summary {
    font-size: 0.78rem;
    width: 100%;
  }
  
  .field-row {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }
  
  .field-label {
    font-size: 0.82rem;
    font-weight: 600;
  }
  
  .mode-toggle {
    width: 100%;
    flex-wrap: wrap;
    gap: 4px;
  }
  
  .mode-btn {
    flex: 1;
    min-width: 80px;
    padding: 0.5rem 0.6rem;
    text-align: center;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.82rem;
  }
  
  .mode-toggle.compact {
    width: 100%;
  }
  
  .field-note {
    font-size: 0.78rem;
  }
  
  .template-select-panel {
    gap: 0.75rem;
  }
  
  .row-group {
    flex-direction: column;
    gap: 0.75rem;
  }
  
  .select-group {
    width: 100%;
  }
  
  .select-wrapper {
    width: 100%;
  }
  
  .styled-select {
    width: 100%;
    min-height: 44px;
    font-size: 16px;
    padding: 12px 32px 12px 12px;
  }
  
  .select-with-button {
    flex-direction: column;
    gap: 0.5rem;
  }
  
  .random-button {
    width: 100%;
    min-height: 44px;
    justify-content: center;
  }
  
  .progress-wrapper {
    margin: 1rem 0;
    gap: 0.5rem;
  }
  
  .progress-bar {
    height: 6px;
  }
  
  .progress-text {
    width: 40px;
    font-size: 0.75rem;
  }
  
  .progress-meta {
    padding: 0.8rem 0.85rem;
  }
  
  .progress-meta-top {
    align-items: flex-start;
  }
  
  .progress-summary {
    width: 100%;
    font-size: 0.78rem;
  }
  
  .progress-details {
    gap: 0.5rem;
    font-size: 0.75rem;
  }
  
  .progress-seq {
    max-width: 100%;
  }
  
  .progress-chip {
    font-size: 0.72rem;
    padding: 0.15rem 0.5rem;
    min-height: 24px;
  }
  
  .actions {
    flex-direction: column;
    gap: 0.5rem;
    margin-top: 1rem;
  }
  
  .run-meta {
    width: 100%;
    flex-direction: column;
    align-items: stretch;
  }
  
  .field-input {
    min-width: 100%;
    min-height: 44px;
    font-size: 16px;
  }
  
  .btn-run {
    width: 100%;
    padding: 1rem;
    min-height: 48px;
    font-size: 1rem;
  }
  
  .request-card {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 1rem;
  }
  
  .request-actions {
    width: 100%;
    flex-direction: column;
  }
  
  .request-actions .btn-secondary {
    width: 100%;
    min-height: 44px;
    text-align: center;
    justify-content: center;
  }
  
  .request-panel {
    padding: 0.75rem;
  }
  
  .request-grid {
    gap: 0.5rem;
  }
  
  .request-query {
    flex-direction: column;
  }
  
  .request-query .field-input {
    min-width: 100%;
  }
  
  .request-query .btn-secondary {
    width: 100%;
    min-height: 44px;
  }

  .table-wrapper {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    margin: 0 -1rem;
    padding: 0 1rem;
  }

  .candidate-table {
    min-width: 540px;
  }

  .table-seqname {
    width: 120px;
    min-width: 120px;
  }

  .table-sequence {
    min-width: 160px;
  }

  .table-length {
    width: 70px;
    min-width: 70px;
  }

  .table-gc {
    width: 80px;
    min-width: 80px;
  }

  .action-bar {
    flex-direction: column;
    align-items: stretch;
    position: static;
    bottom: auto;
    gap: 0.75rem;
  }

  .action-left {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }

  .action-right {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }

  .btn-secondary {
    min-height: 44px;
  }

  .mode-segment {
    flex-wrap: wrap;
    gap: 4px;
    width: 100%;
    justify-content: center;
  }

  .mode-chip {
    min-height: 44px;
    padding: 0.5rem 1rem;
    font-size: 0.84rem;
    flex: 1;
    min-width: 80px;
    text-align: center;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}

@media (max-width: 480px) {
  .home-container {
    padding: 0.5rem;
  }
  
  .card {
    padding: 1rem;
  }
  
  .title {
    font-size: 1.2rem;
  }
  
  .subtitle {
    font-size: 0.85rem;
  }
  
  .section {
    padding: 0.75rem;
  }
}

/* ------------ Design Bench workspace redesign ------------ */
.bench-page {
  width: 100%;
  padding: 1.35rem clamp(1rem, 2.5vw, 2.5rem) 2.25rem;
}

.bench-shell {
  width: min(100%, 1480px);
  margin: 0 auto;
  display: grid;
  gap: 0.9rem;
}

.bench-header {
  display: grid;
  align-items: start;
  min-height: 66px;
  padding: 0 0.2rem 0.35rem;
}

.bench-page .title {
  margin: 0;
  color: var(--text-strong);
  font-size: clamp(1.8rem, 2.55vw, 2.35rem);
  line-height: 1.1;
  text-align: left;
}

.bench-page .subtitle {
  max-width: 680px;
  margin: 0.45rem 0 0;
  color: var(--text-muted);
  font-size: 0.98rem;
  line-height: 1.45;
  text-align: left;
}

.bench-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.48fr) minmax(390px, 0.82fr);
  gap: 0.9rem;
  align-items: stretch;
}

.bench-side {
  display: grid;
  gap: 0.9rem;
  align-content: start;
  min-width: 0;
}

.bench-bottom-grid {
  display: grid;
  gap: 0.9rem;
}

.bench-panel {
  min-width: 0;
  margin: 0;
  padding: 1.05rem;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: rgba(var(--card-bg-rgb), 0.9);
  box-shadow: 0 5px 18px rgba(15, 23, 42, 0.045);
}

.bench-page .section-title,
.bench-page .request-title {
  margin: 0 0 0.75rem;
  color: var(--text-strong);
  font-size: 1rem;
  font-weight: 720;
  line-height: 1.2;
}

.sequence-panel :deep(.seq-list) {
  min-width: 0;
}

.sequence-panel :deep(.mode-toggle) {
  margin-bottom: 0.35rem;
  border-radius: 8px;
}

.sequence-panel :deep(.fasta-input) {
  min-height: clamp(318px, 42vh, 410px);
  max-height: 52vh;
  border-color: var(--border-strong);
  background: rgba(var(--card-bg-rgb), 0.96);
  line-height: 1.45;
}

.sequence-panel :deep(.fasta-hint) {
  margin-top: 0.15rem;
}

.bench-page .reference-card {
  background: rgba(var(--card-bg-rgb), 0.9);
  padding: 1.05rem;
  border-radius: 8px;
}

.bench-page .section-header {
  align-items: center;
  margin-bottom: 0.75rem;
}

.bench-page .reference-summary {
  flex: 0 1 auto;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 6px;
  border: 1px solid var(--border-soft);
  background: rgba(var(--card-bg-rgb), 0.72);
  font-size: 0.78rem;
}

.bench-page .field-row {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  align-items: center;
  gap: 0.7rem;
  margin: 0 0 0.75rem;
  min-width: 0;
}

.bench-page .field-label,
.bench-page .switch-label,
.bench-page .label {
  min-width: 0;
  color: var(--text-soft);
  font-size: 0.82rem;
  font-weight: 650;
}

.bench-page .mode-toggle,
.bench-page .mode-segment {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--nav-text-rgb), 0.035);
}

.bench-page .mode-toggle.compact {
  width: min(100%, 180px);
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.bench-page .mode-btn,
.bench-page .mode-chip {
  min-width: 0;
  min-height: 40px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.4rem 0.45rem;
  font-size: 0.82rem;
  font-weight: 700;
  line-height: 1.15;
  text-align: center;
}

.bench-page .mode-btn.active,
.bench-page .mode-chip.active {
  background: rgba(var(--accent-rgb), 0.12);
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.34);
  box-shadow: 0 1px 2px rgba(var(--accent-rgb), 0.08);
}

.bench-page .mode-btn:hover:not(:disabled),
.bench-page .mode-chip:hover:not(:disabled) {
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.2);
}

.bench-page .field-note {
  margin: 0 0 0.75rem;
  color: var(--text-muted);
  font-size: 0.82rem;
  line-height: 1.45;
}

.bench-page .template-preview {
  margin-top: 0;
  padding: 0.75rem;
  border-radius: 8px;
  border-color: var(--border-soft);
  background: rgba(var(--nav-text-rgb), 0.025);
}

.bench-page .ref-meta {
  margin-bottom: 0;
}

.bench-page .ref-input {
  width: 100%;
  min-height: 160px;
  resize: vertical;
}

.bench-page .row-group {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.85rem;
}

.bench-page .select-group {
  margin-bottom: 0;
}

.bench-page .select-group .label {
  margin-bottom: 0.35rem;
}

.bench-page .select-wrapper {
  width: 100%;
}

.bench-page .styled-select {
  width: 100%;
  min-height: 46px;
  border: 1px solid var(--field-border);
  border-radius: 8px;
  background-color: var(--field-bg);
  color: var(--text-strong);
  font-size: 0.95rem;
  font-weight: 500;
  padding: 0.65rem 2.45rem 0.65rem 0.8rem;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.32);
}

.bench-page .styled-select:hover:not(:disabled) {
  border-color: rgba(var(--accent-rgb), 0.38);
}

.bench-page .styled-select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.18);
}

.bench-page .styled-select:disabled {
  color: var(--text-muted);
  border-color: rgba(30, 41, 59, 0.18);
  background-color: rgba(var(--card-bg-rgb), 0.58);
  opacity: 1;
}

.bench-page .select-arrow {
  right: 0.85rem;
  color: var(--text-strong);
  font-size: 0.8rem;
  opacity: 0.9;
}

.bench-page .select-with-button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.55rem;
}

.bench-page .random-button {
  min-height: 46px;
  border: 1px solid rgba(var(--accent-rgb), 0.26);
  border-radius: 8px;
  background: rgba(var(--accent-rgb), 0.07);
  color: var(--text-strong);
  font-size: 0.9rem;
  font-weight: 650;
  padding: 0.55rem 0.8rem;
}

.bench-page .table-wrapper {
  max-height: 300px;
  overflow: auto;
}

.run-panel {
  align-self: start;
}

.bench-page .action-bar {
  position: static;
  bottom: auto;
  z-index: auto;
  display: grid;
  gap: 0.8rem;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.bench-page .action-left,
.bench-page .action-right,
.bench-page .run-meta,
.bench-page .run-cta {
  display: grid;
  gap: 0.45rem;
  align-items: stretch;
}

.bench-page .action-left {
  grid-template-columns: 1fr;
}

.bench-page .mode-segment {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.bench-page .field-input {
  width: 100%;
  min-width: 0;
  min-height: 42px;
  border-radius: 7px;
  padding: 0.55rem 0.7rem;
  font-size: 0.92rem;
}

.bench-page .save-note {
  display: none;
}

.bench-page .cache-toggle {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  color: var(--text-muted);
  font-size: 0.82rem;
  line-height: 1.4;
  cursor: pointer;
}

.bench-page .cache-toggle input {
  flex: 0 0 auto;
  margin: 0.12rem 0 0;
  accent-color: var(--accent);
}

.bench-page .btn-run {
  width: 100%;
  min-height: 48px;
  border: 1px solid rgba(var(--accent-rgb), 0.28);
  border-radius: 7px;
  background: var(--accent);
  color: #fff;
  padding: 0.75rem 1rem;
  font-size: 0.95rem;
  font-weight: 750;
  box-shadow: 0 6px 16px rgba(var(--accent-rgb), 0.17);
}

.bench-page .btn-run:hover:not(:disabled) {
  background: var(--accent-strong);
}

.bench-page .progress-wrapper {
  margin: 0.75rem 0 0;
}

.bench-page .progress-meta {
  margin: 0.75rem 0 0;
  border-radius: 8px;
}

.bench-page .request-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 0.75rem;
  margin: 0.85rem 0 0;
  border-radius: 8px;
  background: rgba(var(--accent-rgb), 0.07);
}

.bench-page .request-id {
  overflow-wrap: anywhere;
}

.bench-page .request-actions {
  grid-column: 1 / -1;
}

.lookup-panel.request-panel {
  margin: 0;
  padding: 1.05rem;
  background: rgba(var(--card-bg-rgb), 0.9);
}

.lookup-panel .request-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem 1rem;
  align-items: end;
}

.lookup-panel .field-row {
  grid-template-columns: 112px minmax(0, 1fr);
  margin: 0;
}

.lookup-panel .request-query {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.5rem;
  min-width: 0;
}

.lookup-panel .request-history,
.lookup-panel .field-note,
.lookup-panel .field-error {
  grid-column: 1 / -1;
}

.results-panel {
  padding: 1.05rem;
}

.results-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.results-head .request-title {
  flex: 0 0 auto;
  margin: 0;
}

.results-panel .summary-bar {
  min-width: 0;
  margin: 0;
}

.results-panel .summary-pill {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 6px;
  background: rgba(var(--nav-text-rgb), 0.03);
}

@media (max-width: 1080px) {
  .bench-grid {
    grid-template-columns: 1fr;
  }

  .bench-side {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
  }

  .sequence-panel :deep(.fasta-input) {
    min-height: 300px;
    max-height: none;
  }
}

@media (max-width: 768px) {
  .bench-page {
    padding: 0.75rem 0.65rem 1rem;
  }

  .bench-shell {
    gap: 0.75rem;
  }

  .bench-header {
    min-height: 0;
    padding: 0;
  }

  .bench-page .title {
    font-size: 1.55rem;
    line-height: 1.12;
  }

  .bench-page .subtitle {
    margin-top: 0.45rem;
    font-size: 0.9rem;
  }

  .bench-side {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .bench-grid,
  .bench-bottom-grid {
    gap: 0.75rem;
  }

  .bench-panel {
    padding: 0.85rem;
  }

  .bench-page .section-header {
    display: grid;
    gap: 0.45rem;
  }

  .bench-page .reference-summary {
    width: 100%;
  }

  .bench-page .field-row,
  .lookup-panel .field-row {
    grid-template-columns: 1fr;
    gap: 0.4rem;
  }

  .bench-page .mode-btn,
  .bench-page .mode-chip {
    min-height: 42px;
    padding: 0.4rem 0.25rem;
    font-size: 0.78rem;
  }

  .bench-page .mode-toggle.compact {
    width: 100%;
  }

  .bench-page .row-group,
  .lookup-panel .request-grid {
    grid-template-columns: 1fr;
  }

  .lookup-panel .request-query {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .lookup-panel .request-query .btn-secondary {
    width: auto;
    min-width: 78px;
  }

  .sequence-panel :deep(.fasta-input) {
    min-height: 220px;
  }

  .bench-page .field-input,
  .bench-page .styled-select,
  .bench-page .btn-secondary {
    min-height: 44px;
    font-size: 16px;
  }

  .bench-page .btn-run {
    min-height: 48px;
    font-size: 1rem;
  }

  .results-head {
    display: grid;
    gap: 0.55rem;
  }

  .results-panel .summary-pill {
    white-space: normal;
  }
}

@media (max-width: 480px) {
  .bench-page {
    padding: 0.6rem 0.55rem 0.85rem;
  }

  .bench-page .title {
    font-size: 1.4rem;
  }

  .bench-page .subtitle {
    font-size: 0.84rem;
  }

  .bench-panel {
    padding: 0.8rem;
  }

  .sequence-panel :deep(.fasta-input) {
    min-height: 210px;
  }
}

:deep(.el-input-number .el-input__inner) {
  border-color: var(--field-border) !important;
  color: var(--field-text) !important;
  background: var(--field-bg) !important;
}
</style>
