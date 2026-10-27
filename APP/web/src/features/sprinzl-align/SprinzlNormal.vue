<!-- src/views/sprinzl/SprinzlNormal.vue -->
<template>
  <div class="sprinzl-page">
    <section class="sprinzl-shell">
      <header class="workspace-header">
        <div class="workspace-copy">
          <h1 class="title">Sprinzl Numbering</h1>
        </div>
        <div class="run-state" :class="{ active: running }">
          <span class="run-state-dot"></span>
          <span>{{ running ? runStage : "Ready" }}</span>
        </div>
      </header>

      <div class="workspace-grid">
        <section class="workspace-panel input-panel">
          <div class="panel-header">
            <div>
              <h2>Sequences to number</h2>
              <p>Paste FASTA or edit the sequence list directly.</p>
            </div>
          </div>

          <SeqInputList
            v-model="seqList"
            :default-seq="DEFAULT_SEQ"
            :default-fasta="DEFAULT_FASTA"
            default-mode="fasta"
          />
        </section>

        <aside class="workspace-panel setup-panel">
          <div class="panel-header">
            <div>
              <h2>Numbering setup</h2>
              <p>Choose how the run should be executed.</p>
            </div>
          </div>

          <div class="setup-stack">
            <div class="setting-block">
              <div class="setting-label-row">
                <span class="setting-label">Run mode</span>
                <details class="help-popover">
                  <summary aria-label="About run modes" title="About run modes">?</summary>
                  <div class="help-popover-content">
                    <p><strong>Agent</strong> selects additional scientific tools when the evidence is ambiguous. Deterministic Harness rules validate every candidate.</p>
                    <p><strong>DP only</strong> runs the deterministic comparison workflow without language-model planning.</p>
                  </div>
                </details>
              </div>
              <div class="mode-segment" role="radiogroup" aria-label="Numbering Mode">
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

            <div class="setting-block">
              <div class="setting-label-row">
                <label class="field-label" for="sprinzl-domain">Biological domain</label>
                <details class="help-popover">
                  <summary aria-label="About biological domain" title="About biological domain">?</summary>
                  <div class="help-popover-content">
                    Use General for mixed or unknown sources. When the source is known, select it so tRNAscan-SE uses the corresponding domain search mode.
                  </div>
                </details>
              </div>
              <select
                id="sprinzl-domain"
                v-model="domainHint"
                class="field-input domain-select"
                :disabled="running"
              >
                <option
                  v-for="option in domainOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </option>
              </select>
            </div>

            <div v-if="domainHint === 'mitochondrial'" class="setting-block">
              <label class="field-label" for="sprinzl-mitochondrial-subtype">
                Mitochondrial model
              </label>
              <select
                id="sprinzl-mitochondrial-subtype"
                v-model="mitochondrialSubtype"
                class="field-input domain-select"
                :disabled="running"
              >
                <option value="vertebrate">Vertebrate (M: vert)</option>
                <option value="mammal">Mammal (M: mammal)</option>
              </select>
            </div>

            <div class="setting-block">
              <label class="field-label" for="sprinzl-detail-language">Run details language</label>
              <select
                id="sprinzl-detail-language"
                v-model="interpretationLanguage"
                class="field-input domain-select"
              >
                <option
                  v-for="option in interpretationLanguageOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </option>
              </select>
            </div>

            <button class="btn-run" :disabled="running" @click="runAlign">
              {{ running ? "Numbering..." : "Run Numbering" }}
            </button>
            <label class="cache-toggle">
              <input v-model="bypassCache" type="checkbox" :disabled="running" />
              <span>Run fresh (skip cache and replace it)</span>
            </label>

            <div v-if="requestId" class="request-card">
              <div class="request-dot"></div>
              <div class="request-content">
                <div class="request-inline">
                  <span class="request-label">Request ID</span>
                  <span class="request-id">{{ requestId }}</span>
                </div>
                <div v-if="requestExpiresAt" class="request-expiry">
                  Expires {{ formatTime(requestExpiresAt) }}
                </div>
              </div>
              <div class="request-actions">
                <button type="button" class="btn-secondary" @click="copyRequestId">
                  Copy
                </button>
                <button type="button" class="btn-secondary" @click="useRequestId">
                  Query
                </button>
              </div>
              <details class="request-save-details">
                <summary>{{ boundEmail ? "Email saved" : "Save this run by email" }}</summary>
                <form class="request-email" @submit.prevent="bindEmailToSession">
                  <label class="field-label" for="sprinzl-session-email">
                    {{ boundEmail ? "Saved email" : "Email address" }}
                  </label>
                  <div class="request-email-row">
                    <input
                      id="sprinzl-session-email"
                      v-model="requestEmail"
                      type="email"
                      autocomplete="email"
                      required
                      class="field-input"
                      placeholder="you@example.com"
                    />
                    <button
                      type="submit"
                      class="btn-secondary"
                      :disabled="emailBinding"
                    >
                      {{ emailBinding ? "Saving..." : boundEmail ? "Update email" : "Save email" }}
                    </button>
                  </div>
                </form>
              </details>
            </div>

            <div class="request-panel" :class="{ expanded: historyOpen }">
              <div class="request-title-row">
                <button
                  type="button"
                  class="request-toggle"
                  :aria-expanded="historyOpen"
                  aria-controls="saved-run-lookup"
                  @click="historyOpen = !historyOpen"
                >
                  <span class="request-title">Saved runs & lookup</span>
                  <span class="request-toggle-hint">{{ historyOpen ? "Hide" : "Open" }}</span>
                </button>
                <details class="help-popover" @click.stop>
                  <summary aria-label="About saved run lookup" title="About saved run lookup">?</summary>
                  <div class="help-popover-content">
                    Load a saved run with its Request ID, or use an email address to find associated IDs.
                  </div>
                </details>
              </div>
              <div v-show="historyOpen" id="saved-run-lookup" class="request-grid">
                <div v-if="!requestId" class="field-row">
                  <span class="field-label">Save current run by email</span>
                  <input
                    id="sprinzl-email"
                    v-model="requestEmail"
                    type="email"
                    autocomplete="email"
                    class="field-input"
                    placeholder="you@example.com"
                  />
                  <span class="field-note">Optional. You can also associate an email after the run.</span>
                </div>
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
            </div>
          </div>
        </aside>
      </div>

      <section class="workspace-panel status-panel">
        <div class="panel-header status-header">
          <div>
            <h2>Results & run state</h2>
            <p>{{ statusSummary }}</p>
          </div>
        </div>

        <div class="progress-wrapper" v-show="running || progress > 0">
          <div class="progress-bar">
            <div class="progress-inner" :style="{ width: progress + '%' }"></div>
          </div>
          <span class="progress-text">{{ progress }}%</span>
        </div>

        <div v-if="running || progress > 0" class="progress-meta">
          <div class="progress-meta-top">
            <div class="progress-badges">
              <span class="progress-chip progress-chip-stage">{{ runStage }}</span>
              <span class="progress-chip" :class="transportChipClass">{{ transportLabel }}</span>
              <span v-if="running" class="progress-chip progress-chip-live">Live</span>
            </div>
            <div class="progress-summary">{{ progressSummary }}</div>
          </div>

          <div class="progress-details">
            <span>Elapsed {{ formatDuration(elapsedSeconds) }}</span>
            <span v-if="running">Last update {{ silenceSeconds }}s ago</span>
            <span v-if="currentIndex !== null">Current #{{ currentIndex + 1 }}</span>
            <span v-if="currentSequence" class="progress-seq" :title="currentSequence">
              {{ currentSequence }}
            </span>
          </div>

          <div v-if="backendSummary" class="progress-backend">
            <span class="progress-backend-label">Backend</span>
            <span>{{ backendSummary }}</span>
          </div>

          <div
            v-if="progressHint"
            class="progress-note"
            :class="{ warning: showWaitingHint }"
          >
            {{ progressHint }}
          </div>
        </div>

        <div v-if="results.length || running" class="results-wrap">
          <ResultsSection
            :results="results"
            :editable="false"
            reference-mode="per-row"
            :interpretation-language="interpretationLanguage"
          />
        </div>
        <div v-else class="empty-results">
          Run numbering to populate the Sprinzl table and structure views.
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import SeqInputList from "@/features/sprinzl-align/components/SeqInputList.vue";
import ResultsSection from "@/features/sprinzl-align/components/ResultsPanel.vue";
import type {
  AlignmentResult,
  SeqEntry,
  SessionListItem,
} from "@/features/sprinzl-align/model/types";
import { useSprinzlSession } from "@/features/sprinzl-align/composables/useSession";
import { useSprinzlAlignmentRunner } from "@/features/sprinzl-align/composables/useAlignmentRunner";
import { useSprinzlUrlSync } from "@/features/sprinzl-align/composables/useUrlSync";

/* ---------- 基础状态 ---------- */
const DEFAULT_SEQ =
  "GGGCCGGUAGCUCAGCCUGGAAGAGUGUCCGGCUUGCAACCGGAAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA";
const DEFAULT_ANTICODE = "ACG";
const DEFAULT_FASTA = `>acidHosp1:Ala-TGC
GGGCCGGUAGCUCAGCCUGGAAGAGUGUCCGGCUUGCAACCGGAAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA
>acidHosp1:Ala-GGC
GGGCCGGUAGCUCAGCCUGGAAGAGUGCUCGGUUGGCAUCCGAGAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA
>acidHosp1:Ala-CGC
GGGCCGGUAGCUCAGCUGGAAGAGUGCCCGCCUCGCAAGCGGGAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA
>acidHosp1:Arg-TCT
GGACCCGUAGCUCAGCCAGGAUAGAGCGCCGGCCUUCUAAGUCGGUGGUCACGGGUUCGAAUCCCGUCGGGUCCGCCA
>acidHosp1:Arg-CCT
GGACCCGUAGCUCAGCCAGGAUAGAGCGCCGGCCUCCUAAGCCGGUGGUCACGGGUUCGAAUCCCGUCGGGUCCGCCA
>acidHosp1:Ile-GAT
GGGCCCGUAGCUUAGCAUGGUUAGAGCGCCCGGCUGAUAACCGGGAGGUCGGGGGUUCAAAUCCCCCCGGGCCCA
>acetArab:Ala-TGC
GGGGGUAUAGCUCAGUUGGGAGAGCGCCUGCCUUGCAAGCAGGAGGUCAGCGGUUCGAAUCCGCUUACCUCCACCA
>acetArab:Ala-GGC
GGGGAUAUAGCUCAGUGGGAGAGCGCUUGUCUGGCAGACAAGAGGUCGAGGGUUCAAGUCCCUCUAUCUCCACCA
>acetArab:Arg-ACG
GCGCCCGUAGCUCAACUGGAUAGAGUAACUGACUACGAAUCAGGAGGUUGGAGGUUCGACUCCUCCCGGGCGCACCA
>acetArab:Arg-TCT
GCGCCUGUAGCUCAGGUGGAUAGAGCAAUGGACUUCUAAUCCAUGCGUCGGGGGUUCGAGUCCUCCCAGGCGUACCA
>acetArab:Arg-TCG
GCGCCCGUAGCUCAACUGGAUAGAGUCGCAGACUUCGAAUCUGAAGGUUGGAGGUUCGAGUCCUCCCGGGCGUGCCA
>acetArab:Ile-GAT
GGGUCUAUGGCUCAGUUGGUCAGAGCGCGCGCCUGAUAAGCGUGAGGUCGGUGGUUCGAAUCCACCUAGACCCACCA`;
const seqList = ref<SeqEntry[]>([
  {
    name: "acidHosp1:Ala-TGC",
    seq: "GGGCCGGUAGCUCAGCCUGGAAGAGUGUCCGGCUUGCAACCGGAAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA",
    anticode: "TGC",
  },
  {
    name: "acidHosp1:Ala-GGC",
    seq: "GGGCCGGUAGCUCAGCCUGGAAGAGUGCUCGGUUGGCAUCCGAGAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA",
    anticode: "GGC",
  },
  {
    name: "acidHosp1:Ala-CGC",
    seq: "GGGCCGGUAGCUCAGCUGGAAGAGUGCCCGCCUCGCAAGCGGGAGGUCCCGGGUUCAAAUCCCGGCCGGUCCA",
    anticode: "CGC",
  },
  {
    name: "acidHosp1:Arg-TCT",
    seq: "GGACCCGUAGCUCAGCCAGGAUAGAGCGCCGGCCUUCUAAGUCGGUGGUCACGGGUUCGAAUCCCGUCGGGUCCGCCA",
    anticode: "TCT",
  },
  {
    name: "acidHosp1:Arg-CCT",
    seq: "GGACCCGUAGCUCAGCCAGGAUAGAGCGCCGGCCUCCUAAGCCGGUGGUCACGGGUUCGAAUCCCGUCGGGUCCGCCA",
    anticode: "CCT",
  },
  {
    name: "acidHosp1:Ile-GAT",
    seq: "GGGCCCGUAGCUUAGCAUGGUUAGAGCGCCCGGCUGAUAACCGGGAGGUCGGGGGUUCAAAUCCCCCCGGGCCCA",
    anticode: "GAT",
  },
  {
    name: "acetArab:Ala-TGC",
    seq: "GGGGGUAUAGCUCAGUUGGGAGAGCGCCUGCCUUGCAAGCAGGAGGUCAGCGGUUCGAAUCCGCUUACCUCCACCA",
    anticode: "TGC",
  },
  {
    name: "acetArab:Ala-GGC",
    seq: "GGGGAUAUAGCUCAGUGGGAGAGCGCUUGUCUGGCAGACAAGAGGUCGAGGGUUCAAGUCCCUCUAUCUCCACCA",
    anticode: "GGC",
  },
  {
    name: "acetArab:Arg-ACG",
    seq: "GCGCCCGUAGCUCAACUGGAUAGAGUAACUGACUACGAAUCAGGAGGUUGGAGGUUCGACUCCUCCCGGGCGCACCA",
    anticode: "ACG",
  },
  {
    name: "acetArab:Arg-TCT",
    seq: "GCGCCUGUAGCUCAGGUGGAUAGAGCAAUGGACUUCUAAUCCAUGCGUCGGGGGUUCGAGUCCUCCCAGGCGUACCA",
    anticode: "TCT",
  },
  {
    name: "acetArab:Arg-TCG",
    seq: "GCGCCCGUAGCUCAACUGGAUAGAGUCGCAGACUUCGAAUCUGAAGGUUGGAGGUUCGAGUCCUCCCGGGCGUGCCA",
    anticode: "TCG",
  },
  {
    name: "acetArab:Ile-GAT",
    seq: "GGGUCUAUGGCUCAGUUGGUCAGAGCGCGCGCCUGAUAAGCGUGAGGUCGGUGGUUCGAAUCCACCUAGACCCACCA",
    anticode: "GAT",
  }
]);
type LlmMode = "dp" | "llm_fast";
type DomainHint =
  | "unknown"
  | "archaea"
  | "bacteria"
  | "cytosolic_eukaryote"
  | "chloroplast"
  | "mitochondrial";
type MitochondrialSubtype = "vertebrate" | "mammal";
type InterpretationLanguage = "en" | "zh-CN" | "de" | "fr";
const llmMode = ref<LlmMode>("llm_fast");
const domainHint = ref<DomainHint>("unknown");
const mitochondrialSubtype = ref<MitochondrialSubtype>("vertebrate");
const interpretationLanguage = ref<InterpretationLanguage>("en");
const interpretationLanguageOptions: Array<{ value: InterpretationLanguage; label: string }> = [
  { value: "en", label: "English" },
  { value: "zh-CN", label: "中文" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
];
const domainOptions: Array<{ value: DomainHint; label: string }> = [
  { value: "unknown", label: "General / mixed source (G)" },
  { value: "bacteria", label: "Bacteria (B)" },
  { value: "archaea", label: "Archaea (A)" },
  { value: "cytosolic_eukaryote", label: "Cytosolic eukaryote (E)" },
  { value: "chloroplast", label: "Chloroplast / cyanelle (O)" },
  { value: "mitochondrial", label: "Mitochondrial (M)" },
];
const use_llm = computed(() => llmMode.value !== "dp");
const llm_reasoning = computed(() => false);
const running = ref(false);
const bypassCache = ref(false);
const results = ref<AlignmentResult[]>([]);
const requestEmail = ref("");
const requestId = ref("");
const requestExpiresAt = ref("");
const requestMessage = ref("");
const requestError = ref("");
const boundEmail = ref("");
const emailBinding = ref(false);
const queryId = ref("");
const queryEmail = ref("");
const emailHistory = ref<SessionListItem[]>([]);
const historyOpen = ref(false);
const progress = ref(0);

function formatTime(raw: string) {
  if (!raw) return "";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return raw;
  return dt.toLocaleString();
}

function formatDuration(totalSeconds: number) {
  const sec = Math.max(0, Math.floor(totalSeconds || 0));
  const minutes = Math.floor(sec / 60);
  const seconds = sec % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function buildPayload() {
  return {
    mode: "sprinzl",
    use_llm: use_llm.value,
    llm_reasoning: llm_reasoning.value,
    llm_mode: llmMode.value,
    interpretation_language: interpretationLanguage.value,
    sequences: seqList.value,
    ...buildAlignmentOptions(),
    bypass_cache: bypassCache.value,
  };
}

function buildAlignmentOptions() {
  return {
    input_type: "unknown",
    domain_hint: domainHint.value,
    cca_status: "unknown",
    intron_status: "unknown",
    trna_class: "unknown",
    mitochondrial_subtype:
      domainHint.value === "mitochondrial" ? mitochondrialSubtype.value : "unknown",
    enable_root_recovery: false,
    bypass_cache: bypassCache.value,
  };
}

const {
  resetRequestState,
  saveSession,
  bindEmailToSession,
  copyRequestId,
  useRequestId,
  loadByRequestId,
  loadByEmail,
} = useSprinzlSession({
  requestEmail,
  requestId,
  requestExpiresAt,
  requestMessage,
  requestError,
  boundEmail,
  emailBinding,
  queryId,
  queryEmail,
  emailHistory,
  results,
  interpretationLanguage,
});

const {
  runAlign,
  runStage,
  transportMode,
  currentSequence,
  currentIndex,
  doneCount,
  totalCount,
  successCount,
  failedCount,
  abstainedCount,
  backendStatus,
  backendPending,
  backendWorkers,
  elapsedSeconds,
  silenceSeconds,
} = useSprinzlAlignmentRunner({
  seqList,
  useLlm: use_llm,
  llmReasoning: llm_reasoning,
  results,
  running,
  progress,
  resetRequestState,
  saveSession,
  buildPayload,
  buildAlignmentOptions,
});

const transportLabel = computed(() => {
  switch (transportMode.value) {
    case "sse":
      return "Live stream";
    case "batch":
      return "Batch API";
    case "serial":
      return "Serial fallback";
    default:
      return "Preparing";
  }
});

const transportChipClass = computed(() => `progress-chip-${transportMode.value}`);

const progressSummary = computed(() => {
  const total = Math.max(totalCount.value, seqList.value.length, 0);
  if (!total) return "Waiting to start";
  return `${doneCount.value}/${total} done · ${successCount.value} accepted · ${abstainedCount.value} abstained · ${failedCount.value} failed`;
});

const loadedResultsSummary = computed(() => {
  const loaded = results.value;
  const total = loaded.length;
  const done = loaded.filter((result) => !result.pending).length;
  const failed = loaded.filter((result) => !result.pending && Boolean(result.error)).length;
  const abstained = loaded.filter(
    (result) =>
      !result.pending
      && !result.error
      && String(result.agent?.work_state_summary?.disposition || "") === "abstained",
  ).length;
  const accepted = Math.max(0, done - failed - abstained);
  return `${done}/${total} done · ${accepted} accepted · ${abstained} abstained · ${failed} failed`;
});

const statusSummary = computed(() => {
  if (running.value || progress.value > 0) return progressSummary.value;
  if (results.value.length) return loadedResultsSummary.value;
  return "No active run. Results will appear here after numbering starts.";
});

const showWaitingHint = computed(() => running.value && silenceSeconds.value >= 15);

const backendSummary = computed(() => {
  const parts: string[] = [];
  const status = String(backendStatus.value || "").trim();
  if (status) parts.push(status);
  if (backendWorkers.value > 0) {
    parts.push(`${backendWorkers.value} worker${backendWorkers.value > 1 ? "s" : ""} active`);
  }
  if (backendPending.value > 0) {
    parts.push(`${backendPending.value} pending`);
  }
  return parts.join(" · ");
});

const progressHint = computed(() => {
  if (runStage.value === "Completed") {
    return "Alignment finished. Rendering the final result table.";
  }
  if (runStage.value === "Completed with errors") {
    return "The run finished with missing or failed rows. Check the per-row errors below.";
  }
  if (showWaitingHint.value) {
    if (transportMode.value === "serial") {
      return "Still working on the current sequence. Serial fallback can stay quiet for a while between results.";
    }
    if (use_llm.value) {
      return "The request is still active. LLM and template-sync steps can take tens of seconds without new events.";
    }
    return "The request is still active. Waiting for the server to return the next result.";
  }
  switch (transportMode.value) {
    case "sse":
      return "Results should appear one by one as the server streams them back.";
    case "batch":
      return "Batch mode waits for the server to assemble the full response before rows fill in.";
    case "serial":
      return "Serial fallback is slower, but it continues sequence by sequence instead of stopping.";
    default:
      return running.value ? "Preparing the request and opening the alignment pipeline." : "";
  }
});

useSprinzlUrlSync({
  seqList,
  running,
  defaultAnticode: DEFAULT_ANTICODE,
  runAlign,
});
</script>

<style scoped>
/* ------------ 布局 ------------ */
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

/* ------------ 标题 ------------ */
.title {
  font-size: 2rem;
  color: var(--text-strong);
  margin-bottom: 1.5rem;
  text-align: center;
}

/* ------------ 选项 ------------ */
.options {
  margin-top: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.opt-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.6rem;
}
.switch-label {
  margin: 0 0.5rem;
  color: var(--text-muted);
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
  border: 1px solid transparent;
  padding: 0.35rem 0.8rem;
  border-radius: 999px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 0.82rem;
}
.mode-chip.active {
  background: rgba(var(--success-rgb), 0.2);
  color: var(--text-strong);
  border-color: rgba(var(--success-rgb), 0.55);
}
.mode-chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.mode-warning {
  color: var(--warning);
  font-size: 0.78rem;
  line-height: 1.35;
  max-width: 420px;
}
.opt-number-label {
  color: var(--text-soft);
  margin: 0 0.5rem;
}

/* ------------ 进度条 ------------ */
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
.progress-chip-batch {
  color: var(--warning);
}
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
.progress-note {
  color: var(--text-muted);
  font-size: 0.82rem;
  line-height: 1.45;
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

/* ------------ 按钮 ------------ */
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

.field-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}
.field-label {
  color: var(--text-soft);
  font-size: 0.85rem;
}
.field-input {
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.85rem;
  min-width: 220px;
}
.field-note {
  color: var(--text-muted);
  font-size: 0.82rem;
}
.field-error {
  color: var(--danger);
  font-size: 0.82rem;
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
.request-panel {
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  padding: 0.5rem 0.9rem;
  background: var(--surface-3);
  margin-top: 0.75rem;
}
.request-title {
  color: var(--text-strong);
  font-weight: 600;
  margin-bottom: 0.6rem;
}
.request-hint {
  margin: 0.4rem 0 0.6rem;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.request-grid {
  display: grid;
  gap: 0.75rem;
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

@media (max-width: 768px) {
  .home-container {
    padding: 1rem;
  }

  .card {
    padding: 1.5rem;
    border-radius: 8px;
  }

  .title {
    font-size: 1.5rem;
    margin-bottom: 1rem;
  }

  .options {
    margin-top: 1rem;
  }

  .opt-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .switch-label {
    margin: 0;
    font-weight: 600;
  }

  .mode-segment {
    width: 100%;
    flex-wrap: wrap;
    gap: 4px;
  }

  .mode-chip {
    flex: 1;
    min-width: 80px;
    padding: 0.5rem 0.6rem;
    text-align: center;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .mode-warning {
    max-width: 100%;
    font-size: 0.75rem;
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

  .field-row {
    flex-direction: column;
    align-items: stretch;
    gap: 0.4rem;
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
}

@media (max-width: 480px) {
  .home-container {
    padding: 0.5rem;
  }

  .card {
    padding: 1rem;
  }

  .title {
    font-size: 1.3rem;
  }
}

/* ------------ element-plus 适配 ------------ */
:deep(.el-input-number .el-input__inner) {
  border-color: var(--field-border) !important;
  color: var(--field-text) !important;
  background: var(--field-bg) !important;
}
:deep(.el-checkbox__label),
:deep(.el-checkbox .el-checkbox__inner) {
  color: var(--text-strong) !important;
  border-color: var(--border-strong) !important;
}
:deep(.el-checkbox.is-checked .el-checkbox__inner) {
  border-color: var(--success) !important;
  background: var(--success) !important;
}

/* ------------ Sprinzl workspace redesign ------------ */
.sprinzl-page {
  width: 100%;
  padding: 1.25rem clamp(1rem, 2.2vw, 2rem) 2rem;
}

.sprinzl-shell {
  width: min(100%, 1360px);
  margin: 0 auto;
  display: grid;
  gap: 1rem;
}

.workspace-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.15rem 0.15rem 0.35rem;
}

.workspace-copy {
  min-width: 0;
}

.title {
  margin: 0;
  color: var(--text-strong);
  font-size: clamp(1.8rem, 2.8vw, 2.55rem);
  line-height: 1.05;
  text-align: left;
}

.run-state {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 34px;
  padding: 0.35rem 0.7rem;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: rgba(var(--card-bg-rgb), 0.58);
  color: var(--text-soft);
  font-size: 0.82rem;
  font-weight: 650;
}

.run-state-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
}

.run-state.active {
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.28);
  background: rgba(var(--accent-rgb), 0.08);
}

.run-state.active .run-state-dot {
  background: var(--accent);
  box-shadow: 0 0 0 4px rgba(var(--accent-rgb), 0.14);
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(330px, 0.72fr);
  gap: 1rem;
  align-items: stretch;
}

.workspace-panel {
  min-width: 0;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--card-bg-rgb), 0.74);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(10px) saturate(120%);
}

.input-panel,
.setup-panel {
  padding: 1rem;
}

.input-panel {
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.9rem;
}

.panel-header h2 {
  margin: 0;
  color: var(--text-strong);
  font-size: 1rem;
  line-height: 1.2;
}

.panel-header p {
  margin: 0.25rem 0 0;
  color: var(--text-muted);
  font-size: 0.84rem;
  line-height: 1.45;
}

.input-panel :deep(.seq-list) {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.input-panel :deep(.fasta-block) {
  flex: 1;
  min-height: 0;
}

.input-panel :deep(.fasta-input) {
  flex: 1;
  min-height: clamp(260px, 42vh, 460px);
  max-height: none;
}

.setup-panel {
  align-self: stretch;
}

.setup-stack {
  display: grid;
  gap: 0.85rem;
}

.setting-block {
  display: grid;
  gap: 0.45rem;
  min-width: 0;
}

.setting-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.setting-label,
.field-label {
  color: var(--text-soft);
  font-size: 0.82rem;
  font-weight: 650;
}

.mode-segment {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: rgba(var(--nav-text-rgb), 0.045);
}

.mode-chip {
  min-width: 0;
  min-height: 38px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.4rem 0.45rem;
  font-size: 0.82rem;
  font-weight: 700;
  line-height: 1.15;
  text-align: center;
}

.mode-chip:hover:not(:disabled) {
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.2);
}

.mode-chip.active {
  background: rgba(var(--accent-rgb), 0.13);
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.34);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.24);
}

.mode-chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.help-popover {
  position: relative;
  flex: 0 0 auto;
}

.help-popover summary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 23px;
  height: 23px;
  border: 1px solid var(--border-soft);
  border-radius: 50%;
  background: rgba(var(--card-bg-rgb), 0.66);
  color: var(--text-muted);
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 800;
  line-height: 1;
  list-style: none;
}

.help-popover summary::-webkit-details-marker {
  display: none;
}

.help-popover summary:hover,
.help-popover summary:focus-visible,
.help-popover[open] summary {
  border-color: rgba(var(--accent-rgb), 0.42);
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent-strong);
  outline: none;
}

.help-popover-content {
  position: absolute;
  top: calc(100% + 7px);
  right: 0;
  z-index: 30;
  width: min(290px, calc(100vw - 3rem));
  padding: 0.7rem 0.75rem;
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  background: var(--surface-1);
  box-shadow: var(--shadow-soft);
  color: var(--text-soft);
  font-size: 0.78rem;
  line-height: 1.45;
}

.help-popover-content p {
  margin: 0;
}

.help-popover-content p + p {
  margin-top: 0.55rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-soft);
}

.help-popover-content strong {
  color: var(--text-strong);
}

.field-input {
  width: 100%;
  min-width: 0;
  min-height: 40px;
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 7px;
  padding: 0.55rem 0.7rem;
  font-size: 0.92rem;
}
/* The dropdown caret drawn by the customizable select needs its own room. */
select.field-input {
  padding-inline-end: 1.7rem;
}

.field-input::placeholder {
  color: var(--field-placeholder);
}

.btn-run {
  width: 100%;
  min-height: 46px;
  border: 1px solid rgba(var(--accent-rgb), 0.28);
  border-radius: 7px;
  background: var(--accent);
  color: #fff;
  padding: 0.75rem 1rem;
  cursor: pointer;
  box-shadow: 0 9px 20px rgba(var(--accent-rgb), 0.2);
  font-size: 0.95rem;
  font-weight: 750;
}

.btn-run:hover:not(:disabled) {
  background: var(--accent-strong);
}

.btn-run:disabled {
  opacity: 0.64;
  cursor: not-allowed;
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  border: 1px solid var(--border-soft);
  background: rgba(var(--card-bg-rgb), 0.62);
  color: var(--text-strong);
  border-radius: 7px;
  padding: 0.45rem 0.7rem;
  font-size: 0.82rem;
  cursor: pointer;
}

.btn-secondary:hover {
  border-color: rgba(var(--accent-rgb), 0.35);
  color: var(--accent-strong);
}

.request-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 0.75rem;
  margin: 0;
  padding: 0.8rem;
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  border-radius: 8px;
  background: rgba(var(--accent-rgb), 0.07);
}

.request-dot {
  width: 10px;
  height: 10px;
  margin-top: 0.35rem;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(var(--success-rgb), 0.18);
}

.request-content {
  min-width: 0;
}

.request-label {
  color: var(--text-muted);
  font-size: 0.76rem;
  font-weight: 650;
}

.request-id {
  overflow-wrap: anywhere;
  color: var(--text-strong);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
    "Courier New", monospace;
  font-size: 0.9rem;
}

.request-inline {
  display: flex;
  align-items: baseline;
  gap: 0.55rem;
  flex-wrap: wrap;
}

.request-expiry {
  margin-top: 0.15rem;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.request-actions {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.request-email {
  grid-column: 1 / -1;
  display: grid;
  gap: 0.35rem;
  padding-top: 0.7rem;
  border-top: 1px solid rgba(var(--accent-rgb), 0.16);
}

.request-email-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.45rem;
}

.request-email-row .btn-secondary:disabled {
  cursor: wait;
  opacity: 0.65;
}

.request-save-details {
  grid-column: 1 / -1;
  padding-top: 0.55rem;
  border-top: 1px solid rgba(var(--accent-rgb), 0.16);
}

.request-save-details summary {
  color: var(--accent-strong);
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 650;
}

.request-save-details[open] summary {
  margin-bottom: 0.55rem;
}

.request-panel {
  margin: 0;
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--nav-text-rgb), 0.035);
}

.request-title {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.88rem;
  font-weight: 700;
}

.request-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
  margin-bottom: 0;
}

.request-panel.expanded .request-title-row {
  margin-bottom: 0.6rem;
}

.request-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.request-toggle::before {
  content: "›";
  display: inline-block;
  color: var(--accent-strong);
  font-size: 1.15rem;
  line-height: 1;
  transform: rotate(0deg);
  transition: transform 0.15s ease;
}

.request-panel.expanded .request-toggle::before {
  transform: rotate(90deg);
}

.request-toggle-hint {
  color: var(--text-muted);
  font-size: 0.76rem;
  font-weight: 550;
}

.request-grid {
  display: grid;
  gap: 0.65rem;
}

.field-row {
  display: grid;
  gap: 0.35rem;
}

.request-query {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.45rem;
}

.request-history {
  padding: 0.65rem;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--card-bg-rgb), 0.52);
}

.request-history-title {
  margin-bottom: 0.45rem;
  color: var(--text-soft);
  font-size: 0.82rem;
  font-weight: 650;
}

.request-history-list {
  display: grid;
  gap: 0.4rem;
}

.request-history-item {
  min-width: 0;
  border: 1px solid var(--border-soft);
  background: rgba(var(--card-bg-rgb), 0.62);
  color: var(--text-strong);
  border-radius: 7px;
  padding: 0.45rem 0.55rem;
  cursor: pointer;
  text-align: left;
  overflow-wrap: anywhere;
}

.request-history-item small {
  color: var(--text-muted);
}

.field-note {
  color: var(--text-muted);
  font-size: 0.82rem;
}

.field-error {
  color: var(--danger);
  font-size: 0.82rem;
}

.status-panel {
  padding: 1rem;
}

.status-header {
  margin-bottom: 0.7rem;
}

.progress-wrapper {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  margin: 0 0 0.75rem;
}

.progress-bar {
  flex: 1;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(var(--nav-text-rgb), 0.09);
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.08);
}

.progress-inner {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), var(--success));
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
  width: 42px;
  color: var(--text-muted);
  font-size: 0.8rem;
  text-align: right;
}

.progress-meta {
  display: grid;
  gap: 0.55rem;
  margin: 0 0 0.85rem;
  padding: 0.8rem;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--nav-text-rgb), 0.035);
}

.progress-meta-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.7rem;
  flex-wrap: wrap;
}

.progress-badges,
.progress-details,
.progress-backend {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.progress-chip {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0.18rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--border-soft);
  background: rgba(var(--card-bg-rgb), 0.58);
  color: var(--text-soft);
  font-size: 0.76rem;
  line-height: 1.2;
}

.progress-chip-stage {
  color: var(--accent-strong);
  border-color: rgba(var(--accent-rgb), 0.24);
  background: rgba(var(--accent-rgb), 0.08);
}

.progress-chip-sse {
  color: var(--info-strong);
}

.progress-chip-batch,
.progress-chip-serial {
  color: var(--warning, #a16207);
}

.progress-chip-unknown {
  color: var(--text-muted);
}

.progress-chip-live {
  color: var(--success);
  border-color: rgba(var(--success-rgb), 0.34);
  background: rgba(var(--success-rgb), 0.1);
}

.progress-summary,
.progress-details,
.progress-backend,
.progress-note {
  color: var(--text-muted);
  font-size: 0.8rem;
  line-height: 1.45;
}

.progress-backend-label {
  color: var(--text-soft);
  font-weight: 700;
}

.progress-note.warning {
  color: var(--warning, #a16207);
}

.progress-seq {
  max-width: min(100%, 520px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-strong);
}

.results-wrap {
  min-width: 0;
}

.empty-results {
  display: flex;
  align-items: center;
  min-height: 92px;
  padding: 1rem;
  border: 1px dashed var(--border-soft);
  border-radius: 8px;
  background: rgba(var(--nav-text-rgb), 0.025);
  color: var(--text-muted);
  font-size: 0.9rem;
}

@media (max-width: 980px) {
  .workspace-header {
    align-items: flex-start;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .input-panel :deep(.fasta-input) {
    flex: none;
    min-height: 240px;
    max-height: none;
  }

  .input-panel :deep(.seq-list),
  .input-panel :deep(.fasta-block) {
    flex: none;
  }
}

@media (max-width: 768px) {
  .sprinzl-page {
    padding: 0.75rem 0.75rem 1rem;
  }

  .sprinzl-shell {
    gap: 0.75rem;
  }

  .workspace-header {
    display: grid;
    gap: 0.75rem;
    padding: 0;
  }

  .title {
    font-size: 1.65rem;
  }

  .run-state {
    justify-self: start;
  }

  .input-panel,
  .setup-panel,
  .status-panel {
    padding: 0.8rem;
  }

  .panel-header {
    margin-bottom: 0.7rem;
  }

  .mode-segment {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mode-chip {
    min-height: 42px;
    padding: 0.4rem 0.25rem;
    font-size: 0.78rem;
  }

  .mode-note {
    min-height: 0;
  }

  .input-panel :deep(.fasta-input) {
    min-height: 220px;
  }

  .field-input {
    min-height: 44px;
    font-size: 16px;
  }

  .btn-run {
    min-height: 48px;
    font-size: 1rem;
  }

  .request-query {
    grid-template-columns: 1fr;
  }

  .request-actions,
  .request-actions .btn-secondary,
  .request-email-row,
  .request-email-row .btn-secondary,
  .request-query .btn-secondary {
    width: 100%;
  }

  .progress-meta-top {
    align-items: flex-start;
  }

  .progress-summary {
    width: 100%;
  }

  .progress-seq {
    max-width: 100%;
  }
}

@media (max-width: 480px) {
  .sprinzl-page {
    padding: 0.6rem;
  }

  .title {
    font-size: 1.48rem;
  }

  .input-panel,
  .setup-panel,
  .status-panel {
    padding: 0.7rem;
  }

  .input-panel :deep(.fasta-input) {
    min-height: 190px;
  }

}
</style>
