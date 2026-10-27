<template>
  <div class="curation-shell">
    <!-- The batch picker and the queue counters are this view's header, so they
         take the whole width and stay pinned while the case below scrolls. -->
    <header class="curation-topbar">
      <section class="work-strip">
        <div class="queue-title">
          <AppSelect
            class="run-select"
            size="sm"
            :model-value="runId"
            :options="batchOptions"
            :disabled="runBatches.length <= 1"
            :title="runSelectTitle"
            aria-label="运行批次"
            @change="selectRun"
          />
          <span
            v-if="current && current.status !== 'needs_review'"
            class="status-pill"
            :title="decisionProvenanceTitle"
          >
            {{ statusBadgeText }}
          </span>
          <button
            v-if="!lookupOpen"
            type="button"
            class="icon-btn lookup-toggle"
            title="按 tRNA DB ID 或 seqname 载入样本"
            aria-label="按 tRNA DB ID 或 seqname 载入样本"
            @click="openLookup"
          >
            <svg class="search-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-4.3-4.3" />
            </svg>
          </button>
          <span v-else class="lookup-field" ref="lookupField">
            <svg class="search-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-4.3-4.3" />
            </svg>
            <input
              ref="lookupInput"
              v-model.trim="lookupQuery"
              class="lookup-input"
              type="search"
              placeholder="tRNA DB ID 或 seqname"
              title="输入后回车载入；Esc 收起"
              aria-label="按 tRNA DB ID 或 seqname 载入样本"
              :disabled="lookupBusy"
              @keydown.enter.prevent="openLookupCase"
              @keydown.esc.prevent="closeLookup()"
            />
          </span>
        </div>
        <div class="queue-side">
          <AppFilterChips
            class="queue-filter"
            :model-value="queueFilter"
            :options="queueFilterOptions"
            aria-label="复核队列筛选"
            @change="setQueueFilter"
          />
          <AppSelect
            class="language-select"
            size="sm"
            v-model="panelLanguage"
            :options="panelLanguageOptions"
            aria-label="记录与推导过程的显示语言"
            title="记录与推导过程的显示语言"
          />
        </div>
      </section>
    </header>

    <main class="curation-main">

      <div v-if="caseError" class="error-line">{{ caseError }}</div>
      <div v-if="lookupError" class="error-line">{{ lookupError }}</div>
      <div v-if="loading" class="muted-line">正在加载校准队列...</div>

      <div class="workbench-grid">
        <section class="review-pane">
          <div v-if="!current" class="empty-review">
            {{ emptyQueueText }}
          </div>
          <template v-else>
            <div v-if="saveMessage" class="success-line">{{ saveMessage }}</div>
            <div v-if="draftMessage" class="muted-line">{{ draftMessage }}</div>
            <div v-if="saveError" class="error-line">{{ saveError }}</div>
            <div v-if="isServerDraft" class="server-draft-note">
              <strong>结构方案草稿，尚未提交为金标准。</strong>
              <span>{{ serverDraftNote }}</span>
            </div>
            <div v-if="decisionChoice === 'manual_corrected' && validationSummary" class="error-line">
              {{ validationSummary }}
            </div>

            <section class="abc-section">
              <div class="abc-toolbar">
                <div class="section-title">A 现行金标准 · B 本次运行结果 · C 草稿</div>
              </div>

              <div class="abc-card-grid">
                <MiniSprinzlCard
                  title="A"
                  :subtitle="goldCardSubtitle"
                  :labels="current.gold.labels_by_residue"
                  :bases="current.gold.bases_by_residue"
                  :structure="current.gold.structure_by_residue || []"
                  :pair-links-enabled="pairLinksEnabled"
                  :pair-warning="pairLinksWarning"
                  choice-value="accepted_gold"
                  :selected="decisionChoice === 'accepted_gold'"
                  @select="decisionChoice = 'accepted_gold'"
                />
                <MiniSprinzlCard
                  title="B"
                  :subtitle="predictionCardSubtitle"
                  :labels="current.prediction.labels_by_residue"
                  :bases="current.prediction.bases_by_residue"
                  :structure="current.prediction.structure_by_residue || []"
                  :pair-links-enabled="pairLinksEnabled"
                  :pair-warning="pairLinksWarning"
                  choice-value="accepted_model"
                  :selected="decisionChoice === 'accepted_model'"
                  :disabled="!current.prediction.available"
                  @select="decisionChoice = 'accepted_model'"
                />
                <MiniSprinzlCard
                  title="C"
                  :subtitle="curatedSubtitle"
                  :labels="hasCuratedDraft ? curatedLabels : []"
                  :bases="hasCuratedDraft ? current.gold.bases_by_residue : []"
                  :structure="current.gold.structure_by_residue || []"
                  :pair-links-enabled="pairLinksEnabled"
                  :pair-warning="pairLinksWarning"
                  choice-value="manual_corrected"
                  :selected="decisionChoice === 'manual_corrected' && hasCuratedDraft"
                  :selected-label="selectedResidueLabel"
                  empty-text="在右侧 C 编辑器点 A→C 或 B→C"
                  @select="selectCuratedDraft"
                  @node-click="selectResidueByLabel"
                />
              </div>

              <!-- Navigation and the decision live under the cards, where the
                   reviewer has just compared A, B and C. -->
              <div class="abc-actions">
                <div class="abc-pager">
                  <!-- The sample moves with the pager: the queue position and the
                       sequence being reviewed belong to the same control. -->
                  <AppSelect
                    class="case-title-select"
                    size="sm"
                    v-model="selectedSeqname"
                    :options="caseOptions"
                    aria-label="复核样本"
                    @change="loadCase"
                  />
                  <button type="button" class="secondary-btn icon-btn" :disabled="currentIndex <= 0" title="上一个（← 或 [）" aria-label="上一个" aria-keyshortcuts="ArrowLeft [" @click="goPrev">
                    <span aria-hidden="true">‹</span>
                  </button>
                  <span class="abc-counter">
                    <template v-if="currentIndex >= 0">{{ currentIndex + 1 }} / {{ queueTotal }}</template>
                    <template v-else>队列外</template>
                  </span>
                  <button type="button" class="secondary-btn icon-btn" :disabled="currentIndex < 0 || currentIndex >= cases.length - 1" title="下一个（→ 或 ]）" aria-label="下一个" aria-keyshortcuts="ArrowRight ]" @click="goNext">
                    <span aria-hidden="true">›</span>
                  </button>
                </div>
                <div class="abc-decisions">
                  <button type="button" class="save-btn icon-btn" :disabled="saving || !canSubmitDecision" title="提交校准（Enter 或 Ctrl+Enter）" aria-label="提交校准" aria-keyshortcuts="Enter Control+Enter" @click="submitDecision">
                    <span aria-hidden="true">✓</span>
                  </button>
                  <button type="button" class="secondary-btn icon-btn" :disabled="saving" title="不确定" aria-label="不确定" @click="saveDecision('unsure')">
                    <span aria-hidden="true">?</span>
                  </button>
                  <!-- Whether the sequence is scored is a separate axis from the
                       answer itself, so it changes on its own without a decision. -->
                  <AppSelect
                    class="eligibility-select"
                    :model-value="eligibilityChoice"
                    :options="eligibilityOptions"
                    :disabled="savingEligibility"
                    size="sm"
                    aria-label="是否计入本轮评分"
                    @change="applyEligibility"
                  />
                </div>
              </div>
            </section>

            <section class="process-section">
              <div class="process-head">
                <span class="process-title">{{ panelCopy.agentProcess }}</span>
                <span class="process-summary">{{ processHeadline }}</span>
                <span v-if="caseProcess?.available" class="process-status" :class="processDispositionClass">{{ processDispositionText }}</span>
              </div>
              <div class="process-body">
                <div v-if="processLoading" class="muted-line">{{ panelCopy.loadingTrace }}</div>
                <div v-else-if="processError" class="error-line">{{ processError }}</div>
                <div v-else-if="caseProcess && !caseProcess.available" class="muted-line">{{ panelCopy.noTrace }}</div>
                <template v-else-if="caseProcess">
                  <div v-if="processBlockReasonChips.length || processWarningChips.length" class="process-chips">
                    <div v-if="processBlockReasonChips.length" class="process-chip-group">
                      <span class="process-chips-label">{{ panelCopy.guardReasons }}</span>
                      <span v-for="chip in processBlockReasonChips" :key="`fail-${chip}`" class="process-chip is-fail">{{ chip }}</span>
                    </div>
                    <div v-if="processWarningChips.length" class="process-chip-group">
                      <span class="process-chips-label">{{ panelCopy.warnings }}</span>
                      <span v-for="chip in processWarningChips" :key="`warn-${chip}`" class="process-chip is-warn">{{ chip }}</span>
                    </div>
                  </div>
                  <!-- Two columns, as on the numbering page: the recorded steps
                       on the left, and everything that reads from the selected
                       step on the right. -->
                  <div class="process-columns">
                  <ol class="process-timeline">
                    <li
                      v-for="step in processTimeline"
                      :key="step.position"
                      class="process-step"
                      :class="[`is-${step.state}`, { 'is-selected': step.position === selectedProcessPosition }]"
                    >
                      <button
                        type="button"
                        class="process-step-button"
                        :class="{ 'is-selected': step.position === selectedProcessPosition }"
                        @click="selectProcessStep(step.position)"
                      >
                        <span class="process-marker" :class="`is-${step.state}`" aria-hidden="true"></span>
                        <span class="process-step-copy">
                          <strong>{{ step.position }}. {{ step.stage }}</strong>
                          <span>{{ step.detail }}</span>
                        </span>
                      </button>
                    </li>
                  </ol>

                  <div class="process-side">
                    <div class="process-detail">
                      <div v-if="selectedProcessStep" class="process-detail-head">
                        <strong>{{ selectedProcessStep.stage }}</strong>
                        <span class="process-step-position">{{ panelCopy.stepOf(selectedProcessPosition, processTimeline.length) }}</span>
                      </div>
                      <template v-if="isInterpretationStep">
                        <div v-if="interpretationLoading" class="interpretation-writing">
                          <span class="interpretation-loader" aria-hidden="true"></span>
                          <span>{{ panelCopy.interpretationLoading }}</span>
                        </div>
                        <div v-else-if="interpretationError" class="error-line">{{ interpretationError }}</div>
                        <template v-else-if="interpretationText">
                          <p class="interpretation-text">{{ interpretationText }}</p>
                          <ol v-if="interpretationCitations.length" class="reference-list">
                            <li
                              v-for="citation in interpretationCitations"
                              :key="`reference-${citation.citation_id}`"
                            >
                              <span class="reference-marker">{{ citation.citation_id }}</span>
                              <a
                                v-if="citation.url"
                                :href="citation.url"
                                target="_blank"
                                rel="noopener noreferrer"
                                >{{ citation.title }}</a
                              >
                              <span v-else>{{ citation.title }}</span>
                              <small>{{ referenceSummary(citation) }}</small>
                            </li>
                          </ol>
                          <p v-if="interpretationCitations.length" class="reference-note">
                            {{ panelCopy.referencesNote }}
                          </p>
                        </template>
                        <div v-else class="muted-line">{{ panelCopy.interpretationIdle }}</div>
                      </template>
                      <dl v-else-if="selectedProcessFacts.length" class="process-facts">
                        <div v-for="fact in selectedProcessFacts" :key="fact.label">
                          <dt>{{ fact.label }}</dt>
                          <dd :title="fact.raw">{{ fact.value }}</dd>
                        </div>
                      </dl>
                      <div v-else class="muted-line">{{ panelCopy.noStepDetail }}</div>
                    </div>
                    <details v-if="technicalRows.length" class="technical-details">
                      <summary>{{ panelCopy.technicalProvenance }}</summary>
                      <div class="technical-grid">
                        <div v-for="row in technicalRows" :key="row.label" class="technical-group">
                          <span class="technical-label">{{ row.label }}</span>
                          <code :title="row.title">{{ row.value }}</code>
                        </div>
                      </div>
                    </details>
                  </div>
                  </div>

                  <div v-if="caseProcess.actions.length" class="process-actions">
                    <span class="process-subhead">{{ panelCopy.actions }}</span>
                    <table class="process-action-table">
                      <thead>
                        <tr>
                          <th>{{ panelCopy.action }}</th>
                          <th>{{ panelCopy.accepted }}</th>
                          <th>{{ panelCopy.validBefore }}</th>
                          <th>{{ panelCopy.validAfter }}</th>
                          <th>{{ panelCopy.targetRegions }}</th>
                          <th>{{ panelCopy.result }}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(action, position) in caseProcess.actions" :key="`process-action-${position}`">
                          <td>{{ toolLabel(action.action_type, processLanguage) }}</td>
                          <td>{{ action.accepted ? panelCopy.yes : panelCopy.no }}</td>
                          <td>{{ action.valid_before ? panelCopy.yes : panelCopy.no }}</td>
                          <td>{{ action.valid_after ? panelCopy.yes : panelCopy.no }}</td>
                          <td>{{ (action.target_regions || []).map(compactRegion).join("、") || "-" }}</td>
                          <td :title="(action.notes || []).join('; ')">{{ (action.notes || []).map((note) => codeLabel(note, processLanguage)).join(" · ") || "-" }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div class="process-cost">
                    <span>{{ panelCopy.promptTokens }} {{ formatTokens(caseProcess.tokens.prompt) }}</span>
                    <span>{{ panelCopy.completionTokens }} {{ formatTokens(caseProcess.tokens.completion) }}</span>
                    <span>{{ panelCopy.stopCondition }} {{ codeLabel(caseProcess.stop_reason, processLanguage) }}</span>
                  </div>
                </template>
              </div>
            </section>

            <details v-if="hasCuratedDraft" class="diff-section">
              <summary>逐残基差异（{{ residueRows.length }} 个）</summary>
              <div class="diff-table-wrap">
                <table class="diff-table">
                  <thead>
                    <tr>
                      <th>残基序号</th>
                      <th>碱基</th>
                      <th>A 编号</th>
                      <th>B 编号</th>
                      <th>C 编号</th>
                      <th>区域</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in residueRows"
                      :key="row.index"
                      :class="{ changed: row.goldLabel !== row.predLabel }"
                    >
                      <td>{{ row.index }}</td>
                      <td>{{ row.base }}</td>
                      <td>{{ row.goldLabel || "-" }}</td>
                      <td>{{ row.predLabel || "-" }}</td>
                      <td>
                        <input
                          v-model.trim="curatedLabels[row.index - 1]"
                          class="label-input"
                          :class="{
                            edited: curatedLabels[row.index - 1] !== row.goldLabel,
                            invalid: Boolean(labelError(row.index)),
                          }"
                          :title="labelError(row.index)"
                          @input="curatedSource = 'manual'"
                        />
                        <div v-if="labelError(row.index)" class="field-error">
                          {{ labelError(row.index) }}
                        </div>
                      </td>
                      <td>{{ row.region || "-" }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </template>
        </section>
        <div class="side-column">
          <section
            v-if="current"
            class="record-info-bar"
            aria-label="记录信息"
          >
            <div class="record-info-heading">
              <strong>记录信息</strong>
              <span class="submission-state" :class="submissionStatusTone">{{ submissionStatus }}</span>
              <span v-if="current.evidence?.high_risk === true" class="evidence-risk-state is-risk">需人工复核</span>
            </div>
            <dl class="record-info-grid">
              <div>
                <dt>输入长度</dt>
                <dd>{{ recordInfo.length }} nt</dd>
              </div>
              <div>
                <dt>人工矫正</dt>
                <dd>{{ manualCorrectionStatus }}</dd>
              </div>
              <div v-if="recordInfo.predictor">
                <dt>结构预测</dt>
                <dd>{{ recordInfo.predictor }}</dd>
              </div>
            </dl>
            <div v-if="recordSaveMessage" class="record-save-notice" role="status">
              {{ recordSaveMessage }}
            </div>
          </section>
          <aside
            class="c-editor-drawer"
            :class="{ 'is-empty': !current || decisionChoice !== 'manual_corrected' || !hasCuratedDraft }"
          >
            <div class="drawer-head">
              <strong>C 编辑器</strong>
              <span v-if="current && hasCuratedDraft">{{ editorRows.length }} 个残基</span>
            </div>
            <!-- The comparison numbers describe C, so they sit above the editor that
                 produces them. -->
            <div v-if="current" class="metric-strip is-drawer">
              <span class="metric-basis" :title="scoreBasisTitle">{{ scoreBasisLabel }}</span>
              <span class="metric-cell" :title="scoreTitle('label')">编号一致率 <b>{{ scoreText('label') }}</b></span>
              <span class="metric-cell" :title="scoreTitle('region')">区域一致率 <b>{{ scoreText('region') }}</b></span>
              <span class="metric-cell" :title="legalityTitle">格式与硬规则 <b>{{ legalityText }}</b></span>
              <span
                v-if="currentIndex < 0 || current.diff_regions.length"
                class="metric-cell metric-regions"
              >
                <span v-if="current.diff_regions.length" class="metric-regions-label">差异区域</span>
                <span
                  v-if="currentIndex < 0"
                  class="region-chip"
                  title="该样本不在当前复核队列：没有差异，或不属于本轮复核范围"
                >
                  队列外
                </span>
                <span
                  v-for="region in current.diff_regions"
                  :key="region"
                  class="region-chip"
                  :title="region"
                >
                  {{ compactRegion(region) }}
                </span>
              </span>
            </div>
            <!-- The only copy of these two actions: C is created here, next to the
                 rows it fills in, instead of also standing above the A/B/C cards. -->
            <div v-if="current" class="drawer-tools">
              <button type="button" class="primary-btn" title="复制 A 到 C（Shift+A 或 Alt+A）" aria-keyshortcuts="Shift+A Alt+A" @click="copyFromGold">A→C</button>
              <button
                type="button"
                class="primary-btn"
                title="复制 B 到 C（Shift+B 或 Alt+B）"
                aria-keyshortcuts="Shift+B Alt+B"
                @click="copyFromModel"
              >
                B→C
              </button>
            </div>
            <template v-if="current && decisionChoice === 'manual_corrected' && hasCuratedDraft">
              <div v-if="commonFixes.length" class="common-fix-bar">
                <button
                  v-for="fix in commonFixes"
                  :key="fix.id"
                  type="button"
                  class="common-fix-btn"
                  :title="commonFixTitle(fix)"
                  :aria-keyshortcuts="commonFixShortcut(fix) || undefined"
                  @click="applyCommonFix(fix.id)"
                >
                  {{ fix.label }}
                </button>
              </div>
              <div ref="drawerListRef" class="drawer-list">
                <label
                  v-for="row in editorRows"
                  :key="`drawer-${row.index}`"
                  class="drawer-row"
                  :class="{
                    active: selectedResidueIndex === row.index,
                    changed: row.goldLabel !== row.predLabel,
                    invalid: Boolean(labelError(row.index)),
                  }"
                  :data-residue-index="row.index"
                  @click="selectResidue(row.index, false)"
                >
                  <span class="drawer-index">#{{ row.index }}</span>
                  <span class="drawer-base">{{ row.base }}</span>
                  <span class="drawer-ab">A {{ row.goldLabel || "-" }}</span>
                  <span class="drawer-ab">B {{ row.predLabel || "-" }}</span>
                  <input
                    v-model.trim="curatedLabels[row.index - 1]"
                    class="label-input"
                    :class="{
                      edited: curatedLabels[row.index - 1] !== row.goldLabel,
                      invalid: Boolean(labelError(row.index)),
                    }"
                    :title="labelError(row.index)"
                    @input="curatedSource = 'manual'"
                  />
                  <div class="drawer-suggestions">
                    <button
                      v-for="label in labelSuggestions(row)"
                      :key="`${row.index}-${label}`"
                      type="button"
                      class="suggestion-chip"
                      :class="{ chosen: curatedLabels[row.index - 1] === label }"
                      @click.stop="applyLabelSuggestion(row.index, label)"
                    >
                      {{ label }}
                    </button>
                  </div>
                  <span v-if="labelError(row.index)" class="drawer-error">{{ labelError(row.index) }}</span>
                </label>
              </div>
            </template>
            <div v-else class="editor-empty-state">
              <span class="editor-empty-mark" aria-hidden="true">C</span>
              <strong>尚未生成 C</strong>
              <p>点上面的 A→C 或 B→C 生成草稿，再逐个残基编辑。</p>
            </div>
          </aside>
        </div>
      </div>
    </main>
    <div class="toast-container">
      <Transition name="toast">
        <div v-if="toast.visible" class="toast" :class="`toast-${toast.type}`">
          <span class="toast-icon">{{ toast.type === 'success' ? '✓' : toast.type === 'error' ? '✗' : 'ℹ' }}</span>
          <span class="toast-message">{{ toast.message }}</span>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppSelect from "@/components/AppSelect.vue";
import AppFilterChips from "@/components/AppFilterChips.vue";
import {
  codeLabel,
  factLabel,
  formatFactValue,
  formatSeconds,
  formatTokens,
  toolLabel,
  type CaseProcess,
  type ProcessState,
} from "@/shared/agentProcess";
import {
  dispositionCopy,
  PANEL_COPY,
  processEventDetail,
  processStageLabel,
  type ProcessLanguage,
} from "@/shared/runProcessCopy";
import SupTrnaRadial from "@/components/supTrnaRadial.vue";
import { apiFetch } from "@/shared/api";

interface CaseItem {
  seqname: string;
  status: string;
  diff_count: number;
  diff_count_estimated?: boolean;
  diff_regions: string[];
  priority: number;
  label_accuracy?: number | null;
  region_accuracy?: number | null;
  legality_passed?: boolean | null;
  template_name?: string;
  prediction_available: boolean;
  input_len: number;
  broad_source?: string;
  evaluation_allowed?: boolean;
}

// "include" plus the reason codes the answer table accepts for an exclusion.
type EligibilityChoice =
  | "include"
  | "non_canonical_trna"
  | "terminal_cca_undetermined"
  | "variable_loop_undetermined"
  | "incomplete_sequence";

interface CaseBundle {
  seqname: string;
  run_id: string;
  review_scope?: string;
  review_round?: string;
  numbering: string[];
  input_seq: string;
  gold: AlignmentSide;
  raw?: AlignmentSide;
  prediction: AlignmentSide & {
    available: boolean;
    status?: string;
    template_name?: string;
    error?: string;
    warnings?: string[];
    metrics?: Record<string, unknown>;
  };
  diffs: DiffRow[];
  diff_count: number;
  diff_regions: string[];
  status: string;
  priority: number;
  active_gold?: {
    source?: string;
    decided_at?: string | null;
    decision_hash?: string | null;
    decision_run_id?: string | null;
    evaluation_allowed?: boolean;
    exclusion_reason?: string | null;
    excluded_at?: string | null;
    labels_by_residue?: string[];
    bases_by_residue?: string[];
  };
  correction?: { notes?: string; exclusion_reason?: string; curated_labels?: string[]; status?: string; decision?: string; curated_at?: string } | null;
  historical_correction?: { notes?: string; exclusion_reason?: string; curated_labels?: string[]; status?: string; decision?: string; curated_at?: string } | null;
  curated_labels?: string[] | null;
  curated_origin?: "" | "server_draft";
  draft?: { notes?: string; curated_labels?: string[]; drafted_at?: string; status?: string } | null;
  evidence?: EvidenceSummary;
}

interface EvidenceSummary {
  high_risk?: boolean;
  risk_codes?: string[];
  structure?: {
    available?: boolean;
    pair_links_allowed?: boolean;
    status?: string;
    predictor?: string;
    trnascan_isotype?: string;
    trnascan_anticodon?: string;
  };
  numbering?: {
    average_posterior?: number | null;
    bit_score?: number | null;
    route_confidence?: number | null;
    calibration_status?: string;
    calibration_disposition?: string;
    calibration_reason?: string;
  };
}

interface AlignmentSide {
  labels_by_residue: string[];
  bases_by_residue: string[];
  structure_by_residue?: string[];
  structure_source?: string;
  structure_meta?: StructureMeta;
  csv_content?: string;
}

interface StructureMeta {
  predictor?: string;
  trnascan_isotype?: string;
  trnascan_anticodon?: string;
  trnascan_anticodon_start?: number | string;
  trnascan_anticodon_end?: number | string;
  trnadb?: {
    trnadb_id?: string;
    sprinzl_id?: string;
    organism?: string;
    kingdom?: string;
    kingdom_label?: string;
    amino_acid?: string;
    anticodon?: string;
    embl_code?: string;
  };
}

interface DiffRow {
  residue_index: number;
  gold_base: string;
  pred_base: string;
  gold_label: string;
  pred_label: string;
  region: string;
}

interface SummaryPayload {
  run_id: string;
  corrections_path: string;
  gold_path?: string;
  gold_table_version?: {
    rows: number;
    changed_at: string | null;
    checksum: string;
    decided: number;
    excluded: number;
  } | null;
  run_path?: string;
  review_scope?: string;
  review_round?: string;
  counts: Record<string, number>;
  groups?: Record<string, number>;
}

interface RunBatch {
  run_id: string;
  label: string;
  role: string;
  state: string;
  model_id: string;
  cases: number;
  case_total: number;
  gold_sha256: string;
  answers_revised?: number;
  is_default: boolean;
}

interface ResidueDisplayRow {
  index: number;
  base: string;
  goldLabel: string;
  predLabel: string;
  region: string;
}

interface PairLink {
  left_id: string;
  right_id: string;
  left_index: number;
  right_index: number;
}

interface CommonFix {
  id: string;
  label: string;
  description: string;
}

function buildPairLinks(labels: string[], structure: string[] | undefined): PairLink[] {
  const stack: Array<{ id: string; index: number }> = [];
  const links: PairLink[] = [];
  (structure || []).forEach((token, idx) => {
    const id = String(labels[idx] || "").trim();
    if (!id) return;
    if (token === ">") {
      stack.push({ id, index: idx + 1 });
    } else if (token === "<") {
      const left = stack.pop();
      if (left && left.id !== id) {
        links.push({
          left_id: left.id,
          right_id: id,
          left_index: left.index,
          right_index: idx + 1,
        });
      }
    }
  });
  return links;
}

const MiniSprinzlCard = defineComponent({
  name: "MiniSprinzlCard",
  props: {
    title: { type: String, required: true },
    subtitle: { type: String, required: true },
    labels: { type: Array as () => string[], required: true },
    bases: { type: Array as () => string[], required: true },
    structure: { type: Array as () => string[], default: () => [] },
    pairLinksEnabled: { type: Boolean, default: true },
    pairWarning: { type: String, default: "" },
    choiceValue: { type: String, required: true },
    selected: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
    selectedLabel: { type: String, default: "" },
    emptyText: { type: String, default: "没有可用编号结果" },
  },
  emits: ["select", "node-click"],
  setup(props, { emit }) {
    const nodes = computed(() =>
      props.labels.map((label, idx) => {
        const base = props.bases[idx] || "-";
        return {
          id: String(label || `?${idx + 1}`),
          base,
          sup_base: base,
          structure: props.structure[idx] || "",
        };
      })
    );
    const pairLinks = computed(() => props.pairLinksEnabled ? buildPairLinks(props.labels, props.structure) : []);
    return () =>
      h("button", {
        type: "button",
        class: ["abc-card", { selected: props.selected, disabled: props.disabled }],
        disabled: props.disabled,
        onClick: () => {
          if (!props.disabled) emit("select");
        },
      }, [
        h("div", { class: "abc-card-text" }, [
          h("label", { class: "abc-choice", onClick: (event: Event) => event.stopPropagation() }, [
            h("input", {
              type: "radio",
              name: "gold-curation-decision",
              value: props.choiceValue,
              checked: props.selected,
              disabled: props.disabled,
              onChange: () => {
                if (!props.disabled) emit("select");
              },
            }),
            h("strong", props.title),
          ]),
          h("span", props.subtitle),
        ]),
        props.labels.length
          ? h("div", { class: "abc-card-visual" }, [
              h(SupTrnaRadial, {
                class: "abc-card-radial",
                data: nodes.value,
                pairLinks: pairLinks.value,
                colorMode: "targetOnly",
                width: 360,
                height: 300,
                r: 10,
                editable: props.choiceValue === "manual_corrected",
                selectedId: props.selectedLabel,
                onNodeClick: (id: string) => {
                  emit("select");
                  emit("node-click", id);
                },
              }),
              props.selected && props.choiceValue === "manual_corrected"
                ? h("span", { class: "edit-badge" }, "右侧编辑 C")
                : null,
              props.pairWarning ? h("span", { class: "pair-warning" }, props.pairWarning) : null,
            ])
          : h("div", { class: "abc-empty" }, props.emptyText),
      ]);
  },
});

type QueueFilter = "reviewable" | "needs_review" | "completed" | "unsure" | "excluded";
type ReviewScope = "classic_three_domain_mismatch" | "all_evaluation_mismatch";
type InterpretationLanguage = "en" | "zh-CN" | "de" | "fr";

const QUEUE_FILTERS: QueueFilter[] = ["reviewable", "needs_review", "completed", "unsure", "excluded"];
const COMPLETED_STATUSES: string[] = ["accepted_gold", "accepted_model", "manual_corrected"];

// The manuscript cohort is the sealed 2,530-sequence batch; older batches and
// flow controls stay reachable through the batch selector.
const DEFAULT_RUN_ID = "20261020-164533-deepseekv4-flash-guangdong";
const DEFAULT_DATASET_ID = "classic-2530";
const DEFAULT_CASE_ID = "tdbD00002457|DR1800";
const DEFAULT_REVIEW_SCOPE: ReviewScope = "all_evaluation_mismatch";
const DEFAULT_QUEUE_FILTER: QueueFilter = "needs_review";

function trnadbIdFromSeqname(seqname: string) {
  return String(seqname || "").split("|")[0] || "";
}

function runIdFromLocation(raw: string) {
  const clean = String(raw || "").trim();
  return clean || DEFAULT_RUN_ID;
}

function readLocationState() {
  const params = new URLSearchParams(window.location.search);
  const scope = params.get("scope");
  const filter = params.get("filter");
  const rawIndex = Number.parseInt(params.get("index") || "", 10);
  return {
    datasetId: params.get("dataset")?.trim() || DEFAULT_DATASET_ID,
    runId: runIdFromLocation(params.get("run")?.trim() || params.get("run_id")?.trim() || DEFAULT_RUN_ID),
    scope: scope === "classic_three_domain_mismatch" || scope === "all_evaluation_mismatch"
      ? scope
      : DEFAULT_REVIEW_SCOPE,
    filter: QUEUE_FILTERS.includes(filter as QueueFilter) ? (filter as QueueFilter) : DEFAULT_QUEUE_FILTER,
    seqname: params.get("trnadb")?.trim() || params.get("seqname")?.trim()
      || (params.has("index") ? "" : DEFAULT_CASE_ID),
    index: Number.isFinite(rawIndex) && rawIndex > 0 ? rawIndex : 0,
  };
}

const initialLocationState = readLocationState();
const datasetId = initialLocationState.datasetId;
const runId = ref(initialLocationState.runId);
const runBatches = ref<RunBatch[]>([]);
const reviewScope: ReviewScope = initialLocationState.scope;
let pendingLocationCase = initialLocationState.seqname;
let pendingLocationIndex = initialLocationState.index;
let restoringLocation = true;
const summary = ref<SummaryPayload | null>(null);
const cases = ref<CaseItem[]>([]);
const queueTotal = ref(0);
const current = ref<CaseBundle | null>(null);
const selectedSeqname = ref("");
const queueFilter = ref<QueueFilter>(initialLocationState.filter);
const lookupQuery = ref("");
const lookupBusy = ref(false);
const lookupError = ref("");
const lookupOpen = ref(false);
const lookupInput = ref<HTMLInputElement | null>(null);
const lookupField = ref<HTMLElement | null>(null);
const caseProcess = ref<CaseProcess | null>(null);
// The written interpretation is not part of the stored trace: it is produced on
// demand from that trace, so the page holds it next to the step list.
interface CaseCitation {
  citation_id: string;
  source: string;
  title: string;
  authors?: string[];
  year?: string;
  venue?: string;
  page?: number;
  url?: string;
}

interface CaseInterpretation {
  interpretation?: { sequence_assessment?: { text?: string } };
  citations?: CaseCitation[];
}
const caseInterpretation = ref<CaseInterpretation | null>(null);
const interpretationLoading = ref(false);
const interpretationError = ref("");
let interpretationSerial = 0;
let interpretationController: AbortController | null = null;
const processLoading = ref(false);
const processError = ref("");
const selectedProcessPosition = ref(1);
let processRequestId = 0;
const caseCache = new Map<string, CaseBundle>();
const curatedLabels = ref<string[]>([]);
const curatedSource = ref<"none" | "A" | "B" | "manual">("none");
const decisionChoice = ref<"accepted_gold" | "accepted_model" | "manual_corrected">("accepted_gold");
const selectedResidueIndex = ref<number | null>(null);
const drawerListRef = ref<HTMLElement | null>(null);
const actionMessage = ref("");
const recordSaveMessages = ref<Record<string, string>>({});
const loading = ref(false);
const saving = ref(false);
const caseError = ref("");
const saveError = ref("");
const saveMessage = ref("");
const draftMessage = ref("");
const panelLanguageOptions: Array<{ value: InterpretationLanguage; label: string }> = [
  { value: "en", label: "English" },
  { value: "zh-CN", label: "中文" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
];
const PANEL_LANGUAGE_STORAGE_KEY = "sprinzlmap:gold-curation:interpretation-language";
const panelLanguage = ref<InterpretationLanguage>(readPanelLanguage());
const recordSaveMessage = computed(() => {
  const seqname = current.value?.seqname || "";
  return seqname ? recordSaveMessages.value[seqname] || "" : "";
});

// Toast notification state
interface ToastState {
  visible: boolean;
  message: string;
  type: 'success' | 'error' | 'info';
}
const toast = ref<ToastState>({ visible: false, message: '', type: 'success' });
let toastTimer: ReturnType<typeof window.setTimeout> | null = null;

function showToast(message: string, type: 'success' | 'error' | 'info' = 'success', duration = 2000) {
  if (toastTimer) {
    clearTimeout(toastTimer);
    toastTimer = null;
  }
  toast.value = { visible: true, message, type };
  toastTimer = window.setTimeout(() => {
    toast.value.visible = false;
    toastTimer = null;
  }, duration);
}

let draftTimer: ReturnType<typeof window.setTimeout> | null = null;
let applyingBundle = false;
// Clearing the scratch panel after a submit must not write that blank state
// straight back to the server as a new draft.
let suppressDraftSave = false;

function readPanelLanguage(): InterpretationLanguage {
  try {
    const value = window.localStorage.getItem(PANEL_LANGUAGE_STORAGE_KEY);
    return value === "en" || value === "zh-CN" || value === "de" || value === "fr" ? value : "en";
  } catch {
    return "en";
  }
}

function syncLocationState() {
  const url = new URL(window.location.href);
  url.searchParams.set("dataset", datasetId);
  url.searchParams.set("run", runId.value);
  url.searchParams.set("filter", queueFilter.value);
  const seqname = current.value?.seqname || "";
  if (seqname) {
    url.searchParams.set("trnadb", trnadbIdFromSeqname(seqname));
  } else {
    url.searchParams.delete("trnadb");
  }
  for (const legacyKey of ["run_id", "scope", "seqname", "index"]) {
    url.searchParams.delete(legacyKey);
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function draftKey(seqname: string) {
  return `gold-curation-draft:${runId.value}:${reviewScope}:${seqname}`;
}

function writeLocalDraft() {
  if (!current.value) return;
  const payload = {
    seqname: current.value.seqname,
    run_id: runId.value,
    status: decisionChoice.value,
    curated_labels: curatedLabels.value,
    saved_at: new Date().toISOString(),
  };
  try {
    window.localStorage.setItem(draftKey(current.value.seqname), JSON.stringify(payload));
    draftMessage.value = "草稿已暂存";
  } catch {
    draftMessage.value = "草稿暂存失败";
  }
}

function readLocalDraft(seqname: string) {
  try {
    const raw = window.localStorage.getItem(draftKey(seqname));
    return raw ? JSON.parse(raw) as { curated_labels?: string[]; notes?: string; status?: string; saved_at?: string } : null;
  } catch {
    return null;
  }
}

function clearLocalDraft(seqname: string) {
  try {
    window.localStorage.removeItem(draftKey(seqname));
  } catch {
    // best effort
  }
}

function scheduleDraftSave() {
  if (applyingBundle || suppressDraftSave || !current.value) return;
  writeLocalDraft();
  if (draftTimer) window.clearTimeout(draftTimer);
  const seqname = current.value.seqname;
  draftTimer = window.setTimeout(() => {
    const params = new URLSearchParams({ run_id: runId.value, scope: reviewScope });
    void apiFetch(
      `/gold-curation/cases/${encodeURIComponent(seqname)}/draft?${params.toString()}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: decisionChoice.value,
          curated_labels: curatedLabels.value,
        }),
      }
    ).then((resp) => {
      if (resp.ok) draftMessage.value = "草稿已暂存到服务器";
    }).catch(() => {});
  }, 500);
}

// Only an unsubmitted draft fills C.  A committed decision already lives in A,
// so re-opening a decided case shows the answer, not a copy of it in the draft.
function savedCuratedLabels(bundle: CaseBundle) {
  const labels = bundle.curated_labels;
  if (Array.isArray(labels) && labels.length === bundle.gold.bases_by_residue.length) return [...labels];
  return [];
}

// Every chip is one queue the backend can serve directly, so the numbers come
// from its grouped counts instead of a client-side sum of decisions.
const queueGroups = computed<Record<string, number>>(() => {
  const served = summary.value?.groups;
  if (served && Object.keys(served).length) return served;
  const counts = summary.value?.counts || {};
  const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  return {
    all: total,
    reviewable: total - Number(counts.auto_agree || 0),
    needs_review: Number(counts.needs_review || 0),
    completed: COMPLETED_STATUSES.reduce((sum, key) => sum + Number(counts[key] || 0), 0),
    unsure: Number(counts.unsure || 0),
    excluded: Number(counts.excluded || 0),
  };
});

const queueFilterOptions = computed(
  () =>
    [
      { value: "reviewable", label: "全部与金标准存在差异的序列", count: queueGroups.value.reviewable ?? 0, title: "现行金标准与该批次模型结果不一致的样本，含已经确认过的" },
      { value: "needs_review", label: "待确认金标准", count: queueGroups.value.needs_review ?? 0, title: "尚未提交金标准结论的样本" },
      { value: "completed", label: "已确认金标准", count: queueGroups.value.completed ?? 0, title: "沿用现行金标准、采纳 B、保存 C 的总数" },
      { value: "unsure", label: "不确定", count: queueGroups.value.unsure ?? 0, title: "标记为不确定的样本" },
      { value: "excluded", label: "放弃", count: queueGroups.value.excluded ?? 0, title: "不计入本轮评分的样本，金标准答案保留" },
    ] satisfies Array<{ value: QueueFilter; label: string; count: number; title: string }>
);

function matchesQueueFilter(status: string) {
  if (queueFilter.value === "reviewable") return status !== "auto_agree";
  if (queueFilter.value === "completed") return COMPLETED_STATUSES.includes(status);
  return status === queueFilter.value;
}

const currentBatch = computed(
  () => runBatches.value.find((batch) => batch.run_id === runId.value) || null
);

// The stored batch that the page reports is listed first, then the remaining
// batches newest first, so a control run cannot be mistaken for the cohort.
const selectableBatches = computed(() => {
  const batches = [...runBatches.value];
  if (!batches.some((batch) => batch.run_id === runId.value)) {
    batches.unshift({
      run_id: runId.value,
      label: runId.value,
      role: "指定批次",
      state: "sealed",
      model_id: "",
      cases: 0,
      case_total: 0,
      gold_sha256: "",
      is_default: false,
    });
  }
  return batches.sort(
    (left, right) =>
      Number(right.is_default) - Number(left.is_default) ||
      right.run_id.localeCompare(left.run_id)
  );
});

const runSelectTitle = computed(() => {
  const batch = currentBatch.value;
  if (!batch) return `运行批次：${runId.value}`;
  const table = summary.value?.gold_table_version;
  return [
    `${batch.role} · ${batch.state}`,
    batch.model_id ? `模型 ${batch.model_id}` : "",
    `批次封存时的答案快照 ${batch.gold_sha256}`,
    table
      ? `现行答案表 ${table.rows} 条，已校准 ${table.decided} 条，排除 ${table.excluded} 条` +
        `${table.changed_at ? `，最近更改 ${table.changed_at}` : ""}`
      : "",
    // Scores are never stored with the batch: every number on screen is graded
    // against the current answer table, so the only thing worth naming is how
    // many answers moved after the batch finished.
    (batch.answers_revised ?? 0)
      ? `${batch.answers_revised} 条答案在批次结束后被重新校准，显示的数字已按现行答案重算`
      : "分数按现行答案实时计算",
    batch.run_id,
  ].filter(Boolean).join("\n");
});

const batchOptions = computed(() =>
  selectableBatches.value.map((batch) => ({
    value: batch.run_id,
    label: `${batch.label} · ${batch.role} · ${batch.cases}/${batch.case_total}${batch.state === "sealed" ? "" : " · 运行中"}`,
    title: batch.run_id,
  }))
);

// The sample list mirrors the queue order, with an out-of-queue entry kept on
// top so a looked-up sample is never dropped from the control.
const caseOptions = computed(() => {
  const options: Array<{ value: string; label: string; title: string }> = [];
  if (current.value && currentIndex.value < 0) {
    options.push({
      value: current.value.seqname,
      label: current.value.seqname,
      title: `${queueNote(current.value.seqname, current.value.diff_count, false, current.value.status)} · 队列外`,
    });
  }
  cases.value.forEach((item) => {
    options.push({
      value: item.seqname,
      label: item.seqname,
      title: queueNote(item.seqname, item.diff_count, item.diff_count_estimated === true, item.status),
    });
  });
  return options;
});

// The selector doubles as the page title, so its entries carry only the sequence
// name; the queue facts move into the tooltip.
function queueNote(seqname: string, diffCount: number, estimated: boolean, status: string) {
  const diffs = estimated ? "差异待核对" : `${diffCount} 处金标准/模型差异`;
  return `${seqname} · ${diffs} · ${statusLabel(status)}`;
}

function shortSeqname(seqname: string) {
  const parts = String(seqname || "").split("|");
  return parts[1] || parts[0] || seqname;
}

function pct(raw: unknown) {
  const value = Number(raw);
  if (!Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

// A single sequence is scored by residue count, so the strip shows matched over
// total; the percentage stays in the tooltip.
type ScoreKind = "label" | "region";

// Region membership mirrors the benchmark's number_to_region, so the reviewer
// sees the same definition while editing C that the score uses afterwards.
const REGION_BANDS: Array<[number, number, string]> = [
  [-1, 9, "Aminoacyl arm 5' end"],
  [10, 26, "D loop + D stem"],
  [27, 43, "Anticodon loop + Anticodon stem"],
  [44, 48, "Variable loop"],
  [49, 65, "T loop + T stem"],
  [66, 76, "Aminoacyl arm 3' end"],
];

function regionOfLabel(raw: unknown): string {
  let label = String(raw || "").trim();
  if (!label) return "";
  const insertion = label.match(/^(.+?)i\d+$/);
  if (insertion) label = insertion[1];
  if (/^-?\d+$/.test(label)) {
    const num = Number(label);
    for (const [low, high, region] of REGION_BANDS) {
      if (num >= low && num <= high) return region;
    }
    return "";
  }
  if (label === "17a" || label === "20a" || label === "20b") return "D loop + D stem";
  const variable = label.match(/^V(\d+)$/);
  if (variable) {
    const num = Number(variable[1]);
    if (num >= 1 && num <= 5) return "Variable loop";
    if (num >= 11 && num <= 17) return "Variable loop";
    if (num >= 21 && num <= 27) return "Variable loop";
  }
  return "";
}

// The strip follows the numbering the reviewer is working on: C while a draft
// covers every residue, otherwise the stored model result B.
const draftScore = computed(() => {
  const gold = current.value?.gold.labels_by_residue || [];
  const draft = curatedLabels.value;
  if (!gold.length || draft.length !== gold.length) return null;
  let labelCorrect = 0;
  let regionCorrect = 0;
  gold.forEach((goldLabel, index) => {
    const label = String(draft[index] || "").trim();
    const reference = String(goldLabel || "").trim();
    if (label === reference) labelCorrect += 1;
    if (regionOfLabel(label) === regionOfLabel(reference)) regionCorrect += 1;
  });
  return { labelCorrect, regionCorrect, total: gold.length };
});

const scoreBasis = computed<"C" | "B">(() => (draftScore.value ? "C" : "B"));

const scoreBasisLabel = computed(() => `${scoreBasis.value}–A`);

const scoreBasisTitle = computed(() =>
  scoreBasis.value === "C"
    ? "正在编辑 C：以下数值实时比较 C 草稿与现行金标准 A。"
    : "尚无 C 草稿：以下数值比较本次运行结果 B 与现行金标准 A。"
);

const legalityText = computed(() => {
  if (draftScore.value) return labelErrors.value.length ? "未通过" : "通过";
  return current.value?.prediction.metrics?.legality_passed === true ? "通过" : "未通过";
});

const legalityTitle = computed(() => {
  if (draftScore.value) {
    const head = labelErrors.value.length
      ? "C 草稿存在编号问题：编号需非空、格式合法、不重复且顺序正确。"
      : "C 草稿的编号非空、格式合法、不重复且顺序正确。";
    return `${head}提交时服务端还会按硬性结构规则复核。`;
  }
  return "只检查序列保持、编号格式、顺序和硬性结构规则，不代表坐标与参考一致。";
});

function scoreParts(kind: ScoreKind) {
  const draft = draftScore.value;
  if (draft) {
    const correct = kind === "label" ? draft.labelCorrect : draft.regionCorrect;
    return { correct, total: draft.total, accuracy: correct / draft.total };
  }
  const metrics = (current.value?.prediction.metrics || {}) as Record<string, unknown>;
  const correct = Number(metrics[`${kind}_correct`]);
  const total = Number(metrics[`${kind}_total`]);
  if (!Number.isFinite(correct) || !Number.isFinite(total) || total <= 0) return null;
  const accuracy = Number(metrics[`${kind}_accuracy`]);
  return { correct, total, accuracy: Number.isFinite(accuracy) ? accuracy : correct / total };
}

function scoreText(kind: ScoreKind) {
  const parts = scoreParts(kind);
  if (parts) return `${parts.correct}/${parts.total}`;
  return pct((current.value?.prediction.metrics || {})[`${kind}_accuracy`]);
}

function scoreTitle(kind: ScoreKind) {
  const what = kind === "label" ? "Sprinzl 编号" : "编号所属区域";
  const parts = scoreParts(kind);
  if (!parts) return `与现行金标准${what}一致的残基数`;
  return `${parts.correct} / ${parts.total} 个残基的${what}与现行金标准 A 一致（${(parts.accuracy * 100).toFixed(1)}%）`;
}

const pairLinksEnabled = computed(() => current.value?.evidence?.structure?.pair_links_allowed === true);

const pairLinksWarning = computed(() => pairLinksEnabled.value ? "" : "没有可用的二级结构预测，配对连线已隐藏");

async function readError(resp: Response) {
  try {
    const data = await resp.json();
    return data?.message ? String(data.message) : JSON.stringify(data);
  } catch {
    return `HTTP ${resp.status}`;
  }
}

function exactLookupMatch(seqname: string, query: string) {
  const lower = query.trim().toLowerCase();
  if (!lower) return false;
  return (
    seqname.toLowerCase() === lower ||
    shortSeqname(seqname).toLowerCase() === lower ||
    trnadbIdFromSeqname(seqname).toLowerCase() === lower
  );
}

/**
 * Resolve a user-supplied identifier to a full seqname.  The current review
 * queue is searched first, then the full run, so samples outside the queue
 * (auto-agree records, other review rounds) stay reachable.
 */
async function resolveSeqname(query: string): Promise<string> {
  const raw = query.trim();
  if (!raw) return "";
  const inQueue =
    cases.value.find((item) => exactLookupMatch(item.seqname, raw)) ||
    cases.value.find((item) => item.seqname.toLowerCase().includes(raw.toLowerCase()));
  if (inQueue) return inQueue.seqname;
  const params = new URLSearchParams({
    run_id: runId.value,
    status: "all",
    scope: "",
    q: raw,
    limit: "50",
  });
  const resp = await apiFetch(`/gold-curation/cases?${params.toString()}`);
  if (!resp.ok) throw new Error(await readError(resp));
  const data = (await resp.json()) as { items?: Array<{ seqname: string }> };
  const items = data.items || [];
  const exact = items.find((item) => exactLookupMatch(item.seqname, raw));
  return (exact || items[0])?.seqname || "";
}

async function openLookupCase() {
  const raw = lookupQuery.value.trim();
  lookupError.value = "";
  if (!raw || lookupBusy.value) return;
  lookupBusy.value = true;
  try {
    const seqname = await resolveSeqname(raw);
    if (!seqname) {
      lookupError.value = `没有匹配的样本：${raw}`;
      return;
    }
    await loadCase(seqname);
    // A hit closes the field; a miss keeps it open so the query can be fixed.
    closeLookup();
  } catch (err) {
    lookupError.value = err instanceof Error ? err.message : String(err);
  } finally {
    lookupBusy.value = false;
  }
}

function openLookup() {
  lookupOpen.value = true;
  void nextTick(() => {
    lookupInput.value?.focus();
    lookupInput.value?.select();
  });
}

function closeLookup() {
  lookupQuery.value = "";
  lookupError.value = "";
  lookupOpen.value = false;
}

// The field only exists while it holds focus, so clicking anywhere else folds
// it back to the icon instead of leaving an empty query on the toolbar.
function handleLookupOutsidePointer(event: PointerEvent) {
  if (!lookupOpen.value) return;
  const field = lookupField.value;
  if (!field || field.contains(event.target as Node)) return;
  closeLookup();
}

watch(lookupOpen, (open) => {
  if (open) {
    window.addEventListener("pointerdown", handleLookupOutsidePointer, true);
    return;
  }
  window.removeEventListener("pointerdown", handleLookupOutsidePointer, true);
});

// Function one of this page: explain how a stored numbering was produced.
// Everything below is read back from the run's own trace, so opening it never
// repeats a model call.  The wording is shared with the numbering page and
// follows the language already chosen here.
const processLanguage = computed<ProcessLanguage>(() => panelLanguage.value);
const panelCopy = computed(() => PANEL_COPY[processLanguage.value]);

// A stored step keeps its observations nested, while the shared wording reads a
// flat trace event, so each step is flattened before it is described.
const interpretationPosition = computed(() => (caseProcess.value?.steps || []).length + 1);

const interpretationState = computed<ProcessState>(() => {
  if (interpretationError.value) return "failed";
  if (caseInterpretation.value) return "passed";
  return "info";
});

const interpretationDetail = computed(() => {
  if (interpretationLoading.value) return panelCopy.value.interpretationLoading;
  if (interpretationError.value) return panelCopy.value.interpretationFailed;
  if (caseInterpretation.value) return panelCopy.value.interpretationDone;
  return panelCopy.value.interpretationIdle;
});

const interpretationText = computed(() =>
  String(caseInterpretation.value?.interpretation?.sequence_assessment?.text || "").trim(),
);

const interpretationCitations = computed<CaseCitation[]>(
  () => caseInterpretation.value?.citations || [],
);

function formatAuthorName(name: string): string {
  // RIS and BibTeX write "Surname, F. M."; the review UI reads better as "Surname F.M."
  const parts = String(name || "").split(",");
  if (parts.length < 2) return String(name || "").trim();
  const initials = parts
    .slice(1)
    .join(" ")
    .split(/\s+/)
    .filter(Boolean)
    .map((token) => (token.replace(/[^A-Za-z]/g, "").charAt(0) || "").toUpperCase())
    .join("")
    .slice(0, 3);
  return initials ? `${parts[0].trim()} ${initials}` : parts[0].trim();
}

function referenceSummary(citation: CaseCitation): string {
  const authors = (citation.authors || []).map(formatAuthorName).filter(Boolean);
  const who = authors.length ? (authors.length > 1 ? `${authors[0]} et al.` : authors[0]) : "";
  const parts = [who, citation.year, String(citation.venue || "").trim()];
  if (citation.page) parts.push(`p.${citation.page}`);
  parts.push(
    citation.source === "local_pdf"
      ? panelCopy.value.referenceSourceLocal
      : panelCopy.value.referenceSourcePublic,
  );
  return parts.filter(Boolean).join(" · ");
}

const processTimeline = computed(() => {
  const steps = (caseProcess.value?.steps || []).map((step) => ({
    position: step.position,
    state: step.state,
    stage: processStageLabel(step.stage, processLanguage.value),
    detail: processEventDetail(
      { stage: step.stage, ...(step.facts || {}) } as Record<string, unknown>,
      processLanguage.value,
    ),
  }));
  if (!steps.length) return steps;
  // The last step is the write-up of everything above it, marked as generated
  // rather than recorded.
  return [
    ...steps,
    {
      position: interpretationPosition.value,
      state: interpretationState.value,
      stage: processStageLabel("scientific_interpretation", processLanguage.value),
      detail: interpretationDetail.value,
    },
  ];
});

const processDispositionText = computed(() =>
  dispositionCopy(caseProcess.value?.disposition || "", processLanguage.value),
);

const processDispositionClass = computed(() => {
  const disposition = String(caseProcess.value?.disposition || "").toLowerCase();
  if (disposition === "accepted") return "is-accepted";
  if (disposition === "investigate") return "is-investigate";
  if (disposition === "rejected" || disposition === "abstained") return "is-abstained";
  return "is-neutral";
});

const selectedProcessStep = computed(() =>
  processTimeline.value.find((step) => step.position === selectedProcessPosition.value) || null,
);

const isInterpretationStep = computed(
  () => Boolean(caseProcess.value?.steps.length) && selectedProcessPosition.value === interpretationPosition.value,
);

const selectedProcessFacts = computed(() => {
  if (isInterpretationStep.value) return [];
  const step = (caseProcess.value?.steps || []).find((item) => item.position === selectedProcessPosition.value);
  if (!step) return [];
  return Object.entries(step.facts || {}).map(([key, value]) => ({
    label: factLabel(key, processLanguage.value),
    value: formatFactValue(key, value, processLanguage.value),
    raw: typeof value === "object" ? JSON.stringify(value) : String(value ?? ""),
  }));
});

const processBlockReasonChips = computed(() => {
  const payload = caseProcess.value;
  if (!payload || payload.legality_passed !== false) return [];
  return (payload.legality_reasons || []).map((code) => codeLabel(code, processLanguage.value));
});

const processWarningChips = computed(() => {
  const payload = caseProcess.value;
  if (!payload) return [];
  return (payload.warnings || []).map((code) => codeLabel(code, processLanguage.value));
});

const processHeadline = computed(() => {
  const payload = caseProcess.value;
  const copy = panelCopy.value;
  if (!payload) return processLoading.value ? copy.loadingTrace : copy.summaryFallback;
  if (!payload.available) return copy.noTrace;
  return [
    `${copy.planning} ${payload.counts.planner_decisions ?? "-"}`,
    `${copy.cmCalls} ${payload.counts.cm_tool_calls ?? "-"}`,
    `${copy.actions} ${payload.counts.agent_actions ?? "-"}`,
    `${copy.llmCalls} ${payload.counts.llm_calls ?? "-"} / ${formatTokens(payload.tokens.total)}`,
    formatSeconds(payload.elapsed_sec, processLanguage.value),
  ].join(" · ");
});

// What the run actually rested on: model, template and the guard observations a
// reviewer would otherwise have to open a file to see.
const technicalRows = computed(() => {
  const payload = caseProcess.value;
  if (!payload) return [];
  const copy = panelCopy.value;
  const language = processLanguage.value;
  const provenance = payload.provenance || {};
  const calibration = (provenance.calibration || {}) as Record<string, unknown>;
  const rows: Array<{ label: string; value: string; title: string }> = [];
  const probabilities = calibration.probabilities as Record<string, number> | undefined;
  if (probabilities && Object.keys(probabilities).length) {
    rows.push({
      label: copy.domainProbabilities,
      value: Object.entries(probabilities)
        .map(([group, value]) => `${group} ${Number(value).toFixed(3)}`)
        .join(" · "),
      title: JSON.stringify(probabilities),
    });
  }
  const calibrationDecision = String(calibration.disposition || calibration.status || "");
  if (calibrationDecision) {
    rows.push({
      label: copy.calibrationDecision,
      value: codeLabel(calibrationDecision, language),
      title: calibrationDecision,
    });
  }
  if (provenance.numbering_backend) {
    rows.push({
      label: copy.numberingBackend,
      value: formatFactValue("numbering_backend", provenance.numbering_backend, language),
      title: String(provenance.numbering_backend),
    });
  }
  if (payload.template_name) {
    rows.push({ label: copy.template, value: payload.template_name, title: payload.template_name });
  }
  if (payload.model) {
    rows.push({
      label: copy.model,
      value: payload.provider ? `${payload.model} · ${payload.provider}` : payload.model,
      title: payload.model,
    });
  }
  if (payload.planner_policy) {
    rows.push({
      label: copy.plannerPolicy,
      value: codeLabel(payload.planner_policy, language),
      title: payload.planner_policy,
    });
  }
  if ((payload.planner_sources || []).length) {
    rows.push({
      label: copy.plannerSources,
      value: payload.planner_sources.map((source) => codeLabel(source, language)).join(" · "),
      title: payload.planner_sources.join(", "),
    });
  }
  const guard = payload.guard || {};
  if (guard.challenge_count !== undefined) {
    rows.push({
      label: copy.guardAudit,
      value: [
        `${copy.guardChallenges} ${String(guard.challenge_count)}`,
        `${copy.guardRejected} ${String(guard.rejected_count ?? "-")}`,
        `${copy.guardEscaped} ${String(guard.escaped_count ?? "-")}`,
        `${copy.guardBaseline} ${guard.baseline_valid ? copy.yes : copy.no}`,
      ].join(" · "),
      title: JSON.stringify(guard),
    });
  }
  return rows;
});

async function loadCaseProcess(seqname: string) {
  const requestId = ++processRequestId;
  processLoading.value = true;
  processError.value = "";
  try {
    const params = new URLSearchParams({ run_id: runId.value });
    const resp = await apiFetch(`/gold-curation/cases/${encodeURIComponent(seqname)}/process?${params.toString()}`);
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as CaseProcess;
    if (requestId !== processRequestId) return;
    caseProcess.value = data;
    if (!data.steps.some((step) => step.position === selectedProcessPosition.value)) {
      selectedProcessPosition.value = data.steps[0]?.position ?? 1;
    }
  } catch (err) {
    if (requestId !== processRequestId) return;
    processError.value = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestId === processRequestId) processLoading.value = false;
  }
}

function resetInterpretation() {
  interpretationSerial += 1;
  interpretationController?.abort();
  interpretationController = null;
  interpretationLoading.value = false;
  interpretationError.value = "";
  caseInterpretation.value = null;
}

async function generateCaseInterpretation() {
  const seqname = current.value?.seqname;
  if (!seqname || interpretationLoading.value) return;
  interpretationController?.abort();
  const requestSerial = ++interpretationSerial;
  const controller = new AbortController();
  interpretationController = controller;
  interpretationLoading.value = true;
  interpretationError.value = "";
  try {
    const params = new URLSearchParams({
      run_id: runId.value,
      scope: reviewScope,
      language: processLanguage.value,
    });
    const response = await apiFetch(
      `/gold-curation/cases/${encodeURIComponent(seqname)}/interpretation?${params.toString()}`,
      { method: "POST", signal: controller.signal },
    );
    if (!response.ok) throw new Error(await readError(response));
    const payload = (await response.json()) as CaseInterpretation;
    if (requestSerial === interpretationSerial && current.value?.seqname === seqname) {
      caseInterpretation.value = payload;
    }
  } catch (err) {
    if (controller.signal.aborted || requestSerial !== interpretationSerial) return;
    interpretationError.value = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestSerial === interpretationSerial) {
      interpretationLoading.value = false;
      interpretationController = null;
    }
  }
}

function selectProcessStep(position: number) {
  selectedProcessPosition.value = position;
  // The write-up only runs when the reviewer asks for it, so browsing a queue
  // does not spend a model call on every sample.
  if (position === interpretationPosition.value) void generateCaseInterpretation();
}

// A newer filter or batch request owns the queue; responses from an older one
// are dropped so a slow reply cannot overwrite the list the reviewer sees.
let queueRequestId = 0;

async function loadCases() {
  const requestId = ++queueRequestId;
  loading.value = true;
  caseError.value = "";
  try {
    const params = new URLSearchParams({
      run_id: runId.value,
      status: queueFilter.value,
      scope: reviewScope,
      limit: "6000",
    });
    const resp = await apiFetch(`/gold-curation/cases?${params.toString()}`);
    if (!resp.ok) throw new Error(await readError(resp));
    if (requestId !== queueRequestId) return;
    const data = (await resp.json()) as {
      items: CaseItem[];
      total?: number;
      counts?: Record<string, number>;
      groups?: Record<string, number>;
      run_id?: string;
    };
    cases.value = data.items || [];
    queueTotal.value = Number(data.total ?? cases.value.length) || cases.value.length;
    if (data.counts) {
      summary.value = {
        run_id: data.run_id || runId.value,
        corrections_path: summary.value?.corrections_path || "",
        counts: data.counts,
        groups: data.groups || summary.value?.groups,
      };
    }
    const currentSeqname = current.value?.seqname || "";
    const pendingCase = restoringLocation ? pendingLocationCase : "";
    const restoredItem = restoringLocation
      ? cases.value.find((item) => item.seqname === pendingLocationCase || trnadbIdFromSeqname(item.seqname) === pendingLocationCase)
        || cases.value[pendingLocationIndex - 1]
      : null;
    restoringLocation = false;
    pendingLocationCase = "";
    pendingLocationIndex = 0;
    let targetSeqname = restoredItem?.seqname
      || (currentSeqname && cases.value.some((item) => item.seqname === currentSeqname) ? currentSeqname : "")
      || cases.value[0]?.seqname
      || "";
    if (!restoredItem && pendingCase) {
      const resolved = await resolveSeqname(pendingCase);
      if (resolved) {
        targetSeqname = resolved;
      } else {
        targetSeqname = "";
        lookupError.value = `没有匹配的样本：${pendingCase}`;
      }
    }
    if (targetSeqname) {
      await loadCase(targetSeqname);
    } else {
      current.value = null;
      selectedSeqname.value = "";
      syncLocationState();
    }
  } catch (err) {
    if (requestId === queueRequestId) {
      caseError.value = err instanceof Error ? err.message : String(err);
    }
  } finally {
    if (requestId === queueRequestId) loading.value = false;
  }
}

function setQueueFilter(next: QueueFilter) {
  if (queueFilter.value === next) return;
  queueFilter.value = next;
  syncLocationState();
  void reloadAll();
}

/** Fetch the batches registered in the run store so the page can open any of them. */
async function loadRunBatches() {
  try {
    const resp = await apiFetch("/gold-curation/runs");
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as { runs?: RunBatch[] };
    runBatches.value = data.runs || [];
  } catch {
    // A missing store only disables the selector; the opened batch still works.
    runBatches.value = [];
  }
}

/**
 * Switch to another benchmark batch. Curation decisions are stored per
 * sequence, so the submitted answers stay; only the B-side candidate that the
 * batch produced changes.
 */
async function selectRun(nextRunId: string) {
  const clean = String(nextRunId || "").trim();
  if (!clean || clean === runId.value) return;
  runId.value = clean;
  restoringLocation = false;
  pendingLocationCase = "";
  pendingLocationIndex = 0;
  lookupError.value = "";
  syncLocationState();
  await reloadAll();
}

function applyCaseBundle(bundle: CaseBundle, resetMessages = true) {
  applyingBundle = true;
  if (resetMessages) {
    saveError.value = "";
    saveMessage.value = "";
    actionMessage.value = "";
    draftMessage.value = "";
  }
  current.value = bundle;
  eligibilityChoice.value = eligibilityFromBundle(bundle);
  // The derivation belongs to the case, so a new sample drops the old trace
  // and immediately asks for its own steps.
  if (caseProcess.value?.seqname !== bundle.seqname || caseProcess.value?.run_id !== runId.value) {
    caseProcess.value = null;
    processError.value = "";
  }
  if (!caseProcess.value) void loadCaseProcess(bundle.seqname);
  resetInterpretation();
  selectedSeqname.value = bundle.seqname;
  selectedResidueIndex.value = null;
  const saved = savedCuratedLabels(bundle);
  curatedLabels.value = saved.length ? [...saved] : [];
  // A stays selected unless the reviewer has a draft waiting: nothing gets
  // pre-adopted, and Enter always confirms what panel A currently shows.
  const hasDraft =
    bundle.curated_origin === "server_draft"
    && curatedLabels.value.length === bundle.gold.bases_by_residue.length;
  decisionChoice.value = hasDraft ? "manual_corrected" : "accepted_gold";
  curatedSource.value = hasDraft ? "manual" : "none";
  if (bundle.curated_origin === "server_draft" && hasCuratedDraft.value) {
    draftMessage.value = "已加载服务器结构方案草稿，尚未提交。";
  }
  const draft = readLocalDraft(bundle.seqname);
  if (draft?.curated_labels?.length === bundle.gold.bases_by_residue.length) {
    curatedLabels.value = [...draft.curated_labels];
    decisionChoice.value =
      draft.status === "accepted_model" && bundle.prediction.available
        ? "accepted_model"
        : draft.status === "manual_corrected"
          ? "manual_corrected"
          : decisionChoice.value;
    curatedSource.value = decisionChoice.value === "manual_corrected" ? "manual" : curatedSource.value;
    draftMessage.value = `已恢复本机草稿${draft.saved_at ? `（${new Date(draft.saved_at).toLocaleString()}）` : ""}`;
  }
  void nextTick(() => {
    applyingBundle = false;
  });
}

function nextSeqnameAfter(seqname: string) {
  const idx = cases.value.findIndex((item) => item.seqname === seqname);
  if (idx < 0) return "";
  return cases.value[idx + 1]?.seqname || cases.value[idx - 1]?.seqname || "";
}

function caseBundleUrl(seqname: string, scope: string) {
  const params = new URLSearchParams({ run_id: runId.value, scope });
  return `/gold-curation/cases/${encodeURIComponent(seqname)}?${params.toString()}`;
}

async function fetchCaseBundle(seqname: string): Promise<CaseBundle> {
  let resp = await apiFetch(caseBundleUrl(seqname, reviewScope));
  if (!resp.ok && reviewScope) {
    // A sample resolved from the full run can be missing from the current
    // review round; fall back to the unscoped comparison table.
    resp = await apiFetch(caseBundleUrl(seqname, ""));
  }
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as CaseBundle;
}

function preloadCase(seqname: string) {
  if (!seqname || caseCache.has(seqname)) return;
  void fetchCaseBundle(seqname)
    .then((bundle) => {
      caseCache.set(bundle.seqname, bundle);
    })
    .catch(() => {});
}

async function loadCase(seqname: string, resetMessages = true) {
  if (resetMessages) {
    saveError.value = "";
    saveMessage.value = "";
    actionMessage.value = "";
  }
  const cached = caseCache.get(seqname);
  if (cached) {
    applyCaseBundle(cached, resetMessages);
    syncLocationState();
    preloadCase(nextSeqnameAfter(seqname));
    return;
  }
  let bundle: CaseBundle;
  try {
    bundle = await fetchCaseBundle(seqname);
  } catch (err) {
    caseError.value = err instanceof Error ? err.message : String(err);
    return;
  }
  caseCache.set(bundle.seqname, bundle);
  applyCaseBundle(bundle, resetMessages);
  syncLocationState();
  preloadCase(nextSeqnameAfter(seqname));
}

async function reloadAll() {
  current.value = null;
  caseCache.clear();
  await loadCases();
}

function copyFromGold() {
  if (!current.value) return;
  delete recordSaveMessages.value[current.value.seqname];
  curatedLabels.value = [...current.value.gold.labels_by_residue];
  decisionChoice.value = "manual_corrected";
  curatedSource.value = "A";
  actionMessage.value = `已复制 A 到 C：${curatedLabels.value.length} 个残基。`;
  saveMessage.value = actionMessage.value;
  saveError.value = "";
}

function copyFromModel() {
  if (!current.value) return;
  delete recordSaveMessages.value[current.value.seqname];
  if (!current.value.prediction.available) {
    const reason = current.value.prediction.error || "模型没有可用编号结果";
    saveError.value = `B→C 失败：${reason}`;
    saveMessage.value = "";
    return;
  }
  curatedLabels.value = [...current.value.prediction.labels_by_residue];
  decisionChoice.value = "manual_corrected";
  curatedSource.value = "B";
  actionMessage.value = `已复制 B 到 C：${curatedLabels.value.length} 个残基。`;
  saveMessage.value = actionMessage.value;
  saveError.value = "";
}

function chooseGold() {
  if (!current.value) return;
  decisionChoice.value = "accepted_gold";
  saveError.value = "";
  saveMessage.value = "已选择 A：tRNAdb 原始编号。";
}

function chooseModel() {
  if (!current.value) return;
  if (!current.value.prediction.available) {
    const reason = current.value.prediction.error || "模型没有可用编号结果";
    saveError.value = `无法选择 B：${reason}`;
    saveMessage.value = "";
    return;
  }
  decisionChoice.value = "accepted_model";
  saveError.value = "";
  saveMessage.value = "已选择 B：模型预测。";
}

function chooseCurated() {
  if (!current.value) return;
  if (!hasCuratedDraft.value) {
    saveError.value = "C 尚未生成：请先按 Shift+A/Alt+A 或 Shift+B/Alt+B。";
    saveMessage.value = "";
    return;
  }
  decisionChoice.value = "manual_corrected";
  saveError.value = "";
  saveMessage.value = "已选择 C：人工校对结果。";
}

function selectCuratedDraft() {
  if (!hasCuratedDraft.value) {
    saveError.value = "C 尚未生成：请先点 A→C 或 B→C。";
    saveMessage.value = "";
    return;
  }
  decisionChoice.value = "manual_corrected";
}

function scrollDrawerToResidue(index: number) {
  void nextTick(() => {
    const list = drawerListRef.value;
    const row = list?.querySelector<HTMLElement>(`[data-residue-index="${index}"]`);
    if (!list || !row) return;
    const targetTop = row.offsetTop - list.clientHeight / 2 + row.offsetHeight / 2;
    list.scrollTop = Math.max(0, targetTop);
  });
}

function selectResidue(index: number, shouldScroll = true) {
  if (!current.value || !hasCuratedDraft.value) return;
  selectedResidueIndex.value = index;
  decisionChoice.value = "manual_corrected";
  if (shouldScroll) scrollDrawerToResidue(index);
}

function applyLabelSuggestion(index: number, label: string) {
  if (!current.value || !hasCuratedDraft.value) return;
  curatedLabels.value[index - 1] = label;
  selectedResidueIndex.value = index;
  decisionChoice.value = "manual_corrected";
  curatedSource.value = "manual";
}

function commonFixShortcut(fix: CommonFix) {
  return fix.id === "variable-loop-shift-left" ? "V" : "";
}

function commonFixTitle(fix: CommonFix) {
  const shortcut = commonFixShortcut(fix);
  return shortcut ? `${fix.description}（快捷键 ${shortcut}）` : fix.description;
}

function hasCommonFix(id: string) {
  return commonFixes.value.some((fix) => fix.id === id);
}

function applyCommonFixFromShortcut(id: string) {
  if (!current.value) return;
  if (!hasCuratedDraft.value) {
    saveError.value = "C 尚未生成：请先按 Shift+A/Alt+A 或 Shift+B/Alt+B。";
    saveMessage.value = "";
    return;
  }
  if (!hasCommonFix(id)) {
    saveError.value =
      id === "variable-loop-shift-left"
        ? "当前样本没有可用的 V环区前移处理。"
        : "当前样本没有可用的常见处理。";
    saveMessage.value = "";
    return;
  }
  applyCommonFix(id);
}

function applyCommonFix(id: string) {
  if (!current.value || !hasCuratedDraft.value) return;
  const next = [...curatedLabels.value];
  let changed = 0;

  if (id === "variable-loop-shift-left") {
    next.forEach((label, idx) => {
      const match = String(label || "").match(/^V(\d+)$/);
      if (!match) return;
      const n = Number(match[1]);
      if (!Number.isFinite(n) || n <= 1 || n > 5) return;
      const replacement = `V${n - 1}`;
      if (replacement !== label) {
        next[idx] = replacement;
        changed += 1;
      }
    });
  } else if (id === "compress-insertions") {
    const groups = new Map<string, Array<{ idx: number; label: string; suffix: number }>>();
    next.forEach((label, idx) => {
      const match = String(label || "").match(/^(-?\d+[A-Za-z]?|V\d+)i(\d+)$/);
      if (!match) return;
      const anchor = match[1];
      const suffix = Number(match[2]);
      if (!groups.has(anchor)) groups.set(anchor, []);
      groups.get(anchor)?.push({ idx, label, suffix });
    });
    groups.forEach((items) => {
      items
        .sort((a, b) => a.idx - b.idx)
        .forEach((item, order) => {
          const replacement = `${insertionAnchor(item.label)}i${order + 1}`;
          if (replacement !== item.label) {
            next[item.idx] = replacement;
            changed += 1;
          }
        });
    });
  }

  if (!changed) {
    saveError.value = "当前 C 不需要应用这个处理。";
    saveMessage.value = "";
    return;
  }
  curatedLabels.value = next;
  decisionChoice.value = "manual_corrected";
  curatedSource.value = "manual";
  saveMessage.value = `已应用常见处理：调整 ${changed} 个编号。`;
  saveError.value = "";
}

function selectResidueByLabel(label: string) {
  if (!current.value || !hasCuratedDraft.value) return;
  const wanted = String(label || "").trim();
  if (!wanted) return;
  const row = residueByLabel.value.get(wanted);
  if (row) selectResidue(row.index, true);
}

function submitDecision() {
  if (!canSubmitDecision.value) return;
  void saveDecision(decisionChoice.value);
}

function submitDecisionFromShortcut() {
  if (!current.value || saving.value) return;
  if (!canSubmitDecision.value) {
    saveError.value = validationSummary.value || "当前选择不能提交。";
    saveMessage.value = "";
    return;
  }
  submitDecision();
}

// Scoring eligibility is its own axis: dropping a sequence from the benchmark
// never replaces its answer, and the reason says why the answer is unusable.
const EXCLUSION_REASON_LABELS: Record<string, string> = {
  non_canonical_trna: "非经典 tRNA",
  terminal_cca_undetermined: "末端与 CCA 未定",
  variable_loop_undetermined: "可变环边界未定",
  incomplete_sequence: "序列不完整",
};

const EXCLUSION_OPTIONS: Array<{ value: EligibilityChoice; label: string; title: string }> = [
  { value: "non_canonical_trna", label: `不计入：${EXCLUSION_REASON_LABELS.non_canonical_trna}`, title: "序列不能按经典 Sprinzl 坐标编号" },
  { value: "terminal_cca_undetermined", label: `不计入：${EXCLUSION_REASON_LABELS.terminal_cca_undetermined}`, title: "3′ 末端或 CCA 添加位无法判定" },
  { value: "variable_loop_undetermined", label: `不计入：${EXCLUSION_REASON_LABELS.variable_loop_undetermined}`, title: "可变环边界无法判定" },
  { value: "incomplete_sequence", label: `不计入：${EXCLUSION_REASON_LABELS.incomplete_sequence}`, title: "序列缺端或过短" },
];

const eligibilityOptions: Array<{ value: EligibilityChoice; label: string; title: string }> = [
  { value: "include", label: "计入评分", title: "该序列参与本轮基准评分" },
  ...EXCLUSION_OPTIONS,
];

const eligibilityChoice = ref<EligibilityChoice>("include");
const savingEligibility = ref(false);

function eligibilityFromBundle(bundle: CaseBundle | null): EligibilityChoice {
  const gold = bundle?.active_gold;
  if (!gold || gold.evaluation_allowed !== false) return "include";
  const reason = String(gold.exclusion_reason || "");
  return EXCLUSION_OPTIONS.some((option) => option.value === reason)
    ? (reason as EligibilityChoice)
    : "non_canonical_trna";
}

async function applyEligibility(value: EligibilityChoice) {
  const bundle = current.value;
  if (!bundle || savingEligibility.value) return;
  const previous = eligibilityFromBundle(bundle);
  if (value === previous) return;
  const allowed = value === "include";
  if (!allowed && !window.confirm("确认把这条序列移出本轮评分吗？金标准答案保留，随时可以改回。")) {
    eligibilityChoice.value = previous;
    return;
  }
  savingEligibility.value = true;
  try {
    const params = new URLSearchParams({ run_id: runId.value });
    const resp = await apiFetch(
      `/gold-curation/cases/${encodeURIComponent(bundle.seqname)}/evaluation?${params.toString()}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allowed, reason: allowed ? "" : value }),
      }
    );
    if (!resp.ok) throw new Error(await readError(resp));
    bundle.active_gold = {
      ...(bundle.active_gold || {}),
      evaluation_allowed: allowed,
      exclusion_reason: allowed ? null : value,
      // Naive UTC, the same shape the answer table renders, so the stamp in the
      // A subtitle does not shift when the case is reloaded from MySQL.
      excluded_at: allowed ? null : new Date().toISOString().slice(0, 19),
    };
    const item = cases.value.find((row) => row.seqname === bundle.seqname);
    if (item) item.evaluation_allowed = allowed;
    const groups = summary.value?.groups;
    if (groups) {
      groups.excluded = Math.max(0, Number(groups.excluded || 0) + (allowed ? -1 : 1));
      groups.scored = Math.max(0, Number(groups.scored || 0) + (allowed ? 1 : -1));
    }
    eligibilityChoice.value = value;
    showToast(allowed ? "已计入本轮评分" : "已移出本轮评分", "success", 1500);
  } catch (err) {
    eligibilityChoice.value = previous;
    showToast(`评分状态保存失败：${err instanceof Error ? err.message : String(err)}`, "error", 3000);
  } finally {
    savingEligibility.value = false;
  }
}

const GOLD_SOURCE_BY_DECISION: Record<string, string> = {
  accepted_gold: "adopted_gold",
  accepted_model: "adopted_model",
  manual_corrected: "manual",
  unsure: "unsure",
  excluded: "excluded",
};

function localStampNow() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

// A is the answer table, so a submitted decision moves A immediately: saving C,
// adopting B, or re-confirming the answer all become visible without a reload.
function applySubmittedGold(status: string) {
  const bundle = current.value;
  if (!bundle) return;
  let labels: string[];
  if (status === "accepted_model") labels = [...(bundle.prediction.labels_by_residue || [])];
  else if (status === "manual_corrected") labels = [...curatedLabels.value];
  else labels = [...(bundle.gold.labels_by_residue || [])];
  if (!labels.length) return;
  bundle.gold = { ...bundle.gold, labels_by_residue: labels };
  bundle.active_gold = {
    ...bundle.active_gold,
    source: GOLD_SOURCE_BY_DECISION[status] || "raw",
    decided_at: localStampNow(),
    decision_run_id: runId.value,
    labels_by_residue: labels,
  };
}

// Committing a decision empties C: its labels are now panel A, and the draft
// that carried them is no longer pending.
function clearScratchAfterSubmit() {
  suppressDraftSave = true;
  curatedLabels.value = [];
  curatedSource.value = "none";
  decisionChoice.value = "accepted_gold";
  if (current.value) {
    current.value.curated_labels = null;
    current.value.curated_origin = "";
    current.value.draft = null;
  }
  draftMessage.value = "";
  window.setTimeout(() => {
    suppressDraftSave = false;
  }, 0);
}

async function saveDecision(status: string, exclusionReason = "") {
  if (!current.value) return;
  const savedSeqname = current.value.seqname;
  const savedIndex = currentIndex.value;
  const oldStatus = current.value.status;
  saving.value = true;
  saveError.value = "";
  saveMessage.value = "";
  delete recordSaveMessages.value[savedSeqname];
  try {
    const params = new URLSearchParams({ run_id: runId.value, scope: reviewScope });
    const resp = await apiFetch(
      `/gold-curation/cases/${encodeURIComponent(current.value.seqname)}/decision?${params.toString()}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status,
          curated_labels: curatedLabels.value,
          exclusion_reason: exclusionReason || undefined,
        }),
      }
    );
    if (!resp.ok) throw new Error(await readError(resp));
    current.value.status = status;
    applySubmittedGold(status);
    clearScratchAfterSubmit();
    current.value.correction = {
      ...(current.value.correction || {}),
      status,
      decision: status,
      exclusion_reason: exclusionReason,
      curated_labels: [...curatedLabels.value],
    };
    recordSaveMessages.value[savedSeqname] = "记录已保存。";
    clearLocalDraft(savedSeqname);
    draftMessage.value = "";
    caseCache.delete(savedSeqname);

    // Show success toast and delay before advancing to next case
    const statusLabels: Record<string, string> = {
      accepted_gold: 'A 确认现行金标准',
      accepted_model: 'B 本次运行结果',
      manual_corrected: 'C 人工校准',
      unsure: '不确定',
      excluded: '放弃',
    };
    const label = statusLabels[status] || status;
    showToast(`${savedSeqname.split('|')[0]} 已提交：${label}`, 'success', 1500);

    // Delay the queue advancement to let user see the success message
    await new Promise(resolve => setTimeout(resolve, 800));
    advanceQueueAfterSave(savedSeqname, savedIndex, oldStatus, status);
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    saveError.value = errorMsg;
    showToast(`提交失败：${errorMsg}`, 'error', 3000);
  } finally {
    saving.value = false;
  }
}

function advanceQueueAfterSave(seqname: string, previousIndex: number, oldStatus: string, status: string) {
  const idx = cases.value.findIndex((item) => item.seqname === seqname);
  const stayAt = idx >= 0 ? idx : previousIndex;
  let removedFromQueue = false;
  if (idx >= 0) {
    if (!matchesQueueFilter(status)) {
      cases.value.splice(idx, 1);
      queueTotal.value = Math.max(0, queueTotal.value - 1);
      removedFromQueue = true;
    } else {
      cases.value[idx].status = status;
    }
  }
  if (summary.value?.counts) {
    if (oldStatus) {
      summary.value.counts[oldStatus] = Math.max(0, Number(summary.value.counts[oldStatus] || 0) - 1);
    }
    summary.value.counts[status] = Number(summary.value.counts[status] || 0) + 1;
    const groups = summary.value.groups;
    if (groups) {
      // 全部差异 depends on the residual mismatch, which only the next fetch
      // can grade, so the group counts move with the decision itself.
      const step = (key: string, from: string, to: string) => {
        groups[key] = Math.max(0, Number(groups[key] || 0) + (to === key ? 1 : 0) - (from === key ? 1 : 0));
      };
      step("needs_review", oldStatus, status);
      step("unsure", oldStatus, status);
      step("excluded", oldStatus, status);
      const wasDone = COMPLETED_STATUSES.includes(oldStatus);
      const isDone = COMPLETED_STATUSES.includes(status);
      groups.completed = Math.max(0, Number(groups.completed || 0) + (isDone ? 1 : 0) - (wasDone ? 1 : 0));
    }
  }

  const next = removedFromQueue
    ? cases.value[stayAt] || cases.value[stayAt - 1]
    : cases.value[stayAt + 1] || cases.value[stayAt] || cases.value[stayAt - 1];
  if (next) {
    void loadCase(next.seqname).then(() => {
      preloadCase(cases.value[stayAt + 2]?.seqname || "");
    });
  } else {
    current.value = null;
    selectedSeqname.value = "";
    curatedLabels.value = [];
  }
}

const emptyQueueText = computed(() => {
  const labels: Record<QueueFilter, string> = {
    reviewable: "当前没有与金标准存在差异的样本。",
    needs_review: "当前没有待确认金标准的样本。",
    completed: "当前没有已确认金标准的样本。",
    unsure: "当前没有标记为不确定的样本。",
    excluded: "当前没有放弃的样本。",
  };
  return labels[queueFilter.value];
});

const residueRows = computed<ResidueDisplayRow[]>(() => {
  if (!current.value) return [];
  const gold = current.value.gold;
  const pred = current.value.prediction;
  const len = Math.max(gold.labels_by_residue.length, pred.labels_by_residue.length, curatedLabels.value.length);
  const rows: ResidueDisplayRow[] = [];
  for (let i = 0; i < len; i += 1) {
    const goldLabel = gold.labels_by_residue[i] || "";
    const predLabel = pred.labels_by_residue[i] || "";
    const curatedLabel = curatedLabels.value[i] || "";
    if (goldLabel === predLabel && (!hasCuratedDraft.value || curatedLabel === goldLabel)) continue;
    const diff = current.value.diffs.find((item) => item.residue_index === i + 1);
    rows.push({
      index: i + 1,
      base: gold.bases_by_residue[i] || pred.bases_by_residue[i] || "-",
      goldLabel,
      predLabel,
      region: diff?.region || "",
    });
  }
  return rows;
});

const editorRows = computed<ResidueDisplayRow[]>(() => {
  if (!current.value) return [];
  const gold = current.value.gold;
  const pred = current.value.prediction;
  const rows: ResidueDisplayRow[] = [];
  for (let i = 0; i < gold.bases_by_residue.length; i += 1) {
    const goldLabel = gold.labels_by_residue[i] || "";
    const predLabel = pred.labels_by_residue[i] || "";
    const diff = current.value.diffs.find((item) => item.residue_index === i + 1);
    rows.push({
      index: i + 1,
      base: gold.bases_by_residue[i] || "-",
      goldLabel,
      predLabel,
      region: diff?.region || compactRegionForLabel(goldLabel || predLabel),
    });
  }
  return rows;
});

const hasCuratedDraft = computed(() => {
  if (!current.value) return false;
  return curatedLabels.value.length === current.value.gold.bases_by_residue.length;
});

const correctionStatus = computed(() => {
  return String(current.value?.correction?.status || current.value?.correction?.decision || "").trim();
});

const manualCorrectionStatus = computed(() => {
  // A pending draft is the actionable state, so it is reported before the
  // decision that the draft was written on top of.
  if (current.value?.curated_origin === "server_draft") return "结构方案草稿，待确认";
  if (correctionStatus.value === "accepted_gold") return "采纳 A";
  if (correctionStatus.value === "accepted_model") return "采纳 B";
  if (correctionStatus.value === "manual_corrected") return "本轮人工矫正";
  const historicalStatus = String(
    current.value?.historical_correction?.status || current.value?.historical_correction?.decision || ""
  ).trim();
  if (historicalStatus === "accepted_gold") return "历史采纳 A";
  if (historicalStatus === "accepted_model") return "历史采纳 B";
  if (historicalStatus === "manual_corrected") return "历史人工矫正";
  if (hasCuratedDraft.value && decisionChoice.value === "manual_corrected") {
    return "本轮人工矫正（待提交）";
  }
  return "未进行";
});

const submissionStatus = computed(() => {
  if (correctionStatus.value === "excluded") return "已标记为非经典 tRNA";
  if (correctionStatus.value === "unsure") return "已提交，暂不确定";
  if (correctionStatus.value) return "已提交";
  if (current.value?.historical_correction) return "本轮尚未提交";
  return "尚未提交";
});

const submissionStatusTone = computed(() => {
  if (!correctionStatus.value) return "is-pending";
  if (correctionStatus.value === "excluded") return "is-excluded";
  if (correctionStatus.value === "unsure") return "is-pending";
  return "is-submitted";
});

const isServerDraft = computed(() => {
  return current.value?.curated_origin === "server_draft";
});

const serverDraftNote = computed(() => {
  return String(current.value?.draft?.notes || "依据结构证据生成，待人工确认。").trim();
});

const residueByLabel = computed(() => {
  const byLabel = new Map<string, ResidueDisplayRow>();
  if (!hasCuratedDraft.value) return byLabel;
  editorRows.value.forEach((row) => {
    const label = String(curatedLabels.value[row.index - 1] || "").trim();
    if (label && !byLabel.has(label)) byLabel.set(label, row);
  });
  return byLabel;
});

const selectedResidueLabel = computed(() => {
  const idx = selectedResidueIndex.value;
  if (!idx || !hasCuratedDraft.value) return "";
  return curatedLabels.value[idx - 1] || "";
});

const allowedLabelSet = computed(() => {
  const labels = new Set<string>();
  current.value?.numbering.forEach((label) => {
    const clean = String(label || "").trim();
    if (clean) labels.add(clean);
  });
  return labels;
});

const labelUseCounts = computed(() => {
  const counts = new Map<string, number>();
  if (!hasCuratedDraft.value) return counts;
  curatedLabels.value.forEach((raw) => {
    const label = String(raw || "").trim();
    if (!label) return;
    counts.set(label, (counts.get(label) || 0) + 1);
  });
  return counts;
});

function labelRank(label: string) {
  if (!current.value) return Number.NaN;
  const clean = String(label || "").trim();
  const direct = current.value.numbering.indexOf(clean);
  if (direct >= 0) return direct * 100000;
  const match = clean.match(/^(-?\d+[A-Za-z]?|V\d+)i(\d+)$/);
  if (!match) return Number.NaN;
  const anchor = match[1];
  const suffix = Number(match[2]);
  const anchorIndex = current.value.numbering.indexOf(anchor);
  if (anchorIndex < 0 || !Number.isFinite(suffix)) return Number.NaN;
  return anchorIndex * 100000 + suffix;
}

const labelOrderErrors = computed(() => {
  const errors = new Map<number, string>();
  if (!hasCuratedDraft.value) return errors;
  let previous: { index: number; label: string; rank: number } | null = null;
  curatedLabels.value.forEach((raw, idx) => {
    const label = String(raw || "").trim();
    if (!label || !isAllowedLabel(label) || (labelUseCounts.value.get(label) || 0) > 1) return;
    const rank = labelRank(label);
    if (!Number.isFinite(rank)) return;
    if (previous && rank <= previous.rank) {
      errors.set(idx + 1, `编号顺序错误：应排在 ${previous.label} 后面`);
      if (!errors.has(previous.index)) {
        errors.set(previous.index, `编号顺序错误：后面第 ${idx + 1} 个残基用了更靠前的 ${label}`);
      }
      return;
    }
    previous = { index: idx + 1, label, rank };
  });
  return errors;
});

function labelError(index: number) {
  if (!hasCuratedDraft.value) return "";
  const label = String(curatedLabels.value[index - 1] || "").trim();
  if (!label) return "编号不能为空";
  if (!isAllowedLabel(label)) return "不是标准编号";
  if ((labelUseCounts.value.get(label) || 0) > 1) return "编号重复";
  const orderError = labelOrderErrors.value.get(index);
  if (orderError) return orderError;
  return "";
}

function insertionAnchor(label: string) {
  const match = String(label || "").trim().match(/^(-?\d+[A-Za-z]?|V\d+)i\d+$/);
  return match?.[1] || "";
}

function isInsertionLabel(label: string) {
  const anchor = insertionAnchor(label);
  return Boolean(anchor && allowedLabelSet.value.has(anchor));
}

function isAllowedLabel(label: string) {
  const clean = String(label || "").trim();
  return allowedLabelSet.value.has(clean) || isInsertionLabel(clean);
}

function numberingIndex(label: string) {
  if (!current.value) return -1;
  const clean = String(label || "").trim();
  const pos = current.value.numbering.indexOf(clean);
  if (pos >= 0) return pos;
  const anchor = insertionAnchor(clean);
  return anchor ? current.value.numbering.indexOf(anchor) : -1;
}

function nearbyLabels(label: string, radius = 2) {
  if (!current.value) return [];
  const pos = numberingIndex(label);
  if (pos < 0) return [];
  const start = Math.max(0, pos - radius);
  const end = Math.min(current.value.numbering.length, pos + radius + 1);
  return current.value.numbering.slice(start, end);
}

function uniqueLabels(values: string[]) {
  const seen = new Set<string>();
  const out: string[] = [];
  values.forEach((raw) => {
    const label = String(raw || "").trim();
    if (!label || seen.has(label)) return;
    seen.add(label);
    out.push(label);
  });
  return out;
}

function insertionLabelsFor(label: string) {
  const clean = String(label || "").trim();
  const anchor = insertionAnchor(clean) || clean;
  if (!allowedLabelSet.value.has(anchor)) return [];
  return [`${anchor}i1`, `${anchor}i2`, `${anchor}i3`];
}

function localContextLabels(index: number) {
  const out: string[] = [];
  const sources = [
    current.value?.gold.labels_by_residue || [],
    current.value?.prediction.labels_by_residue || [],
    curatedLabels.value,
  ];
  sources.forEach((labels) => {
    for (let offset = -2; offset <= 2; offset += 1) {
      const label = labels[index - 1 + offset];
      if (label) out.push(String(label));
    }
  });
  return out;
}

function unusedFirst(labels: string[], rowIndex: number) {
  return labels.sort((a, b) => {
    const aUsed = (labelUseCounts.value.get(a) || 0) > (curatedLabels.value[rowIndex - 1] === a ? 1 : 0);
    const bUsed = (labelUseCounts.value.get(b) || 0) > (curatedLabels.value[rowIndex - 1] === b ? 1 : 0);
    if (aUsed !== bUsed) return aUsed ? 1 : -1;
    return 0;
  });
}

function labelSuggestions(row: ResidueDisplayRow) {
  if (!hasCuratedDraft.value) return [];
  const currentLabel = curatedLabels.value[row.index - 1] || "";
  const local = localContextLabels(row.index);
  const anchors = uniqueLabels([row.goldLabel, row.predLabel, currentLabel, ...local])
    .flatMap((label) => [label, ...nearbyLabels(label, 1), ...insertionLabelsFor(label)]);
  return unusedFirst(uniqueLabels([
    row.goldLabel,
    row.predLabel,
    currentLabel,
    ...local,
    ...anchors,
    ...nearbyLabels(row.goldLabel),
    ...nearbyLabels(row.predLabel),
    ...nearbyLabels(currentLabel),
  ]).filter(isAllowedLabel), row.index).slice(0, 12);
}

function compactRegionForLabel(label: string) {
  if (!label) return "";
  const diff = current.value?.diffs.find((item) => item.gold_label === label || item.pred_label === label);
  return diff?.region || "";
}

const labelErrors = computed(() =>
  hasCuratedDraft.value
    ? curatedLabels.value
        .map((_label, idx) => ({ index: idx + 1, message: labelError(idx + 1) }))
        .filter((item) => item.message)
    : []
);

const commonFixes = computed<CommonFix[]>(() => {
  if (!hasCuratedDraft.value) return [];
  const labels = curatedLabels.value.map((label) => String(label || "").trim());
  const labelSet = new Set(labels.filter(Boolean));
  const fixes: CommonFix[] = [];

  const vLoopNumbers = labels
    .map((label) => label.match(/^V(\d+)$/)?.[1])
    .filter((value): value is string => Boolean(value))
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value >= 1 && value <= 5);
  if (!labelSet.has("V1") && vLoopNumbers.some((value) => value >= 2)) {
    const minV = Math.min(...vLoopNumbers);
    fixes.push({
      id: "variable-loop-shift-left",
      label: "V环区前移",
      description:
        minV === 2
          ? "存在 V2/V3/... 但没有 V1：只把 V1-V5 的 V环区整体前移"
          : `V环区从 V${minV} 开始：只移动 V1-V5，不改 V配对区域`,
    });
  }

  const insertionGroups = new Map<string, number[]>();
  labels.forEach((label) => {
    const match = label.match(/^(-?\d+[A-Za-z]?|V\d+)i(\d+)$/);
    if (!match) return;
    const anchor = match[1];
    const suffix = Number(match[2]);
    if (!insertionGroups.has(anchor)) insertionGroups.set(anchor, []);
    insertionGroups.get(anchor)?.push(suffix);
  });
  const hasSparseInsertion = Array.from(insertionGroups.values()).some((suffixes) => {
    const sorted = [...suffixes].sort((a, b) => a - b);
    return sorted.some((value, idx) => value !== idx + 1);
  });
  if (hasSparseInsertion) {
    fixes.push({
      id: "compress-insertions",
      label: "i编号压缩",
      description: "把同一 anchor 下的插入编号整理为连续的 i1/i2/i3",
    });
  }

  return fixes;
});

const validationSummary = computed(() => {
  if (!current.value) return "";
  if (!hasCuratedDraft.value) {
    return "C 尚未生成，请先点 A→C 或 B→C。";
  }
  if (curatedLabels.value.length !== current.value.gold.bases_by_residue.length) {
    return "C 的残基数量不一致，不能提交。";
  }
  if (!labelErrors.value.length) return "";
  const first = labelErrors.value[0];
  return `C 有 ${labelErrors.value.length} 处编号问题：第 ${first.index} 个残基 ${first.message}。`;
});

const currentIndex = computed(() => {
  if (!current.value) return -1;
  return cases.value.findIndex((item) => item.seqname === current.value?.seqname);
});

function goPrev() {
  const idx = currentIndex.value;
  if (idx <= 0) return;
  void loadCase(cases.value[idx - 1].seqname);
}

function goNext() {
  const idx = currentIndex.value;
  if (idx < 0 || idx >= cases.value.length - 1) return;
  void loadCase(cases.value[idx + 1].seqname);
}

function isTextEntryTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select";
}

function blocksPlainEnterSubmit(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return target.tagName.toLowerCase() === "textarea";
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented) return;

  const key = event.key.toLowerCase();
  if (event.isComposing || key === "process") return;

  const inTextEntry = isTextEntryTarget(event.target);
  const ctrlOrMeta = event.ctrlKey || event.metaKey;

  if (key === "enter") {
    if (ctrlOrMeta || !blocksPlainEnterSubmit(event.target)) {
      event.preventDefault();
      submitDecisionFromShortcut();
    }
    return;
  }

  if (inTextEntry || ctrlOrMeta) return;

  if (event.shiftKey || event.altKey) {
    if (key === "a") {
      event.preventDefault();
      copyFromGold();
      return;
    }
    if (key === "b") {
      event.preventDefault();
      copyFromModel();
      return;
    }
    if (key === "v") {
      event.preventDefault();
      applyCommonFixFromShortcut("variable-loop-shift-left");
      return;
    }
    return;
  }

  if (key === "a") {
    event.preventDefault();
    chooseGold();
    return;
  }
  if (key === "b") {
    event.preventDefault();
    chooseModel();
    return;
  }
  if (key === "c") {
    event.preventDefault();
    chooseCurated();
    return;
  }
  if (key === "v") {
    event.preventDefault();
    applyCommonFixFromShortcut("variable-loop-shift-left");
    return;
  }
  if (key === "arrowleft" || key === "[") {
    event.preventDefault();
    goPrev();
    return;
  }
  if (key === "arrowright" || key === "]") {
    event.preventDefault();
    goNext();
  }
}

function statusLabel(status: string) {
  switch (status) {
    case "needs_review":
      return "待确认金标准";
    case "accepted_gold":
      return "已确认：沿用现行金标准";
    case "accepted_model":
      return "已确认：采纳 B";
    case "manual_corrected":
      return "已确认：保存 C";
    case "unsure":
      return "已确认：不确定";
    case "excluded":
      return "已确认：放弃";
    case "auto_agree":
      return "与金标准一致";
    default:
      return status || "-";
  }
}

const DECIDED_STATUSES = ["accepted_gold", "accepted_model", "manual_corrected", "unsure", "excluded"];

function batchLabelForRun(id: string) {
  const batch = runBatches.value.find((item) => item.run_id === id);
  return batch ? String(batch.label || "") : "";
}

// The answer table keeps one answer per sequence, so a decision made while
// reviewing an older batch would otherwise read as if it belonged to the batch
// currently on screen.
const decisionProvenanceText = computed(() => {
  const active = current.value?.active_gold;
  if (!active) return "";
  const decided = String(active.decided_at || "").slice(0, 10);
  const run = String(active.decision_run_id || "");
  if (!decided && !run) return "";
  const parts: string[] = [];
  if (decided) parts.push(decided);
  if (run && run !== runId.value) parts.push("非本批次");
  return parts.join(" · ");
});

// The full provenance belongs in the tooltip: the badge itself has to stay short
// enough that the queue header does not reflow.
const decisionProvenanceTitle = computed(() => {
  const active = current.value?.active_gold;
  const run = String(active?.decision_run_id || "");
  if (!run) return "该序列还没有提交过金标准结论";
  const batch = batchLabelForRun(run) || run;
  const decided = String(active?.decided_at || "").slice(0, 16).replace("T", " ");
  return `现行金标准来自 ${batch} 批次${decided ? `，提交于 ${decided}` : ""}`;
});

const statusBadgeText = computed(() => {
  const status = String(current.value?.status || "");
  const label = statusLabel(status);
  if (!DECIDED_STATUSES.includes(status)) return label;
  const provenance = decisionProvenanceText.value;
  return provenance ? `${label}（${provenance}）` : label;
});

const recordInfo = computed(() => {
  const meta = current.value?.gold.structure_meta || {};
  return {
    length: current.value?.input_seq.replace(/\s+/g, "").length || 0,
    predictor: String(meta.predictor || "").trim(),
  };
});

function compactRegion(region: string) {
  const map: Record<string, string> = {
    "Aminoacyl arm 5' end": "氨酰臂5'",
    "Aminoacyl arm 3' end": "氨酰臂3'",
    "Anticodon loop + Anticodon stem": "反密码子区",
    "D loop + D stem": "D区",
    "T loop + T stem": "T区",
    "Variable loop": "可变区",
  };
  return map[region] || region;
}

const predictionCardSubtitle = computed(() => {
  if (!current.value?.prediction.available) {
    return current.value?.prediction.error
      ? "模型运行失败，无可用编号"
      : "无可用模型编号";
  }
  return `本次模型结果（${batchShortLabel.value}）`;
});

// Panel A is the active answer from the gold table, not the untouched tRNAdb
// numbering: the subtitle says where that answer came from and when it settled.
const goldMatchesRawReference = computed(() => {
  const raw = current.value?.raw;
  if (!raw) return true;
  const gold = current.value?.gold.labels_by_residue || [];
  const original = raw.labels_by_residue || [];
  return gold.length === original.length && gold.every((value, index) => value === original[index]);
});

const goldCardSubtitle = computed(() => {
  const active = current.value?.active_gold;
  const source = String(active?.source || "raw");
  const stamp = String(active?.decided_at || "").slice(0, 16).replace("T", " ");
  const when = stamp ? ` · 提交于 ${stamp}` : "";
  if (source === "excluded" || active?.evaluation_allowed === false) {
    const reason = EXCLUSION_REASON_LABELS[String(active?.exclusion_reason || "")] || "非经典 tRNA";
    const moved = String(active?.excluded_at || "").slice(0, 16).replace("T", " ");
    return `现行金标准（${reason}，不计入基准${moved ? ` · 移出于 ${moved}` : when}）`;
  }
  // What A shows is the numbering itself: a submitted answer that equals the
  // tRNAdb numbering reads as unchanged, whoever produced it.
  if (goldMatchesRawReference.value) {
    return source === "raw"
      ? "现行金标准（tRNAdb 原始编号）"
      : `现行金标准（与 tRNAdb 原始编号一致${when}）`;
  }
  if (source === "manual") return `现行金标准（人工校准${when}）`;
  if (source === "adopted_model") return `现行金标准（采纳模型结果${when}）`;
  if (source === "adopted_gold") return `现行金标准（沿用先前校准结果${when}）`;
  if (source === "unsure") return `现行金标准（复核未定，保留原答案${when}）`;
  return "现行金标准（tRNAdb 原始编号）";
});

// B is whichever stored batch the page is showing, so its label cannot go stale
// when the reviewer switches batches.
const batchShortLabel = computed(() => String(currentBatch.value?.label || runId.value));

const canSubmitDecision = computed(() => {
  if (!current.value) return false;
  if (decisionChoice.value === "accepted_model") return current.value.prediction.available;
  if (decisionChoice.value === "manual_corrected") {
    return hasCuratedDraft.value && labelErrors.value.length === 0;
  }
  return true;
});

const curatedSubtitle = computed(() => {
  if (!hasCuratedDraft.value) return "尚未生成";
  if (isServerDraft.value) return "校准草稿（未提交）";
  if (curatedSource.value === "A") return "草稿：复制自 A，未提交";
  if (curatedSource.value === "B") return "草稿：复制自 B，未提交";
  return "草稿：手动编辑，未提交";
});

onMounted(() => {
  window.addEventListener("keydown", handleGlobalKeydown);
  void loadRunBatches();
  void reloadAll();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", handleGlobalKeydown);
  window.removeEventListener("pointerdown", handleLookupOutsidePointer, true);
  interpretationController?.abort();
});

watch([curatedLabels, decisionChoice], () => {
  scheduleDraftSave();
}, { deep: true });

watch(panelLanguage, (language) => {
  try {
    window.localStorage.setItem(PANEL_LANGUAGE_STORAGE_KEY, language);
  } catch {
    // The language still applies to the current page.
  }
  resetInterpretation();
  // The reviewer just asked for another language, so a visible write-up is
  // rewritten in it instead of staying stale.
  if (isInterpretationStep.value) void generateCaseInterpretation();
});
</script>

<style scoped>
.curation-shell {
  min-height: 100vh;
  background: var(--page-bg);
  color: var(--text-strong);
}
.curation-main {
  /* A fixed 1360px cap left a 275px empty band on each side of a 1920 screen. */
  width: min(100%, var(--content-max));
  margin: 0 auto;
  padding: 24px 24px 32px;
}
.metric-strip {
  display: flex;
  align-items: stretch;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--surface-1);
  overflow: hidden;
}
.metric-basis,
.metric-cell {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  white-space: nowrap;
}
.metric-basis {
  color: var(--text-muted);
  font-weight: 700;
}
.metric-basis + .metric-cell,
.metric-cell + .metric-cell {
  border-left: 1px solid var(--border-soft);
}
.metric-cell b {
  color: var(--text-strong);
  font-weight: 700;
}
.metric-regions {
  gap: 5px;
}
.metric-regions-label {
  color: var(--text-muted);
}
.metric-regions .region-chip {
  background: var(--surface-2);
}
/* Inside the narrow C drawer the same numbers stack into two columns. */
.metric-strip.is-drawer {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  border: none;
  background: transparent;
  border-radius: 0;
  overflow: visible;
}
.metric-strip.is-drawer .metric-basis,
.metric-strip.is-drawer .metric-cell {
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--surface-1);
  white-space: normal;
  font-size: 0.76rem;
  line-height: 1.25;
}
.metric-strip.is-drawer .metric-regions {
  grid-column: 1 / -1;
  gap: 4px;
}
.text-input,
.select-input,
.label-input {
  border: 1px solid var(--border-soft);
  background: var(--surface-1);
  color: var(--text-strong);
  border-radius: 6px;
  padding: 8px 10px;
}
.review-pane {
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
  border-radius: 8px;
}
.review-pane {
  padding: 16px;
  min-width: 0;
  min-height: 520px;
}
.workbench-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: start;
  gap: 16px;
}
.review-pane {
  grid-column: span 3;
}
.curation-topbar {
  /* Full-bleed header: the bar paints edge to edge, its controls line up with
     the content measure below. */
  position: sticky;
  top: 0;
  z-index: 20000;
  width: 100%;
  background: var(--nav-bg);
  color: var(--nav-text);
  border-bottom: 1px solid var(--border-soft);
  box-shadow: var(--nav-shadow);
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
}
.work-strip {
  width: min(100%, var(--content-max));
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  grid-template-areas: "title filter";
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 0 24px;
  overflow: visible;
}
.queue-title {
  grid-area: title;
  display: flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
  flex: 0 0 auto;
  min-width: 0;
}
.queue-side {
  grid-area: filter;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}
.queue-filter {
  min-width: 0;
}
.language-select {
  max-width: 150px;
}
/* The batch control sizes to its own label, capped so the strip stays stable. */
.run-select {
  max-width: 300px;
}
.status-pill,
.status-chip {
  border: 1px solid rgba(var(--success-rgb), 0.45);
  background: rgba(var(--success-rgb), 0.12);
  color: var(--text-strong);
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 0.78rem;
  white-space: nowrap;
}
.lookup-toggle {
  border: none;
  background: transparent;
  color: var(--text-muted);
}
.lookup-toggle:hover,
.lookup-toggle:focus-visible {
  color: var(--text-strong);
}
.lookup-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 0 2px;
  border-bottom: 1px solid var(--border-soft);
  color: var(--text-muted);
}
.lookup-input {
  width: clamp(170px, 18vw, 250px);
  min-width: 140px;
  border: none;
  background: transparent;
  color: var(--text-strong);
  font: inherit;
  padding: 2px 0;
  outline: none;
}
.search-glyph {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}
.muted-line,
.empty-state,
.empty-review {
  color: var(--text-muted);
  font-size: 0.85rem;
}
.case-title-select {
  /* seqnames run long, and this control now shares a row with the pager */
  max-width: 268px;
}
.side-column {
  position: sticky;
  top: 68px;
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  height: min(760px, calc(100vh - 92px));
}
.record-info-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 11px 12px;
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  background: var(--surface-1);
}
.record-info-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--text-strong);
}
.record-info-grid {
  display: grid;
  gap: 5px;
  margin: 0;
}
.record-info-grid > div {
  display: grid;
  grid-template-columns: minmax(62px, auto) minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.record-info-grid dt {
  color: var(--text-muted);
  font-size: 0.74rem;
}
.record-info-grid dd {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.82rem;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

/* Toast notification */
.toast-container {
  position: fixed;
  top: 80px;
  right: 24px;
  z-index: 9999;
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  border-radius: 8px;
  background: var(--surface-1);
  border: 1px solid var(--border-soft);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  font-size: 0.88rem;
  color: var(--text-strong);
  pointer-events: auto;
  min-width: 200px;
  max-width: 400px;
}
.toast-success {
  border-left: 3px solid #10b981;
}
.toast-success .toast-icon {
  color: #10b981;
  font-weight: bold;
}
.toast-error {
  border-left: 3px solid #ef4444;
}
.toast-error .toast-icon {
  color: #ef4444;
  font-weight: bold;
}
.toast-info {
  border-left: 3px solid #3b82f6;
}
.toast-info .toast-icon {
  color: #3b82f6;
  font-weight: bold;
}
.toast-message {
  flex: 1;
  word-break: break-all;
}

/* Toast transition */
.toast-enter-active {
  transition: all 0.3s ease-out;
}
.toast-leave-active {
  transition: all 0.2s ease-in;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(30px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(30px);
}

.submission-state {
  display: inline-flex;
  align-items: center;
  min-height: 23px;
  padding: 2px 7px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
}
.submission-state.is-submitted {
  border-color: rgba(var(--success-rgb), 0.46);
  background: rgba(var(--success-rgb), 0.12);
  color: var(--success);
}
.submission-state.is-pending {
  border-color: rgba(var(--info-rgb, 59, 130, 246), 0.42);
  background: rgba(var(--info-rgb, 59, 130, 246), 0.1);
  color: var(--text-strong);
}
.submission-state.is-excluded {
  border-color: rgba(180, 35, 24, 0.42);
  background: rgba(180, 35, 24, 0.08);
  color: var(--danger);
}
.record-save-notice {
  flex: 1 1 100%;
  margin-top: 2px;
  padding: 7px 9px;
  border: 1px solid rgba(var(--success-rgb), 0.28);
  border-radius: 6px;
  background: rgba(var(--success-rgb), 0.1);
  color: var(--success);
  font-size: 0.82rem;
  line-height: 1.3;
}
.region-chip {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  padding: 2px 7px;
  background: var(--surface-1);
}
.evidence-risk-state {
  display: inline-flex;
  align-items: center;
  min-height: 21px;
  padding: 2px 7px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 700;
}
.evidence-risk-state.is-risk {
  border-color: rgba(180, 35, 24, 0.42);
  background: rgba(180, 35, 24, 0.08);
  color: var(--danger);
}
.secondary-btn,
.primary-btn,
.save-btn,
.danger-btn {
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  padding: 7px 10px;
  cursor: pointer;
  white-space: nowrap;
  line-height: 1.2;
}
.icon-btn {
  width: 38px;
  height: 38px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 38px;
  font-size: 1.15rem;
  font-weight: 800;
}
.icon-btn span {
  transform: translateY(-1px);
}
.secondary-btn {
  background: var(--surface-1);
  color: var(--text-strong);
}
.primary-btn {
  background: rgba(var(--info-rgb, 59, 130, 246), 0.18);
  color: var(--text-strong);
}
.save-btn {
  border-color: rgba(var(--success-rgb), 0.65);
  background: rgba(var(--success-rgb), 0.22);
  color: var(--text-strong);
}
.danger-btn {
  border-color: rgba(180, 35, 24, 0.45);
  background: rgba(180, 35, 24, 0.12);
  color: var(--danger);
}
.full {
  width: 100%;
}
.diff-section {
  margin-top: 18px;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: var(--surface-1);
  padding: 10px 12px;
}
.diff-section summary {
  cursor: pointer;
  font-weight: 700;
  color: var(--text-strong);
}
.section-title {
  font-weight: 700;
}
.diff-table-wrap {
  margin-top: 10px;
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
}
.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
}
.diff-table th,
.diff-table td {
  border-bottom: 1px solid var(--border-soft);
  padding: 7px 8px;
  text-align: left;
}
.diff-table th {
  position: sticky;
  top: 0;
  background: var(--surface-2);
  z-index: 1;
}
.diff-table tr.changed td {
  background: rgba(180, 35, 24, 0.06);
}
.label-input {
  width: 82px;
  font-family: Consolas, monospace;
}
.label-input.edited {
  border-color: rgba(var(--success-rgb), 0.75);
}
.label-input.invalid {
  border-color: rgba(180, 35, 24, 0.85);
  background: rgba(180, 35, 24, 0.08);
}
.field-error {
  margin-top: 4px;
  color: var(--danger);
  font-size: 0.74rem;
}
.abc-section {
  margin-top: 16px;
}
.abc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.abc-card-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.abc-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 13px;
  border-top: 1px solid var(--border-soft);
}
.abc-pager,
.abc-decisions {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.eligibility-select {
  min-width: 178px;
}
.abc-counter {
  min-width: 64px;
  text-align: center;
  color: var(--text-soft);
  font-size: 0.82rem;
  white-space: nowrap;
}
.process-section {
  margin-top: 14px;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-1);
}
.process-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 11px;
  border: none;
  background: transparent;
  color: var(--text-strong);
  font-size: 0.85rem;
}
.process-title {
  flex: 0 0 auto;
  font-weight: 700;
}
.process-summary {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
  font-size: 0.8rem;
}
.process-body {
  padding: 0 11px 12px;
}
.interpretation-text {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.84rem;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.reference-list {
  display: grid;
  gap: 7px;
  margin: 10px 0 0;
  padding: 9px 0 0;
  border-top: 1px solid var(--border-soft);
  list-style: none;
}
.reference-list li {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 2px 8px;
  color: var(--text-strong);
  font-size: 0.78rem;
  line-height: 1.45;
}
.reference-list .reference-marker {
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.reference-list a {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid var(--border-soft);
}
.reference-list a:hover,
.reference-list a:focus-visible {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.reference-list small {
  grid-column: 2;
  color: var(--text-muted);
  font-size: 0.68rem;
}
.reference-note {
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 0.68rem;
}
.interpretation-writing {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.interpretation-loader {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  border: 2px solid var(--border-soft);
  border-top-color: var(--text-muted);
  border-radius: 50%;
  animation: interpretation-spin 0.8s linear infinite;
}
@keyframes interpretation-spin {
  to {
    transform: rotate(360deg);
  }
}
.process-detail {
  margin-top: 10px;
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-2);
}
.process-detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 0.85rem;
}
.process-detail-head strong {
  min-width: 0;
  overflow-wrap: anywhere;
}
.process-step-position {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 0.75rem;
  white-space: nowrap;
}
.process-chips {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
}
.process-chip-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}
.process-chips-label {
  color: var(--text-muted);
  font-size: 0.74rem;
}
.process-chip {
  padding: 2px 6px;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  color: var(--text-soft);
  font-size: 0.72rem;
}
.process-chip.is-warn {
  color: var(--warning, #a16207);
  border-color: rgba(180, 83, 9, 0.35);
}
.process-chip.is-fail {
  color: var(--danger);
  border-color: rgba(var(--danger-rgb), 0.35);
}
.process-facts {
  display: grid;
  gap: 5px;
  margin: 0;
  font-size: 0.8rem;
}
.process-facts div {
  display: grid;
  grid-template-columns: minmax(84px, 0.34fr) minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
}
.process-facts dt {
  color: var(--text-muted);
}
.process-facts dd {
  margin: 0;
  color: var(--text-strong);
  overflow-wrap: anywhere;
}
.process-status {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 9px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-soft);
  font-size: 0.74rem;
  font-weight: 750;
}
.process-status.is-accepted {
  border-color: rgba(var(--success-rgb), 0.36);
  background: rgba(var(--success-rgb), 0.1);
  color: var(--success);
}
.process-status.is-investigate {
  border-color: rgba(161, 98, 7, 0.3);
  background: rgba(161, 98, 7, 0.08);
  color: var(--warning, #a16207);
}
.process-status.is-abstained {
  border-color: rgba(var(--danger-rgb), 0.24);
  background: var(--danger-soft);
  color: var(--danger);
}
.process-columns {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(280px, 0.92fr);
  align-items: start;
  gap: 0;
}
.process-timeline {
  min-width: 0;
  display: grid;
  margin: 0 0 6px;
  padding: 0 14px 0 0;
  list-style: none;
}
.process-side {
  min-width: 0;
  padding: 0 0 0 14px;
  border-left: 1px solid var(--border-soft);
}
.process-side .process-detail {
  margin-top: 0;
}
.process-step {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 9px;
  min-width: 0;
  padding: 4px 0;
}
.process-step:not(:last-child)::before {
  content: "";
  position: absolute;
  top: 15px;
  bottom: -5px;
  left: 6px;
  width: 1px;
  background: var(--border-soft);
}
.process-step-button {
  position: relative;
  z-index: 1;
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 9px;
  width: 100%;
  padding: 2px 4px 2px 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.process-step-button:hover,
.process-step-button:focus-visible,
.process-step-button.is-selected {
  background: rgba(var(--accent-rgb), 0.07);
  outline: none;
}
.process-step-button.is-selected .process-step-copy strong {
  color: var(--accent-strong);
}
.process-marker {
  position: relative;
  z-index: 1;
  width: 7px;
  height: 7px;
  margin: 5px 0 0 3px;
  border: 2px solid var(--surface-1);
  border-radius: 50%;
  background: var(--text-muted);
  box-shadow: 0 0 0 1px var(--border-soft);
}
.process-marker.is-passed {
  background: var(--success);
}
.process-marker.is-warning {
  background: var(--warning, #a16207);
}
.process-marker.is-failed {
  background: var(--danger);
}
.process-step-copy {
  display: grid;
  grid-template-columns: minmax(130px, 0.32fr) minmax(0, 1fr);
  gap: 11px;
  min-width: 0;
  line-height: 1.4;
}
.process-step-copy strong {
  color: var(--text-soft);
  font-size: 0.78rem;
  font-weight: 650;
}
.process-step-copy span {
  min-width: 0;
  color: var(--text-muted);
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}
.technical-details {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border-soft);
}
.technical-details summary {
  cursor: pointer;
  color: var(--text-soft);
  font-size: 0.8rem;
  font-weight: 700;
}
.technical-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
  margin-top: 10px;
}
.technical-group {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.technical-label {
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 650;
}
.technical-group code {
  min-width: 0;
  color: var(--text-strong);
  font-size: 0.76rem;
  overflow-wrap: anywhere;
}
.process-actions {
  margin-top: 12px;
}
.process-subhead {
  display: block;
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 0.78rem;
}
.process-action-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.process-action-table th,
.process-action-table td {
  padding: 4px 6px;
  border-bottom: 1px solid var(--border-soft);
  text-align: left;
  vertical-align: top;
}
.process-action-table th {
  color: var(--text-muted);
  font-weight: 600;
  white-space: nowrap;
}
.process-action-table td {
  color: var(--text-soft);
  overflow-wrap: anywhere;
}
.process-cost {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
  color: var(--text-muted);
  font-size: 0.78rem;
}
.abc-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 286px;
  border: 1px solid var(--border-soft);
  background: var(--surface-1);
  color: var(--text-strong);
  border-radius: 8px;
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
}
.abc-card.selected {
  border-color: rgba(var(--success-rgb), 0.72);
  box-shadow: 0 0 0 2px rgba(var(--success-rgb), 0.14);
}
.abc-card.disabled {
  cursor: not-allowed;
  opacity: 0.72;
}
.abc-card-text {
  display: grid;
  gap: 4px;
}
.abc-choice {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.abc-choice input {
  width: 18px;
  height: 18px;
}
.abc-card strong {
  font-size: 1.6rem;
}
.abc-card span {
  color: var(--text-muted);
}
.abc-card-radial {
  width: 100%;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.abc-card-visual {
  position: relative;
  flex: 1;
  display: flex;
  min-height: 190px;
}
:deep(.pair-warning) {
  margin: 0;
  color: var(--danger) !important;
  font-size: 0.78rem;
  font-weight: 700;
  text-align: center;
}
.abc-card-visual > .pair-warning {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 4px;
}
.edit-badge {
  position: absolute;
  right: 10px;
  top: 10px;
  border: 1px solid rgba(var(--success-rgb), 0.6);
  background: rgba(var(--success-rgb), 0.14);
  color: var(--text-strong);
  border-radius: 999px;
  padding: 4px 9px;
  font-size: 0.78rem;
  font-weight: 700;
}
:deep(.abc-card-radial .trna-container) {
  width: 100%;
}
.abc-empty {
  flex: 1;
  display: grid;
  place-items: center;
  min-height: 190px;
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-weight: 700;
}
.error-line,
.success-line {
  margin: 10px 0;
  padding: 9px 10px;
  border-radius: 8px;
}
.error-line {
  color: var(--danger);
  background: rgba(180, 35, 24, 0.08);
}
.success-line {
  color: var(--success);
  background: rgba(var(--success-rgb), 0.1);
}
.server-draft-note {
  display: grid;
  gap: 3px;
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid rgba(var(--info-rgb, 59, 130, 246), 0.38);
  border-radius: 7px;
  background: rgba(var(--info-rgb, 59, 130, 246), 0.08);
  color: var(--text-strong);
  font-size: 0.86rem;
  line-height: 1.45;
}
.server-draft-note span {
  color: var(--text-muted);
}
.c-editor-drawer {
  width: 100%;
  min-width: 0;
  flex: 1 1 auto;
  min-height: 0;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: var(--surface-2);
  box-shadow: var(--shadow-soft);
  padding: 12px;
  /* The drawer now stacks head, measures, tools and rows, so the column grows
     by itself and only the residue list takes the leftover height. */
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.c-editor-drawer.is-empty {
  flex: 0 1 auto;
}
.editor-empty-state {
  min-height: 0;
  flex: 1 1 auto;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
  color: var(--text-muted);
}
.editor-empty-state strong {
  color: var(--text-strong);
  font-size: 0.98rem;
}
.editor-empty-state p {
  max-width: 220px;
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.55;
}
.editor-empty-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 1px dashed var(--border-soft);
  border-radius: 8px;
  color: var(--text-muted);
  font-family: Georgia, serif;
  font-size: 1.25rem;
  font-weight: 700;
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.drawer-head span {
  color: var(--text-muted);
  font-size: 0.84rem;
}
.drawer-tools {
  display: flex;
  gap: 8px;
}
.common-fix-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.common-fix-btn {
  border: 1px solid rgba(var(--info-rgb, 59, 130, 246), 0.4);
  background: rgba(var(--info-rgb, 59, 130, 246), 0.1);
  color: var(--text-strong);
  border-radius: 999px;
  padding: 4px 9px;
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 700;
}
.drawer-list {
  overflow: auto;
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 6px;
  padding-right: 2px;
}
.drawer-row {
  display: grid;
  grid-template-columns: 48px 32px 58px 58px 72px;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  background: var(--surface-1);
  padding: 6px;
  font-size: 0.82rem;
  cursor: pointer;
}
.drawer-row.active {
  border-color: rgba(var(--success-rgb), 0.75);
  background: rgba(var(--success-rgb), 0.12);
  box-shadow: inset 3px 0 0 rgba(var(--success-rgb), 0.85);
}
.drawer-row.changed {
  border-color: rgba(245, 158, 11, 0.32);
}
.drawer-row.invalid {
  border-color: rgba(180, 35, 24, 0.72);
  background: rgba(180, 35, 24, 0.06);
}
.drawer-suggestions {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.suggestion-chip {
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
  color: var(--text-muted);
  border-radius: 999px;
  padding: 2px 7px;
  font-family: Consolas, monospace;
  font-size: 0.74rem;
  cursor: pointer;
}
.suggestion-chip.chosen {
  border-color: rgba(var(--success-rgb), 0.72);
  background: rgba(var(--success-rgb), 0.14);
  color: var(--text-strong);
  font-weight: 700;
}
.drawer-error {
  grid-column: 1 / -1;
  color: var(--danger);
  font-size: 0.75rem;
  font-weight: 700;
}
.drawer-index {
  font-weight: 700;
}
.drawer-base {
  font-family: Consolas, monospace;
  text-align: center;
}
.drawer-ab {
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 1180px) {
  .work-strip {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      "title"
      "filter";
    align-items: stretch;
    min-height: 0;
    padding: 9px 16px;
  }
  .queue-title {
    min-width: 0;
    flex-wrap: wrap;
    white-space: normal;
  }
  .queue-side {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .abc-card-grid {
    grid-template-columns: 1fr;
  }
  .workbench-grid {
    grid-template-columns: 1fr;
  }
  .review-pane {
    grid-column: auto;
  }
  .lookup-input {
    width: 100%;
    max-width: none;
    min-width: 0;
  }
  .lookup-field {
    flex: 1 1 180px;
  }
  .side-column {
    position: static;
    height: auto;
  }
  .c-editor-drawer {
    margin-top: 16px;
    height: 560px;
    flex: 0 0 auto;
  }
  .c-editor-drawer.is-empty {
    height: 340px;
  }
}
@media (max-width: 720px) {
  .process-columns {
    grid-template-columns: 1fr;
  }
  .process-timeline {
    padding-right: 0;
  }
  .process-side {
    margin-top: 12px;
    padding-left: 0;
    border-left: 0;
  }
}
</style>
