<!-- src/components/ResultsPanel.vue -->
<template>
  <div>
    <!-- ==== 汇总下载按钮（仅有 ≥2 条成功结果时显示） ==== -->
    <div v-if="successResults.length > 1" class="merge-row">
      <button class="btn-download-big" @click="downloadMerged">
        ⬇︎ Download All ({{ successResults.length }} seqs)
      </button>
    </div>

    <div v-if="successResults.length > 1" class="viz-toggle">
      <button
        type="button"
        class="viz-btn"
        :class="{ active: vizMode === 'heatmap' }"
        @click="vizMode = 'heatmap'"
      >
        Heatmap
      </button>
      <button
        type="button"
        class="viz-btn"
        :class="{ active: vizMode === 'tree' }"
        @click="vizMode = 'tree'"
      >
        Tree
      </button>
    </div>

    <div v-if="successResults.length > 1">
      <div v-if="vizMode === 'heatmap'">
        <AlignmentTreeHeatmap
          :key="successResults.length"
          :ids="heatmapData.ids"
          :rows="heatmapData.rows"
          :reference="heatmapReference"
          :reference-mode="referenceMode"
          :show-color-toggle="referenceMode === 'single'"
          :row-references="heatmapRowReferences"
          v-model:colorScheme="heatmapScheme"
          :tree-newick="treeNewick"
          :tree-error="treeError"
          :cell-size="20"
        />
      </div>
      <div v-else>
        <div v-if="treeLoading" class="tree-loading">Building tree…</div>
        <div v-else-if="treeNewick" class="tree-panel">
          <PhyloTree ref="treeRef" :newick="treeNewick" :height="520" />
        </div>
        <div v-else-if="treeError" class="tree-error">
          <span>{{ treeError }}</span>
          <button type="button" class="btn-secondary" @click="retryTree">
            Retry
          </button>
        </div>
      </div>
    </div>

    <details class="select-panel">
      <summary>
        Show sequences ({{ selectedCount }}/{{ totalCount }})
      </summary>
      <div class="select-actions">
        <button type="button" class="select-btn" @click="selectAll">
          Select all
        </button>
        <button type="button" class="select-btn" @click="selectNone">
          Select none
        </button>
      </div>
      <div class="select-list">
        <label
          v-for="item in resultItems"
          :key="item.idx"
          class="select-item"
        >
          <input
            type="checkbox"
            :checked="selectedIndices.has(item.idx)"
            @change="toggleSelection(item.idx)"
          />
          <span
            class="select-label"
            @mouseenter="showNameTooltip(getSeqLabel(item.res, item.idx), $event)"
            @mousemove="showNameTooltip(getSeqLabel(item.res, item.idx), $event)"
            @mouseleave="hideNameTooltip"
          >
            {{ getSeqLabel(item.res, item.idx) }}
          </span>
          <span v-if="item.res.error" class="select-error">Error</span>
          <span
            v-else-if="(item.res.warnings || []).length"
            class="select-warn"
            :title="formatWarnings(item.res)"
          >
            Warning
          </span>
        </label>
      </div>
    </details>

    <div v-if="selectedResults.length" class="page-controls">
      <label class="page-size">
        Per page
        <select v-model.number="pageSize" class="page-size-select">
          <option v-for="size in pageSizeOptions" :key="size" :value="size">
            {{ size }}
          </option>
        </select>
      </label>
      <label v-if="hasSelectedReference" class="row-toggle">
        <input
          v-model="showOriginRow"
          type="checkbox"
          aria-label="Compare with reference"
        />
        <span>Compare with reference</span>
      </label>
      <button
        v-if="hasSelectedRunDetails"
        type="button"
        class="master-details-button"
        :class="`is-${masterDetailsState}`"
        :title="masterDetailsLabel"
        :aria-label="masterDetailsLabel"
        @click="toggleAllRunDetails"
      >
        <svg
          v-if="masterDetailsState === 'all'"
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m17 11-5-5-5 5" />
          <path d="m17 18-5-5-5 5" />
        </svg>
        <svg
          v-else-if="masterDetailsState === 'partial'"
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        >
          <path d="M6 12h12" />
        </svg>
        <svg
          v-else
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m7 6 5 5 5-5" />
          <path d="m7 13 5 5 5-5" />
        </svg>
      </button>
      <button
        type="button"
        class="page-btn"
        :disabled="currentPage === 1"
        @click="prevPage"
      >
        Prev
      </button>
      <span class="page-info">
        Page {{ currentPage }} / {{ totalPages }}
      </span>
      <button
        type="button"
        class="page-btn"
        :disabled="currentPage === totalPages"
        @click="nextPage"
      >
        Next
      </button>
    </div>
    <div v-else class="empty-hint">No sequences selected.</div>

    <!-- ==== 单条结果循环 ==== -->
    <div
      v-for="item in pagedResults"
      :key="item.idx"
      class="result-block"
    >
      <template v-if="item.res.pending">
        <div class="live-result-block">
          <div class="title-row">
            <div class="title-copy">
              <h3 class="res-title">{{ getSeqLabel(item.res, item.idx) }}</h3>
            </div>
            <div class="live-state-copy">
              <span class="process-status is-neutral">{{ liveStateLabel(item.res) }}</span>
              <small v-if="item.res.run_process?.stage">{{ processStageLabel(item.res.run_process.stage) }}</small>
            </div>
          </div>
          <div class="run-details-panel live-run-panel">
            <section class="step-inspector" aria-live="polite">
              <template v-if="selectedRunEvent(item.idx, item.res)">
                <strong>{{ processStageLabel(selectedRunEvent(item.idx, item.res)?.stage) }}</strong>
                <p>{{ processEventDetail(selectedRunEvent(item.idx, item.res)!) || detailText("waitingForDetails") }}</p>
                <pre v-if="processEventContent(selectedRunEvent(item.idx, item.res)!)" class="llm-response-stream">{{ processEventContent(selectedRunEvent(item.idx, item.res)!) }}</pre>
                <div v-if="processEventFacts(selectedRunEvent(item.idx, item.res)!).length" class="step-facts">
                  <span v-for="fact in processEventFacts(selectedRunEvent(item.idx, item.res)!)" :key="fact">{{ fact }}</span>
                </div>
              </template>
              <span v-else>{{ detailText("waitingForDetails") }}</span>
            </section>
            <section class="agent-process live-process">
              <strong>{{ detailText("liveProcess") }}</strong>
              <ol class="process-timeline">
                <li
                  v-for="(event, eventIndex) in runProcessEvents(item.res)"
                  :key="`live-event-${item.idx}-${eventIndex}`"
                  class="process-step"
                  :class="{ 'is-selected': (selectedRunEventIndex[item.idx] ?? runProcessEvents(item.res).length - 1) === eventIndex }"
                >
                  <button type="button" class="process-step-button" @click="selectRunEvent(item.idx, eventIndex)">
                    <span class="process-marker"></span>
                    <span class="process-step-copy">
                      <strong>{{ processStageLabel(event.stage) }}</strong>
                      <span>{{ processEventDetail(event) }}</span>
                    </span>
                  </button>
                </li>
              </ol>
            </section>
          </div>
        </div>
      </template>

      <!-- ✨ 错误条目 -->
      <template v-else-if="item.res.error">
        <div class="live-result-block">
          <div class="title-row">
            <h3 class="res-title">{{ getSeqLabel(item.res, item.idx) }}</h3>
            <span class="process-status is-abstained">{{ liveStateLabel(item.res) }}</span>
          </div>
          <div class="error-card">{{ item.res.error }}</div>
          <button
            v-if="runProcessEvents(item.res).length"
            type="button"
            class="run-details-toggle"
            :aria-expanded="isRunDetailsExpanded(item.idx)"
            :aria-controls="`run-details-${item.idx}`"
            @click="toggleRunDetails(item.idx)"
          >{{ isRunDetailsExpanded(item.idx) ? detailText("hideDetails") : detailText("showDetails") }}</button>
          <div v-if="runProcessEvents(item.res).length && isRunDetailsExpanded(item.idx)" :id="`run-details-${item.idx}`" class="run-details-panel live-run-panel">
            <section class="step-inspector" aria-live="polite">
              <strong>{{ processStageLabel(selectedRunEvent(item.idx, item.res)?.stage) }}</strong>
              <p>{{ processEventDetail(selectedRunEvent(item.idx, item.res)!) || item.res.error }}</p>
            </section>
            <section class="agent-process live-process">
              <strong>{{ detailText("liveProcess") }}</strong>
              <ol class="process-timeline">
                <li v-for="(event, eventIndex) in orderedProcessEvents(item.res)" :key="`error-event-${item.idx}-${eventIndex}`" class="process-step" :class="{ 'is-selected': (selectedRunEventIndex[item.idx] ?? orderedProcessEvents(item.res).length - 1) === eventIndex }">
                  <button type="button" class="process-step-button" @click="selectRunEvent(item.idx, eventIndex)">
                    <span class="process-marker"></span>
                    <span class="process-step-copy"><strong>{{ processStageLabel(event.stage) }}</strong><span>{{ processEventDetail(event) }}</span></span>
                  </button>
                </li>
              </ol>
            </section>
          </div>
        </div>
      </template>

      <!-- ✨ 正常条目 -->
      <template v-else>
        <!-- 标题 + 下载按钮 -->
        <div class="title-row">
          <div class="title-copy">
            <h3 class="res-title">
              {{ getSeqLabel(item.res, item.idx) }}
            </h3>
            <div v-if="item.res.template_name" class="res-subtitle">
              {{ resultSourceLabel(item.res) }}: {{ item.res.template_name }}
            </div>
          </div>
          <div v-if="hasAlignment(item.res)" class="title-actions">
            <details
              class="download-menu"
              :ref="(el) => setDownloadMenuRef(item.idx, el as HTMLDetailsElement | null)"
            >
              <summary class="btn-download">⬇︎ Download</summary>
              <div class="download-menu-list">
                <button
                  type="button"
                  class="download-option"
                  :disabled="exportingKind[item.idx] === 'csv'"
                  @click="downloadResult(item.res, item.idx)"
                >
                  下载 CSV
                </button>
                <button
                  type="button"
                  class="download-option"
                  :disabled="exportingKind[item.idx] === 'table-png'"
                  @click="downloadTablePng(item.res, item.idx)"
                >
                  下载左侧编号表 PNG
                </button>
                <button
                  type="button"
                  class="download-option"
                  :disabled="exportingKind[item.idx] === 'structure-png'"
                  @click="downloadStructurePng(item.res, item.idx)"
                >
                  下载右侧结构图 PNG
                </button>
              </div>
            </details>
          </div>
        </div>

        <div v-if="(item.res.warnings || []).length" class="warning-card">
          <strong>Warning:</strong> {{ formatWarnings(item.res) }}
        </div>

        <div v-if="hasResultDetails(item.res)" class="run-details">
          <button
            type="button"
            class="run-details-toggle"
            :aria-expanded="isRunDetailsExpanded(item.idx)"
            :aria-controls="`run-details-${item.idx}`"
            @click="toggleRunDetails(item.idx)"
          >
        <span>{{ isRunDetailsExpanded(item.idx) ? detailText("hideDetails") : detailText("showDetails") }}</span>
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path v-if="isRunDetailsExpanded(item.idx)" d="m6 15 6-6 6 6" />
              <path v-else d="m6 9 6 6 6-6" />
            </svg>
          </button>

          <div
            v-if="isRunDetailsExpanded(item.idx)"
            :id="`run-details-${item.idx}`"
            class="run-details-panel"
          >
            <section v-if="selectedRunEvent(item.idx, item.res) && selectedRunEvent(item.idx, item.res)?.stage !== 'scientific_interpretation'" class="step-inspector detail-column" aria-live="polite">
              <div class="scientific-interpretation-head">
                <div class="detail-heading-copy">
                  <strong>{{ processStageLabel(selectedRunEvent(item.idx, item.res)?.stage) }}</strong>
                  <small>{{ detailText("selectedStep") }}</small>
                </div>
                <button type="button" class="interpretation-button" @click="delete selectedRunEventIndex[item.idx]">
                  {{ detailText("scientificInterpretation") }}
                </button>
              </div>
                <dl v-if="selectedRunEvent(item.idx, item.res)?.stage === 'input_classified'" class="input-metadata-list">
                  <template v-for="field in inputMetadataFields(selectedRunEvent(item.idx, item.res)!)" :key="field.label">
                    <dt>{{ field.label }}</dt>
                    <dd>{{ field.value }}</dd>
                  </template>
                </dl>
                <p v-else>{{ processEventDetail(selectedRunEvent(item.idx, item.res)!) }}</p>
              <div v-if="processEventFacts(selectedRunEvent(item.idx, item.res)!).length" class="step-facts">
                <span v-for="fact in processEventFacts(selectedRunEvent(item.idx, item.res)!)" :key="fact">{{ fact }}</span>
              </div>
            </section>
            <section v-else-if="hasAlignment(item.res)" class="scientific-interpretation detail-column">
              <div class="scientific-interpretation-head">
                <div class="detail-heading-copy">
                  <strong>{{ detailText("scientificInterpretation") }}</strong>
                  <small>{{ detailText("scientificSubtitle") }}</small>
                </div>
                <span
                  v-if="scientificInterpretations[item.idx]"
                  class="interpretation-disposition"
                  :class="interpretationDispositionClass(scientificInterpretations[item.idx].system_disposition?.code)"
                >
                  {{ dispositionLabel(scientificInterpretations[item.idx].system_disposition) }}
                </span>
                <span
                  v-else-if="scientificInterpretationLoading[item.idx]"
                  class="interpretation-status"
                >
                  {{ detailText("generating") }}
                </span>
              </div>

              <div v-if="scientificInterpretationErrors[item.idx]" class="interpretation-error">
                <span>{{ scientificInterpretationErrors[item.idx] }}</span>
                <button
                  type="button"
                  class="interpretation-button"
                  :disabled="scientificInterpretationLoading[item.idx]"
                  @click="retryScientificInterpretation(item.idx, item.res)"
                >
                  {{ detailText("retry") }}
                </button>
              </div>

              <div
                v-else-if="scientificInterpretationLoading[item.idx]"
                class="interpretation-loading"
              >
                {{ detailText("buildingInterpretation") }}
                <div v-if="scientificInterpretationStreams[item.idx]" class="interpretation-stream">
                  <span>{{ detailLanguage === "zh-CN" ? "实时回答" : "Live response" }}</span>
                  <p>{{ scientificInterpretationDraft(scientificInterpretationStreams[item.idx]) }}</p>
                </div>
              </div>

              <template v-else-if="scientificInterpretations[item.idx]">
                <section class="interpretation-section">
                  <h4>{{ detailText("sequenceAssessment") }}</h4>
                  <p>
                    {{ scientificInterpretations[item.idx].interpretation.sequence_assessment.text }}
                    <span class="evidence-citations">
                      <button
                        v-for="evidenceId in scientificInterpretations[item.idx].interpretation.sequence_assessment.evidence_ids"
                        :key="`assessment-citation-${item.idx}-${evidenceId}`"
                        type="button"
                        @click="focusEvidence(item.idx, evidenceId)"
                      >{{ evidenceId }}</button>
                    </span>
                  </p>
                </section>
                <section class="interpretation-section">
                  <h4>{{ detailText("structuralBasis") }}</h4>
                  <ul>
                    <li
                      v-for="(basis, basisIndex) in scientificInterpretations[item.idx].interpretation.structural_basis"
                      :key="`basis-${item.idx}-${basisIndex}`"
                    >
                      <span>{{ basis.text }}</span>
                      <span class="evidence-citations">
                        <button
                          v-for="evidenceId in basis.evidence_ids"
                          :key="`basis-citation-${item.idx}-${basisIndex}-${evidenceId}`"
                          type="button"
                          @click="focusEvidence(item.idx, evidenceId)"
                        >{{ evidenceId }}</button>
                      </span>
                    </li>
                  </ul>
                </section>
                <section
                  v-for="(attention, attentionIndex) in scientificInterpretations[item.idx].interpretation.attention_regions"
                  :key="`attention-${item.idx}-${attentionIndex}`"
                  class="interpretation-section interpretation-attention"
                >
                  <h4>{{ attention.title }}</h4>
                  <p>
                    {{ attention.text }}
                    <span class="evidence-citations">
                      <button
                        v-for="evidenceId in attention.evidence_ids"
                        :key="`attention-citation-${item.idx}-${attentionIndex}-${evidenceId}`"
                        type="button"
                        @click="focusEvidence(item.idx, evidenceId)"
                      >{{ evidenceId }}</button>
                    </span>
                  </p>
                  <div v-if="attention.coordinate_labels.length" class="interpretation-labels">
                    <span v-for="label in attention.coordinate_labels" :key="label">{{ label }}</span>
                  </div>
                </section>
                <div
                  v-if="(scientificInterpretations[item.idx].citations || []).length"
                  class="interpretation-references"
                >
                  <h4>{{ detailText("referencesHeading") }}</h4>
                  <ol>
                    <li
                      v-for="citation in scientificInterpretations[item.idx].citations"
                      :key="`reference-${item.idx}-${citation.citation_id}`"
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
                  <p class="reference-note">{{ detailText("referencesNote") }}</p>
                </div>
                <details class="interpretation-evidence">
                  <summary>
                    <span>{{ detailText("evidenceDetails") }}</span>
                    <small>{{ detailText("evidenceSubtitle") }}</small>
                  </summary>
                  <ul>
                    <li
                      v-for="evidence in scientificInterpretations[item.idx].evidence"
                      :key="evidence.id"
                      :id="evidenceDomId(item.idx, evidence.id)"
                      :class="{ 'is-focused': focusedEvidence[item.idx] === evidence.id }"
                    >
                      <div class="evidence-row-heading">
                        <strong>{{ evidence.id }}</strong>
                        <span>{{ evidence.text }}</span>
                      </div>
                      <div v-if="evidenceTokens(evidence).length" class="evidence-token-list">
                        <span
                          v-for="token in evidenceTokens(evidence)"
                          :key="`${evidence.id}-${token.label}`"
                          :class="`is-${token.kind}`"
                        >{{ token.label }}</span>
                      </div>
                      <small>{{ evidenceSourceLabel(evidence.source) }}</small>
                    </li>
                  </ul>
                </details>
              </template>
            </section>

            <section v-if="item.res.agent || runProcessEvents(item.res).length" class="agent-process detail-column">
              <div class="agent-process-summary">
                <span class="agent-process-summary-copy">
                  <strong>{{ item.res.agent ? `${detailText("agentProcess")} · ${routeLabel(item.res.agent.route)}` : detailText("liveProcess") }}</strong>
                  <small>{{ item.res.pending ? detailText("liveSubtitle") : detailText("agentSubtitle") }}</small>
                </span>
                <span
                  class="process-status"
                  :class="processStatusClass(processDisposition(item.res))"
                >
                  {{ processDispositionLabel(item.res) }}
                </span>
              </div>

              <div class="agent-process-body">
                <ol class="process-timeline">
                  <li
                  v-for="(event, eventIndex) in orderedProcessEvents(item.res)"
                    :key="`agent-event-${item.idx}-${eventIndex}`"
                    class="process-step"
                    :class="processEventClass(event)"
                  >
                    <button type="button" class="process-step-button" :class="{ 'is-selected': selectedRunEventIndex[item.idx] === eventIndex }" @click="selectRunEvent(item.idx, eventIndex, item.res)">
                    <span class="process-marker" :class="{ 'is-running': isCurrentProcessEvent(item.res, event, eventIndex) || event.stage === 'scientific_interpretation' && scientificInterpretationLoading[item.idx] }"></span>
                    <span class="process-step-copy">
                      <strong>{{ processStageLabel(event.stage) }}</strong>
                      <span>{{ processEventDetail(event) }}</span>
                      <span v-if="isCurrentProcessEvent(item.res, event, eventIndex) || event.stage === 'scientific_interpretation' && scientificInterpretationLoading[item.idx]" class="process-loader" aria-label="Running"></span>
                    </span>
                    </button>
                  </li>
                </ol>

                <details v-if="hasTechnicalProvenance(item.res)" class="technical-details">
                  <summary>{{ detailText("technicalProvenance") }}</summary>
                  <div class="technical-grid">
                    <div v-if="calibrationEntries(item.res).length" class="technical-group">
                      <span class="technical-label">{{ detailText("domainProbabilities") }}</span>
                      <div class="probability-list">
                        <span
                          v-for="entry in calibrationEntries(item.res)"
                          :key="entry.group"
                        >
                          {{ entry.group }} {{ formatProbability(entry.value) }}
                        </span>
                      </div>
                    </div>
                    <div v-if="calibrationDisposition(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("calibrationDecision") }}</span>
                      <code>{{ calibrationDisposition(item.res) }}</code>
                    </div>
                    <div v-if="numberingModelName(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("numberingModel") }}</span>
                      <code>{{ numberingModelName(item.res) }}</code>
                    </div>
                    <div v-if="numberingModelHash(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("modelHash") }}</span>
                      <code :title="numberingModelHash(item.res)">
                        {{ shortHash(numberingModelHash(item.res)) }}
                      </code>
                    </div>
                    <div v-if="cmScoreLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("cmObservation") }}</span>
                      <code>{{ cmScoreLabel(item.res) }}</code>
                    </div>
                    <div v-if="trnascanLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("trnascanMode") }}</span>
                      <code>{{ trnascanLabel(item.res) }}</code>
                    </div>
                    <div v-if="specialToolLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("specializedTool") }}</span>
                      <code>{{ specialToolLabel(item.res) }}</code>
                    </div>
                    <div v-if="canonicalLikeEvidenceLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("canonicalGate") }}</span>
                      <code>{{ canonicalLikeEvidenceLabel(item.res) }}</code>
                    </div>
                    <div v-if="inputTransformLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("inputTransform") }}</span>
                      <code>{{ inputTransformLabel(item.res) }}</code>
                    </div>
                    <div v-if="scientificContractLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("candidateContract") }}</span>
                      <code>{{ scientificContractLabel(item.res) }}</code>
                    </div>
                    <div v-if="memoryAuditLabel(item.res)" class="technical-group">
                      <span class="technical-label">{{ detailText("experienceMemory") }}</span>
                      <code>{{ memoryAuditLabel(item.res) }}</code>
                    </div>
                  </div>
                </details>
                <details v-if="hasRegionalTrace(item.res)" class="trace-card">
                  <summary>{{ detailText("regionalValidation") }}</summary>
                  <div class="trace-header">
                    <span>{{ detailText("length") }}: {{ item.res.llm_trace?.sequence_length ?? "-" }}</span>
                    <span>
                      {{ detailText("regionalLlm") }}: {{ item.res.llm_trace?.run_llm ? "ON" : "OFF" }}
                    </span>
                  </div>
                  <details
                    v-for="(region, ri) in item.res.llm_trace?.regions || []"
                    :key="`trace-${item.idx}-${ri}`"
                    class="trace-region"
                  >
                    <summary class="trace-region-summary">
                      <span class="trace-region-name">
                        {{ region.region || `Region ${ri + 1}` }}
                      </span>
                      <span class="trace-region-meta">
                        <span
                          class="trace-status-chip"
                          :class="traceStatusClass(traceStatusValue(region))"
                        >
                          {{ traceStatusLabel(traceStatusValue(region)) }}
                        </span>
                        <span class="trace-attempt-chip">
                          {{ detailText("attempts") }} {{ traceAttemptCount(region) }}
                        </span>
                      </span>
                    </summary>
                    <div v-if="region.message" class="trace-message">
                      {{ region.message }}
                    </div>
                  </details>
                </details>
              </div>
            </section>
          </div>
        </div>

        <div v-if="!hasAlignment(item.res)" class="evidence-only-state">
          <strong>Sprinzl numbering not emitted</strong>
          <span>{{ specialRouteMessage(item.res) }}</span>
        </div>
        <details
          v-if="item.res.structure?.secondary_structure"
          class="structure-card"
        >
          <summary>
            Secondary structure
            <span class="structure-meta">
              ({{ item.res.structure.annotation_source || "unknown" }}, pairs:
              {{ item.res.structure.pair_count ?? 0 }})
            </span>
          </summary>
          <code class="structure-seq">{{ item.res.structure.secondary_structure }}</code>
        </details>

        <details v-if="isEditable" class="edit-panel">
          <summary>Edit alignment (insert/delete)</summary>
          <div v-if="ensureEditState(item.idx, item.res)" class="edit-grid">
            <div class="edit-row">
              <span class="edit-label">Insert after</span>
              <select
                v-model="ensureEditState(item.idx, item.res).insertAfter"
                class="edit-select"
              >
                <option
                  v-for="id in getInsertAnchors(item.res)"
                  :key="id"
                  :value="id"
                >
                  {{ id }}
                </option>
              </select>
              <input
                v-model="ensureEditState(item.idx, item.res).insertBase"
                class="edit-input"
                maxlength="1"
                placeholder="A/C/G/U/-"
              />
              <button
                type="button"
                class="edit-btn"
                @click="insertAfter(item.idx, item.res)"
              >
                Insert
              </button>
            </div>
            <div class="edit-row">
              <span class="edit-label">Set gap at</span>
              <select
                v-model="ensureEditState(item.idx, item.res).deleteId"
                class="edit-select"
              >
                <option v-for="id in getAllIds(item.res)" :key="id" :value="id">
                  {{ id }}
                </option>
              </select>
              <button
                type="button"
                class="edit-btn ghost"
                @click="setGap(item.idx, item.res)"
              >
                Set -
              </button>
            </div>
            <div class="edit-row">
              <span class="edit-label">Delete insertion (iN)</span>
              <select
                v-model="ensureEditState(item.idx, item.res).removeId"
                class="edit-select"
              >
                <option
                  v-for="id in getInsertionIds(item.res)"
                  :key="id"
                  :value="id"
                >
                  {{ id }}
                </option>
              </select>
              <button
                type="button"
                class="edit-btn ghost"
                @click="removeInsertion(item.idx, item.res)"
              >
                Remove
              </button>
            </div>
            <div class="edit-row">
              <span class="edit-label">Target base</span>
              <select
                v-model="ensureEditState(item.idx, item.res).editBase"
                class="edit-select"
              >
                <option v-for="base in BASE_OPTIONS" :key="base" :value="base">
                  {{ base }}
                </option>
              </select>
              <label class="edit-toggle">
                <input
                  type="checkbox"
                  v-model="ensureEditState(item.idx, item.res).clickEdit"
                />
                Click to edit
              </label>
              <small class="edit-note"
                >Click target nucleotides to apply the selected base.</small
              >
            </div>
            <div class="edit-row">
              <span class="edit-label">Re-align</span>
              <button
                type="button"
                class="edit-btn ghost"
                :disabled="realignLoading[item.idx]"
                @click="realignResult(item.idx, item.res)"
              >
                {{ realignLoading[item.idx] ? "Re-aligning…" : "Re-align sequence" }}
              </button>
              <small class="edit-note">
                Submit the edited sequence for a fresh alignment.
              </small>
            </div>
          </div>
          <div v-if="editMessages[item.idx]" class="edit-message">
            {{ editMessages[item.idx] }}
          </div>
          <div class="edit-hint">
            Insertions use parent+iN (e.g., 5i1, 5i2) and only affect this
            result.
          </div>
        </details>

        <div v-if="hasAlignment(item.res)" class="result-content">
          <!-- 按行类型渲染（ID/Template/Target），可隐藏Template -->
          <div
            class="alignment-pane"
            :ref="(el) => setTableExportRef(item.idx, el as HTMLElement | null)"
          >
            <AlignmentTable
              :alignment-data="buildNodes(item.res)"
              :show-origin="compareReferenceFor(item.res)"
              :color-mode="resultColorMode(item.res)"
              :editable="isEditable && ensureEditState(item.idx, item.res).clickEdit"
              @target-click="handleTargetClick(item.idx, item.res, $event)"
              class="alignment-tables"
            />
          </div>

          <!-- 径向示意图 -->
          <div
            class="radial-pane"
            :ref="(el) => setRadialExportRef(item.idx, el as HTMLElement | null)"
          >
            <sup-trna-radial
              class="radial"
              :data="buildNodes(item.res)"
              :pair-links="item.res.structure?.id_pairs || []"
              :color-mode="resultColorMode(item.res)"
              :editable="isEditable && ensureEditState(item.idx, item.res).clickEdit"
              @node-click="handleTargetClick(item.idx, item.res, $event)"
              :height="520"
            />
          </div>
        </div>

      </template>
    </div>

    <teleport to="body">
      <div
        v-if="nameTooltip.visible"
        class="name-tooltip"
        :style="{ left: `${nameTooltip.x}px`, top: `${nameTooltip.y}px` }"
      >
        {{ nameTooltip.text }}
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { toBlob } from "html-to-image";
import AlignmentTable from "@/components/AlignmentTable.vue";
import SupTrnaRadial from "@/components/supTrnaRadial.vue";
import PhyloTree from "@/components/PhyloTree.vue";
import AlignmentTreeHeatmap from "@/components/AlignmentTreeHeatmap.vue";
import { SPRINZL_POSITION_ORDER } from "@/components/layout/sprinzlLayout";
import { apiFetch } from "@/shared/api";
import type { RunProcessEvent, SequenceRunProcess as SequenceRunProcessState } from "../model/types";
import {
  asRecord,
  humanizeCode,
  inputMetadataFields as copyInputMetadataFields,
  processEventDetail as copyEventDetail,
  processStageLabel as copyStageLabel,
  routeLabel as copyRouteLabel,
  toolLabel as copyToolLabel,
  type ProcessEventLike,
} from "@/shared/runProcessCopy";

interface AlignmentResult {
  run_process?: SequenceRunProcessState;
  seq_name?: string;
  seq_index?: number;
  pending?: boolean;
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
  llm_trace?: LlmTracePayload;
  agent?: AgentProcess;
  scientific_provenance?: ScientificProvenance;
  error?: string;
}

interface AgentTraceEvent {
  stage?: string;
  reason?: string;
  route?: string;
  status?: string;
  disposition?: string;
  tool?: string;
  tool_name?: string;
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

interface AgentProcess {
  route?: string;
  hard_guard_passed?: boolean | null;
  trace_events?: AgentTraceEvent[];
  work_state_summary?: Record<string, unknown>;
  [key: string]: unknown;
}

interface ScientificProvenance {
  numbering_backend?: string | null;
  numbering_model?: Record<string, unknown>;
  cm_score?: Record<string, unknown>;
  cm_portfolio?: Record<string, unknown>;
  cm_tool?: Record<string, unknown>;
  specialized_route?: Record<string, unknown>;
  input_transform?: Record<string, unknown>;
  boundary_refinement_contract?: Record<string, unknown>;
  regional_candidate_contract?: Record<string, unknown>;
  structural_region_evidence?: Record<string, unknown>;
  variable_bridge_evidence?: Record<string, unknown>;
  variable_boundary_reconciliation?: Record<string, unknown>;
  acceptor_tail_reconciliation?: Record<string, unknown>;
  [key: string]: unknown;
}

interface ScientificEvidence {
  id: string;
  code?: string;
  text: string;
  source: string;
  data?: Record<string, unknown>;
}

interface ScientificCitation {
  citation_id: string;
  source: string;
  title: string;
  authors?: string[];
  year?: string;
  venue?: string;
  doi?: string;
  pmid?: string;
  page?: number;
  category?: string;
  url?: string;
  snippet?: string;
  cited_by_count?: number;
}

interface ScientificInterpretation {
  system_disposition?: { code?: string; label?: string };
  interpretation: {
    sequence_assessment: { text: string; evidence_ids: string[] };
    structural_basis: Array<{ text: string; evidence_ids: string[] }>;
    attention_regions: Array<{
      region: string;
      title: string;
      text: string;
      coordinate_labels: string[];
      evidence_ids: string[];
    }>;
  };
  evidence: ScientificEvidence[];
  citations?: ScientificCitation[];
}

interface LlmTraceRegion {
  region?: string;
  status?: string;
  raw_status?: string;
  final_status?: string;
  message?: string;
  system_prompt?: string;
  user_prompt?: string;
  attempts?: number;
  llm_attempts?: unknown[];
  [key: string]: unknown;
}

interface LlmTracePayload {
  ok?: boolean;
  template_name?: string;
  sequence_length?: number;
  run_llm?: boolean;
  warnings?: string[];
  regions?: LlmTraceRegion[];
  [key: string]: unknown;
}

type StructurePair = {
  left_id: string;
  right_id: string;
  left_index?: number;
  right_index?: number;
};

type InterpretationLanguage = "en" | "zh-CN" | "de" | "fr";
type EvidenceToken = { label: string; kind: "coordinate" | "pair" | "metric" | "tool" };

const DETAIL_COPY = {
  en: {
    showDetails: "Show run details",
    hideDetails: "Hide run details",
    expandAll: "Expand all run details",
    collapseAll: "Collapse all run details",
    scientificInterpretation: "Scientific interpretation",
    scientificSubtitle: "Narrative grounded in the evidence below",
    generating: "Generating...",
    retry: "Retry",
    buildingInterpretation: "Building an evidence-grounded interpretation for this sequence.",
    sequenceAssessment: "Sequence assessment",
    structuralBasis: "Structural and numbering basis",
    evidenceDetails: "Tool & rule evidence",
    evidenceSubtitle: "Deterministic observations cited by the narrative",
    referencesHeading: "References",
    referencesNote: "Selected from what this run recorded; not used to write the text above.",
    referenceSourceLocal: "project library",
    referenceSourcePublic: "public index",
    agentProcess: "Agent process",
    agentSubtitle: "Recorded tool choices and validation steps",
    technicalProvenance: "Technical provenance",
    domainProbabilities: "Calibrated domain probabilities",
    calibrationDecision: "Calibration decision",
    numberingModel: "Numbering model",
    modelHash: "Model SHA-256",
    cmObservation: "CM observation",
    trnascanMode: "tRNAscan-SE mode",
    specializedTool: "Specialized tool",
    canonicalGate: "Canonical-like structure gate",
    inputTransform: "Input transform",
    candidateContract: "Candidate contract",
    experienceMemory: "Experience memory",
    regionalValidation: "Regional validation status",
    length: "Length",
    regionalLlm: "Regional LLM",
    attempts: "Attempts",
    liveProcess: "Live run process",
    liveSubtitle: "Events update as this sequence runs",
    waitingForDetails: "Waiting for the first sequence-specific event.",
    selectedStep: "Evidence and observations for the selected step",
    noStepDetail: "No additional event details were recorded.",
  },
  "zh-CN": {
    showDetails: "查看运行详情",
    hideDetails: "收起运行详情",
    expandAll: "展开全部运行详情",
    collapseAll: "收起全部运行详情",
    scientificInterpretation: "科学解释",
    scientificSubtitle: "基于下方证据生成的说明",
    generating: "正在生成...",
    retry: "重试",
    buildingInterpretation: "正在为该序列生成基于证据的解释。",
    sequenceAssessment: "序列整体判读",
    structuralBasis: "结构与编号依据",
    evidenceDetails: "工具与规则证据",
    evidenceSubtitle: "解释中引用的确定性观察",
    referencesHeading: "参考文献",
    referencesNote: "按本次运行记录到的观测检索，未用于生成上面的文字。",
    referenceSourceLocal: "项目文献库",
    referenceSourcePublic: "公开索引",
    agentProcess: "智能体过程",
    agentSubtitle: "已记录的工具选择与验证步骤",
    technicalProvenance: "技术溯源",
    domainProbabilities: "校准后的生物域概率",
    calibrationDecision: "校准结论",
    numberingModel: "编号模型",
    modelHash: "模型 SHA-256",
    cmObservation: "CM 观察",
    trnascanMode: "tRNAscan-SE 模式",
    specializedTool: "专用工具",
    canonicalGate: "经典结构检查",
    inputTransform: "输入转换",
    candidateContract: "候选结果约束",
    experienceMemory: "经验记忆",
    regionalValidation: "区域验证状态",
    length: "长度",
    regionalLlm: "区域语言模型",
    attempts: "尝试次数",
    liveProcess: "实时运行过程",
    liveSubtitle: "序列运行时持续更新事件",
    waitingForDetails: "等待该序列的首条运行事件。",
    selectedStep: "当前步骤对应的证据与观察",
    noStepDetail: "该步骤没有记录更多细节。",
  },
  de: {
    showDetails: "Laufdetails anzeigen",
    hideDetails: "Laufdetails ausblenden",
    expandAll: "Alle Laufdetails öffnen",
    collapseAll: "Alle Laufdetails schließen",
    scientificInterpretation: "Wissenschaftliche Interpretation",
    scientificSubtitle: "Erklärung auf Grundlage der unten stehenden Evidenz",
    generating: "Wird erzeugt...",
    retry: "Erneut versuchen",
    buildingInterpretation: "Eine evidenzbasierte Interpretation wird erstellt.",
    sequenceAssessment: "Sequenzbeurteilung",
    structuralBasis: "Struktur- und Nummerierungsgrundlage",
    evidenceDetails: "Werkzeug- und Regelevidenz",
    evidenceSubtitle: "Deterministische Beobachtungen, die im Text zitiert werden",
    referencesHeading: "Literatur",
    referencesNote: "Aus den aufgezeichneten Beobachtungen gesucht; nicht für den Text oben verwendet.",
    referenceSourceLocal: "Projektbibliothek",
    referenceSourcePublic: "öffentlicher Index",
    agentProcess: "Agentenprozess",
    agentSubtitle: "Protokollierte Werkzeugauswahl und Validierung",
    technicalProvenance: "Technische Provenienz",
    domainProbabilities: "Kalibrierte Domänenwahrscheinlichkeiten",
    calibrationDecision: "Kalibrierungsentscheidung",
    numberingModel: "Nummerierungsmodell",
    modelHash: "Modell-SHA-256",
    cmObservation: "CM-Beobachtung",
    trnascanMode: "tRNAscan-SE-Modus",
    specializedTool: "Spezialwerkzeug",
    canonicalGate: "Prüfung der kanonischen Struktur",
    inputTransform: "Eingabetransformation",
    candidateContract: "Kandidatenvertrag",
    experienceMemory: "Erfahrungsspeicher",
    regionalValidation: "Regionale Validierung",
    length: "Länge",
    regionalLlm: "Regionales LLM",
    attempts: "Versuche",
    liveProcess: "Live-Ablauf",
    liveSubtitle: "Ereignisse werden während der Sequenz aktualisiert",
    waitingForDetails: "Warten auf das erste sequenzspezifische Ereignis.",
    selectedStep: "Evidenz und Beobachtungen für diesen Schritt",
    noStepDetail: "Für diesen Schritt wurden keine weiteren Details erfasst.",
  },
  fr: {
    showDetails: "Afficher les détails",
    hideDetails: "Masquer les détails",
    expandAll: "Développer tous les détails",
    collapseAll: "Réduire tous les détails",
    scientificInterpretation: "Interprétation scientifique",
    scientificSubtitle: "Texte fondé sur les preuves ci-dessous",
    generating: "Génération...",
    retry: "Réessayer",
    buildingInterpretation: "Création d'une interprétation fondée sur les preuves.",
    sequenceAssessment: "Évaluation de la séquence",
    structuralBasis: "Fondement structural et de numérotation",
    evidenceDetails: "Preuves des outils et des règles",
    evidenceSubtitle: "Observations déterministes citées dans le texte",
    referencesHeading: "Références",
    referencesNote: "Choisies d’après les observations enregistrées ; non utilisées pour rédiger le texte ci-dessus.",
    referenceSourceLocal: "bibliothèque du projet",
    referenceSourcePublic: "index public",
    agentProcess: "Processus de l'agent",
    agentSubtitle: "Choix d'outils et étapes de validation enregistrés",
    technicalProvenance: "Provenance technique",
    domainProbabilities: "Probabilités de domaine calibrées",
    calibrationDecision: "Décision de calibration",
    numberingModel: "Modèle de numérotation",
    modelHash: "SHA-256 du modèle",
    cmObservation: "Observation CM",
    trnascanMode: "Mode tRNAscan-SE",
    specializedTool: "Outil spécialisé",
    canonicalGate: "Contrôle de structure canonique",
    inputTransform: "Transformation de l'entrée",
    candidateContract: "Contrat du candidat",
    experienceMemory: "Mémoire d'expérience",
    regionalValidation: "Validation régionale",
    length: "Longueur",
    regionalLlm: "LLM régional",
    attempts: "Tentatives",
    liveProcess: "Processus en direct",
    liveSubtitle: "Les événements sont mis à jour pendant l'analyse",
    waitingForDetails: "En attente du premier événement de cette séquence.",
    selectedStep: "Éléments et observations de cette étape",
    noStepDetail: "Aucun détail supplémentaire n'a été enregistré.",
  },
} as const;

type DetailCopyKey = keyof typeof DETAIL_COPY.en;

const props = withDefaults(defineProps<{
  results: AlignmentResult[];
  editable?: boolean;
  referenceMode?: "single" | "per-row" | "none";
  interpretationLanguage?: InterpretationLanguage;
}>(), {
  interpretationLanguage: "en",
});

type EditState = {
  insertAfter: string;
  insertBase: string;
  deleteId: string;
  removeId: string;
  editBase: string;
  clickEdit: boolean;
};

const pageSize = ref(8);
const pageSizeOptions = [4, 8, 12, 20, 30];
const currentPage = ref(1);
const showOriginRow = ref(false);
const expandedDetailIndices = ref<Set<number>>(new Set());
const selectedRunEventIndex = reactive<Record<number, number>>({});
const selectedIndices = ref<Set<number>>(new Set());
const nameTooltip = ref({ visible: false, text: "", x: 0, y: 0 });

const editableResults = ref<AlignmentResult[]>([]);
const editState = reactive<Record<number, EditState>>({});
const editMessages = reactive<Record<number, string>>({});
const realignLoading = reactive<Record<number, boolean>>({});
const downloadMenuRefs = reactive<Record<number, HTMLDetailsElement | null>>({});
const tableExportRefs = reactive<Record<number, HTMLElement | null>>({});
const radialExportRefs = reactive<Record<number, HTMLElement | null>>({});
const exportingKind = reactive<Record<number, "" | "csv" | "table-png" | "structure-png">>({});
const scientificInterpretations = reactive<Record<number, ScientificInterpretation>>({});
const scientificInterpretationLoading = reactive<Record<number, boolean>>({});
const scientificInterpretationErrors = reactive<Record<number, string>>({});
const scientificInterpretationStreams = reactive<Record<number, string>>({});
const scientificInterpretationSignatures = reactive<Record<number, string>>({});
const scientificInterpretationCache = new Map<string, ScientificInterpretation>();
const focusedEvidence = reactive<Record<number, string>>({});
const interpretationQueue: Array<{ idx: number; signature: string }> = [];
const queuedInterpretations = new Set<string>();
const interpretationControllers = new Map<string, AbortController>();
const INTERPRETATION_CONCURRENCY = 2;
let activeInterpretations = 0;
let componentDisposed = false;
let lastResultsSource: AlignmentResult[] | null = null;
const previousPendingState = new Map<number, boolean>();
const isEditable = computed(() => props.editable !== false);
const BASE_OPTIONS = ["A", "C", "G", "U", "-"];

const referenceMode = computed(() => props.referenceMode ?? "single");
const detailLanguage = computed<InterpretationLanguage>(() => props.interpretationLanguage);

function detailText(key: DetailCopyKey): string {
  return DETAIL_COPY[detailLanguage.value][key];
}

const resultItems = computed(() =>
  editableResults.value.map((res, idx) => ({ res, idx }))
);

const selectedResults = computed(() =>
  resultItems.value.filter((item) => selectedIndices.value.has(item.idx))
);
const hasSelectedReference = computed(() =>
  selectedResults.value.some((item) => hasReferenceBaseline(item.res))
);
const hasSelectedRunDetails = computed(() =>
  selectedResults.value.some((item) => hasResultDetails(item.res))
);
const selectedDetailIndices = computed(() =>
  selectedResults.value
    .filter((item) => hasResultDetails(item.res))
    .map((item) => item.idx)
);
const masterDetailsState = computed<"none" | "partial" | "all">(() => {
  const eligible = selectedDetailIndices.value;
  if (!eligible.length) return "none";
  const expandedCount = eligible.filter((idx) => expandedDetailIndices.value.has(idx)).length;
  if (!expandedCount) return "none";
  return expandedCount === eligible.length ? "all" : "partial";
});
const masterDetailsLabel = computed(() =>
  masterDetailsState.value === "all" ? detailText("collapseAll") : detailText("expandAll")
);

const totalCount = computed(() => resultItems.value.length);
const selectedCount = computed(() => selectedIndices.value.size);

const totalPages = computed(() =>
  Math.max(1, Math.ceil(selectedResults.value.length / pageSize.value))
);

const pagedResults = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return selectedResults.value.slice(start, start + pageSize.value);
});

function selectAll() {
  selectedIndices.value = new Set(resultItems.value.map((item) => item.idx));
  currentPage.value = 1;
}

function selectNone() {
  selectedIndices.value = new Set();
  currentPage.value = 1;
}

function toggleSelection(idx: number) {
  const next = new Set(selectedIndices.value);
  if (next.has(idx)) {
    next.delete(idx);
  } else {
    next.add(idx);
  }
  selectedIndices.value = next;
  if (currentPage.value > totalPages.value) currentPage.value = 1;
}

function hasResultDetails(res: AlignmentResult): boolean {
  return !res.pending && !res.error && (hasAlignment(res) || Boolean(res.agent));
}

function liveStateLabel(res: AlignmentResult): string {
  const state = res.run_process?.state || (res.pending ? "running" : "error");
  const labels: Record<InterpretationLanguage, Record<string, string>> = {
    en: { queued: "Queued", running: "Running", completed: "Completed", error: "Failed" },
    "zh-CN": { queued: "排队中", running: "运行中", completed: "已完成", error: "失败" },
    de: { queued: "Wartet", running: "Läuft", completed: "Abgeschlossen", error: "Fehlgeschlagen" },
    fr: { queued: "En attente", running: "En cours", completed: "Terminé", error: "Échec" },
  };
  return labels[detailLanguage.value][state] || state;
}

function isRunDetailsExpanded(idx: number): boolean {
  return expandedDetailIndices.value.has(idx);
}

function toggleRunDetails(idx: number) {
  const next = new Set(expandedDetailIndices.value);
  if (next.has(idx)) {
    next.delete(idx);
    delete selectedRunEventIndex[idx];
  } else next.add(idx);
  expandedDetailIndices.value = next;
}

function toggleAllRunDetails() {
  const next = new Set(expandedDetailIndices.value);
  if (masterDetailsState.value === "all") {
    selectedDetailIndices.value.forEach((idx) => next.delete(idx));
  } else {
    selectedDetailIndices.value.forEach((idx) => next.add(idx));
  }
  expandedDetailIndices.value = next;
}

function nextPage() {
  currentPage.value = Math.min(totalPages.value, currentPage.value + 1);
}

function prevPage() {
  currentPage.value = Math.max(1, currentPage.value - 1);
}

function showNameTooltip(text: string, event: MouseEvent) {
  nameTooltip.value.visible = true;
  nameTooltip.value.text = text || "";
  const padding = 12;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = 280;
  const height = 60;
  let x = event.clientX + padding;
  let y = event.clientY + padding;
  if (x + width > vw) x = vw - width - padding;
  if (y + height > vh) y = vh - height - padding;
  nameTooltip.value.x = x;
  nameTooltip.value.y = y;
}

function hideNameTooltip() {
  nameTooltip.value.visible = false;
}

function evidenceDomId(idx: number, evidenceId: string): string {
  return `run-evidence-${idx}-${evidenceId}`;
}

function focusEvidence(idx: number, evidenceId: string) {
  focusedEvidence[idx] = evidenceId;
  void nextTick(() => {
    const target = document.getElementById(evidenceDomId(idx, evidenceId));
    const details = target?.closest("details") as HTMLDetailsElement | null;
    if (details) details.open = true;
    target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.setTimeout(() => {
      if (focusedEvidence[idx] === evidenceId) delete focusedEvidence[idx];
    }, 1800);
  });
}

function evidenceTokens(evidence: ScientificEvidence): EvidenceToken[] {
  const data = asRecord(evidence.data);
  const tokens: EvidenceToken[] = [];
  const coordinates = Array.isArray(data.coordinates) ? data.coordinates : [];
  coordinates.slice(0, 24).forEach((raw) => {
    const row = asRecord(raw);
    const label = String(row.label || "").trim();
    if (!label) return;
    const base = String(row.base || "-").trim();
    const residue = Number(row.residue_index);
    tokens.push({
      label: `${label} · ${base}${Number.isFinite(residue) && residue > 0 ? ` · nt ${residue}` : ""}`,
      kind: "coordinate",
    });
  });
  const pairs = Array.isArray(data.pairs) ? data.pairs : [];
  pairs.slice(0, 24).forEach((raw) => {
    const pair = asRecord(raw);
    const left = String(pair.left_label || "").trim();
    const right = String(pair.right_label || "").trim();
    if (!left || !right) return;
    tokens.push({
      label: `${left} ${String(pair.left_base || "-")} ↔ ${right} ${String(pair.right_base || "-")}`,
      kind: "pair",
    });
  });
  const posterior = Number(data.average_posterior);
  if (Number.isFinite(posterior)) {
    tokens.push({ label: `mean posterior ${posterior.toFixed(3)}`, kind: "metric" });
  }
  const tools = Array.isArray(data.tools) ? data.tools : [];
  tools.slice(0, 5).forEach((tool) => {
    tokens.push({ label: String(tool), kind: "tool" });
  });
  const warningCodes = Array.isArray(data.warning_codes) ? data.warning_codes : [];
  warningCodes.slice(0, 5).forEach((code) => {
    tokens.push({ label: humanizeCode(code), kind: "metric" });
  });
  const missingLabels = Array.isArray(data.missing_labels) ? data.missing_labels : [];
  missingLabels.forEach((label) => {
    tokens.push({ label: `${String(label)} · not occupied`, kind: "metric" });
  });
  const hiddenCount = Math.max(0, coordinates.length - 24) + Math.max(0, pairs.length - 24);
  if (hiddenCount) tokens.push({ label: `+${hiddenCount}`, kind: "metric" });
  return tokens;
}

function evidenceSourceLabel(source: unknown): string {
  const key = String(source || "").trim();
  const labels: Record<InterpretationLanguage, Record<string, string>> = {
    en: {
      input: "Input sequence",
      candidate_coordinates: "Candidate coordinates",
      structure_annotation: "Structure annotation",
      variable_boundary_rule: "Variable-boundary rule",
      covariance_model: "Covariance model",
      agent_trace: "Agent trace",
      run_validation: "Run validation",
      run_observation: "Recorded run observation",
      deterministic_validation: "Deterministic validation",
    },
    "zh-CN": {
      input: "输入序列",
      candidate_coordinates: "候选坐标",
      structure_annotation: "二级结构注释",
      variable_boundary_rule: "可变区边界规则",
      covariance_model: "协方差模型",
      agent_trace: "智能体轨迹",
      run_validation: "运行验证",
      run_observation: "已记录的运行观察",
      deterministic_validation: "确定性验证",
    },
    de: {
      input: "Eingabesequenz",
      candidate_coordinates: "Kandidatenkoordinaten",
      structure_annotation: "Strukturanmerkung",
      variable_boundary_rule: "Regel der variablen Grenze",
      covariance_model: "Kovarianzmodell",
      agent_trace: "Agentenprotokoll",
      run_validation: "Laufvalidierung",
      run_observation: "Erfasste Laufbeobachtung",
      deterministic_validation: "Deterministische Validierung",
    },
    fr: {
      input: "Séquence d'entrée",
      candidate_coordinates: "Coordonnées candidates",
      structure_annotation: "Annotation structurale",
      variable_boundary_rule: "Règle de frontière variable",
      covariance_model: "Modèle de covariance",
      agent_trace: "Trace de l'agent",
      run_validation: "Validation de l'analyse",
      run_observation: "Observation enregistrée",
      deterministic_validation: "Validation déterministe",
    },
  };
  return labels[detailLanguage.value][key] || humanizeCode(key);
}

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

function referenceSummary(citation: ScientificCitation): string {
  const authors = (citation.authors || []).map(formatAuthorName).filter(Boolean);
  const who = authors.length ? (authors.length > 1 ? `${authors[0]} et al.` : authors[0]) : "";
  const parts = [who, citation.year, String(citation.venue || "").trim()];
  if (citation.page) parts.push(`p.${citation.page}`);
  parts.push(
    citation.source === "local_pdf"
      ? detailText("referenceSourceLocal")
      : detailText("referenceSourcePublic"),
  );
  return parts.filter(Boolean).join(" · ");
}

function setDownloadMenuRef(idx: number, el: HTMLDetailsElement | null) {
  downloadMenuRefs[idx] = el;
}

function setTableExportRef(idx: number, el: HTMLElement | null) {
  tableExportRefs[idx] = el;
}

function setRadialExportRef(idx: number, el: HTMLElement | null) {
  radialExportRefs[idx] = el;
}

function closeDownloadMenu(idx: number) {
  const menu = downloadMenuRefs[idx];
  if (menu) menu.open = false;
}

function handleDocumentClick(event: MouseEvent) {
  const target = event.target as Node | null;
  for (const key of Object.keys(downloadMenuRefs)) {
    const idx = Number(key);
    const menu = downloadMenuRefs[idx];
    if (!menu?.open) continue;
    if (target && menu.contains(target)) continue;
    menu.open = false;
  }
}

onMounted(() => {
  document.addEventListener("click", handleDocumentClick, true);
});

onBeforeUnmount(() => {
  componentDisposed = true;
  interpretationControllers.forEach((controller) => controller.abort());
  interpretationControllers.clear();
  document.removeEventListener("click", handleDocumentClick, true);
});

watch(
  () => props.results,
  (next) => {
    if (next !== lastResultsSource) {
      expandedDetailIndices.value = new Set();
      previousPendingState.clear();
      lastResultsSource = next;
    }
    next.forEach((result, idx) => {
      const wasPending = previousPendingState.get(idx) === true;
      if (wasPending && !result.pending) {
        const expanded = new Set(expandedDetailIndices.value);
        expanded.delete(idx);
        expandedDetailIndices.value = expanded;
        selectedRunEventIndex[idx] = orderedProcessEvents(result).length - 1;
      }
      previousPendingState.set(idx, Boolean(result.pending));
    });
    editableResults.value = cloneResults(next || []);
    for (const key in editState) delete editState[Number(key)];
    for (const key in editMessages) delete editMessages[Number(key)];
    for (const key in realignLoading) delete realignLoading[Number(key)];
    syncScientificInterpretations();
    expandedDetailIndices.value = new Set(
      [...expandedDetailIndices.value].filter(
        (idx) => editableResults.value[idx] && hasResultDetails(editableResults.value[idx])
      )
    );
    Object.keys(selectedRunEventIndex).forEach((rawIdx) => {
      const idx = Number(rawIdx);
      if (!editableResults.value[idx]) delete selectedRunEventIndex[idx];
    });
    selectedIndices.value = new Set(
      editableResults.value.map((_, idx) => idx)
    );
    currentPage.value = 1;
  },
  { immediate: true, deep: true }
);

watch(
  () => props.interpretationLanguage,
  () => syncScientificInterpretations(),
);

watch(
  () => selectedResults.value.length,
  () => {
    if (currentPage.value > totalPages.value) currentPage.value = 1;
  }
);

watch(
  () => pageSize.value,
  () => {
    currentPage.value = 1;
  }
);

watch(hasSelectedReference, (available) => {
  if (!available) showOriginRow.value = false;
});

/* ---------- 成功条目 ---------- */
const successResults = computed(() =>
  editableResults.value.filter(
    (r) => !r.error && r.nums && r.seq && r.nums.length === r.seq.length
  )
);

function formatWarnings(res: AlignmentResult): string {
  const ws = (res.warnings || []).map((w) => String(w).trim()).filter(Boolean);
  return ws.map(humanizeCode).join(" · ");
}

function hasAlignment(res: AlignmentResult): boolean {
  return !!(
    Array.isArray(res.nums)
    && Array.isArray(res.seq)
    && res.nums.length > 0
    && res.nums.length === res.seq.length
  );
}

function interpretationDispositionClass(code: unknown): string {
  const value = String(code || "").trim().toLowerCase();
  if (value === "completed") return "is-completed";
  if (value === "completed_with_note") return "is-noted";
  if (value === "review_required") return "is-review";
  if (value === "non_canonical_structure") return "is-noncanonical";
  return "";
}

function dispositionLabel(disposition: ScientificInterpretation["system_disposition"]): string {
  const code = String(disposition?.code || "").trim();
  const labels: Record<InterpretationLanguage, Record<string, string>> = {
    en: {
      completed: "Completed",
      completed_with_note: "Completed with note",
      review_required: "Review required",
      non_canonical_structure: "Non-canonical structure",
    },
    "zh-CN": {
      completed: "已完成",
      completed_with_note: "已完成，附说明",
      review_required: "需要复核",
      non_canonical_structure: "非经典结构",
    },
    de: {
      completed: "Abgeschlossen",
      completed_with_note: "Mit Hinweis abgeschlossen",
      review_required: "Überprüfung erforderlich",
      non_canonical_structure: "Nichtkanonische Struktur",
    },
    fr: {
      completed: "Terminé",
      completed_with_note: "Terminé avec remarque",
      review_required: "Révision requise",
      non_canonical_structure: "Structure non canonique",
    },
  };
  return labels[detailLanguage.value][code] || String(disposition?.label || humanizeCode(code));
}

function interpretationSequence(res: AlignmentResult): string {
  return (res.seq || [])
    .map((base) => String(base || "").toUpperCase().replace("T", "U"))
    .filter((base) => /^[ACGUN]$/.test(base))
    .join("");
}

function scientificInterpretationSignature(res: AlignmentResult): string {
  return JSON.stringify({
    language: detailLanguage.value,
    target_seq: interpretationSequence(res),
    labels: res.nums || [],
    bases: res.seq || [],
    structure: res.structure || {},
    agent: res.agent || {},
    scientific_provenance: res.scientific_provenance || {},
    warnings: res.warnings || [],
  });
}

function interpretationQueueKey(idx: number, signature: string): string {
  return `${idx}:${signature}`;
}

function syncScientificInterpretations() {
  const activeIndices = new Set<number>();
  editableResults.value.forEach((res, idx) => {
    if (!hasResultDetails(res)) return;
    activeIndices.add(idx);
    const signature = scientificInterpretationSignature(res);
    const previousSignature = scientificInterpretationSignatures[idx];
    if (previousSignature && previousSignature !== signature) {
      delete scientificInterpretations[idx];
      delete scientificInterpretationErrors[idx];
      delete scientificInterpretationStreams[idx];
    }
    scientificInterpretationSignatures[idx] = signature;
    const cached = scientificInterpretationCache.get(signature);
    if (cached) {
      scientificInterpretations[idx] = cached;
      scientificInterpretationLoading[idx] = false;
      return;
    }
    if (!scientificInterpretations[idx]) queueScientificInterpretation(idx, signature);
  });

  Object.keys(scientificInterpretationSignatures).forEach((rawIdx) => {
    const idx = Number(rawIdx);
    if (activeIndices.has(idx)) return;
    delete scientificInterpretationSignatures[idx];
    delete scientificInterpretations[idx];
    delete scientificInterpretationLoading[idx];
    delete scientificInterpretationErrors[idx];
  });
}

function queueScientificInterpretation(idx: number, signature: string, force = false) {
  const key = interpretationQueueKey(idx, signature);
  if (!force && (queuedInterpretations.has(key) || interpretationControllers.has(key))) return;
  queuedInterpretations.add(key);
  scientificInterpretationLoading[idx] = true;
  interpretationQueue.push({ idx, signature });
  drainInterpretationQueue();
}

function drainInterpretationQueue() {
  while (
    !componentDisposed
    && activeInterpretations < INTERPRETATION_CONCURRENCY
    && interpretationQueue.length
  ) {
    const task = interpretationQueue.shift();
    if (!task) return;
    const key = interpretationQueueKey(task.idx, task.signature);
    queuedInterpretations.delete(key);
    if (scientificInterpretationSignatures[task.idx] !== task.signature) continue;
    activeInterpretations += 1;
    generateScientificInterpretation(task.idx, task.signature)
      .finally(() => {
        activeInterpretations -= 1;
        drainInterpretationQueue();
      });
  }
}

function retryScientificInterpretation(idx: number, res: AlignmentResult) {
  const signature = scientificInterpretationSignature(res);
  scientificInterpretationSignatures[idx] = signature;
  delete scientificInterpretationErrors[idx];
  queueScientificInterpretation(idx, signature, true);
}

async function generateScientificInterpretation(idx: number, signature: string) {
  const res = editableResults.value[idx];
  if (!res || !hasAlignment(res)) return;
  const targetSeq = interpretationSequence(res);
  if (!targetSeq) {
    scientificInterpretationErrors[idx] = "The current candidate does not contain a usable nucleotide sequence.";
    scientificInterpretationLoading[idx] = false;
    return;
  }
  scientificInterpretationLoading[idx] = true;
  scientificInterpretationStreams[idx] = "";
  delete scientificInterpretationErrors[idx];
  const key = interpretationQueueKey(idx, signature);
  const controller = new AbortController();
  interpretationControllers.set(key, controller);
  try {
    const response = await apiFetch("/align/explanation/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        target_seq: targetSeq,
        coordinates: { labels: res.nums || [], bases: res.seq || [] },
        structure: res.structure || {},
        agent: res.agent || {},
        scientific_provenance: res.scientific_provenance || {},
        warnings: res.warnings || [],
        record: { id: res.seq_name || `result-${idx + 1}` },
        language: detailLanguage.value,
      }),
    });
    if (!response.ok) throw new Error(await readErrorMessage(response));
    if (!response.body) throw new Error("Streaming response body is unavailable.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let interpretation: ScientificInterpretation | undefined;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      for (const frame of frames) {
        let eventName = "message";
        const dataLines: string[] = [];
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;
        const eventData = JSON.parse(dataLines.join("\n"));
        if (eventName === "delta") {
          scientificInterpretationStreams[idx] += String(eventData.content || "");
        } else if (eventName === "error") {
          throw new Error(String(eventData.message || "Scientific interpretation failed."));
        } else if (eventName === "complete") {
          interpretation = eventData as ScientificInterpretation;
        }
      }
      if (done || interpretation) break;
    }
    if (!interpretation) throw new Error("Scientific interpretation stream ended early.");
    scientificInterpretationCache.set(signature, interpretation);
    if (scientificInterpretationSignatures[idx] === signature) {
      scientificInterpretations[idx] = interpretation;
      delete scientificInterpretationStreams[idx];
    }
  } catch (error) {
    if (!controller.signal.aborted && scientificInterpretationSignatures[idx] === signature) {
      scientificInterpretationErrors[idx] = error instanceof Error
        ? error.message
        : "Scientific interpretation could not be generated.";
    }
  } finally {
    interpretationControllers.delete(key);
    if (scientificInterpretationSignatures[idx] === signature) {
      scientificInterpretationLoading[idx] = false;
    }
  }
}

function scientificInterpretationDraft(raw: string): string {
  const texts: string[] = [];
  const pattern = /"text"\s*:\s*"((?:\\.|[^"\\])*)/g;
  for (const match of raw.matchAll(pattern)) {
    const fragment = match[1] || "";
    try {
      texts.push(JSON.parse(`"${fragment}"`));
    } catch {
      texts.push(fragment
        .replace(/\\n/g, "\n")
        .replace(/\\r/g, "")
        .replace(/\\t/g, "\t")
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, "\\")
        .replace(/\\$/g, ""));
    }
  }
  return texts.filter(Boolean).join("\n\n") || "…";
}

function hasReferenceBaseline(res: AlignmentResult): boolean {
  return !!(
    Array.isArray(res.refNums)
    && Array.isArray(res.ref)
    && res.refNums.length > 0
    && res.refNums.length === res.ref.length
  );
}

function compareReferenceFor(res: AlignmentResult): boolean {
  return showOriginRow.value && hasReferenceBaseline(res);
}

function resultColorMode(res: AlignmentResult): "compare" | "targetOnly" {
  return compareReferenceFor(res) ? "compare" : "targetOnly";
}

function hasRegionalTrace(res: AlignmentResult): boolean {
  return Array.isArray(res.llm_trace?.regions) && res.llm_trace!.regions!.length > 0;
}

function resultSourceLabel(res: AlignmentResult): string {
  const backend = String(res.scientific_provenance?.numbering_backend || "");
  if (backend === "cmalign") return "Numbering model";
  if (!hasAlignment(res)) return "Evidence model";
  return "Matched template";
}

function processEvents(res: AlignmentResult): AgentTraceEvent[] {
  return Array.isArray(res.agent?.trace_events)
    ? res.agent!.trace_events!.filter((event) => String(event?.stage || "").trim())
    : [];
}

function runProcessEvents(res: AlignmentResult): RunProcessEvent[] {
  return Array.isArray(res.run_process?.events) ? res.run_process.events : [];
}

function displayProcessEvents(res: AlignmentResult): AgentTraceEvent[] {
  if (res.pending || res.error) return runProcessEvents(res) as AgentTraceEvent[];
  return processEvents(res).filter((event) => String(event.stage || "") !== "search_stopped");
}

function orderedProcessEvents(res: AlignmentResult): AgentTraceEvent[] {
  const sourceEvents = displayProcessEvents(res);
  const events: AgentTraceEvent[] = [];
  let modelStep: AgentTraceEvent | undefined;

  for (const event of sourceEvents) {
    const stage = String(event.stage || "");
    const tool = String(event.tool || event.tool_name || "");
    if (stage === "search_stopped" || stage === "canonical_route_selected") continue;

    if ((stage === "tool_started" || stage === "tool_observation")
      && ["score_numbering_models", "align_numbering_cm"].includes(tool)) {
      if (!modelStep) {
        modelStep = { stage: "model_alignment", source: event.source, at: event.at };
        events.push(modelStep);
      }
      for (const key of [
        "model_group", "selected_group", "model_groups", "selection_policy",
        "candidate_count", "bit_score", "average_posterior", "residue_count",
        "structure_pair_count", "elapsed_ms", "domain_probabilities_calibrated",
      ]) {
        if (event[key] !== undefined) modelStep[key] = event[key];
      }
      continue;
    }

    if (stage === "root_snapshot_built") {
      events.push({ ...event, stage: "candidate_built", label_count: res.nums?.length || 0 });
      continue;
    }
    events.push(event);
  }

  if (res.pending || res.error) return events;
  const idx = editableResults.value.indexOf(res);
  const interpretationEvent: AgentTraceEvent = {
    stage: "scientific_interpretation",
    source: "system",
    at: new Date().toISOString(),
    status: scientificInterpretationLoading[idx] ? "running" : scientificInterpretationErrors[idx] ? "failed" : scientificInterpretations[idx] ? "completed" : "running",
  };
  return [...events, interpretationEvent];
}

function isCurrentProcessEvent(res: AlignmentResult, event: AgentTraceEvent, index: number): boolean {
  if (!res.pending) return false;
  const events = orderedProcessEvents(res);
  return index === events.length - 1 && !["completed", "failed", "search_stopped"].includes(String(event.stage || ""));
}

function selectedRunEvent(idx: number, res: AlignmentResult): AgentTraceEvent | undefined {
  const events = orderedProcessEvents(res);
  const selected = selectedRunEventIndex[idx] ?? events.length - 1;
  return events[selected];
}

function selectRunEvent(idx: number, eventIndex: number, res?: AlignmentResult) {
  selectedRunEventIndex[idx] = eventIndex;
  if (res && res.pending) return;
}

function processEventFacts(event: AgentTraceEvent): string[] {
  if (["agent_decision", "model_alignment"].includes(String(event.stage || ""))) return [];
  const facts: string[] = [];
  const data = event as Record<string, unknown>;
  for (const key of ["tool", "tool_name", "decision", "disposition", "error_type"]) {
    const value = data[key];
    if (value === undefined || value === null || !String(value).trim()) continue;
    // Tool and action codes get the same short names the curation page shows.
    const text = key === "tool" || key === "tool_name"
      ? copyToolLabel(value, detailLanguage.value)
      : humanizeCode(value);
    facts.push(`${humanizeCode(key)}: ${text}`);
  }
  if (typeof data.valid === "boolean") facts.push(data.valid ? "Validation passed" : "Validation failed");
  if (typeof data.bit_score === "number") facts.push(`bit score ${data.bit_score.toFixed(2)}`);
  if (typeof data.average_posterior === "number") facts.push(`posterior ${data.average_posterior.toFixed(3)}`);
  if (Array.isArray(data.reasons) && event.stage !== "difficulty_assessed") facts.push(...data.reasons.map(humanizeCode).slice(0, 6));
  return [...new Set(facts)];
}

function processEventContent(event: AgentTraceEvent | undefined): string {
  if (!event) return "";
  return String((event as Record<string, unknown>).content || "").trim();
}

function processDisposition(res: AlignmentResult): string {
  const summary = asRecord(res.agent?.work_state_summary);
  const explicit = String(summary.disposition || "").trim().toLowerCase();
  if (explicit) return explicit;
  if (res.agent?.hard_guard_passed === true) return "accepted";
  if (res.agent?.hard_guard_passed === false) return "abstained";
  return "reported";
}

function processDispositionLabel(res: AlignmentResult): string {
  const disposition = processDisposition(res);
  const special = asRecord(res.scientific_provenance?.specialized_route);
  if (disposition === "accepted" && special.disposition === "accepted_with_scope_warning") {
    const scoped: Record<InterpretationLanguage, string> = {
      en: "Accepted with scope warning",
      "zh-CN": "已接受，附适用范围提示",
      de: "Mit Geltungsbereichshinweis akzeptiert",
      fr: "Accepté avec réserve de portée",
    };
    return scoped[detailLanguage.value];
  }
  const labels: Record<InterpretationLanguage, Record<string, string>> = {
    en: { accepted: "Accepted", investigate: "Review", abstained: "Abstained", rejected: "Rejected", reported: "Reported" },
    "zh-CN": { accepted: "已接受", investigate: "需复核", abstained: "已转人工", rejected: "未通过", reported: "已记录" },
    de: { accepted: "Akzeptiert", investigate: "Prüfen", abstained: "Zurückgestellt", rejected: "Abgelehnt", reported: "Erfasst" },
    fr: { accepted: "Accepté", investigate: "À réviser", abstained: "Abstention", rejected: "Rejeté", reported: "Enregistré" },
  };
  return labels[detailLanguage.value][disposition] || humanizeCode(disposition);
}

function processStatusClass(disposition: string): string {
  if (disposition === "accepted") return "is-accepted";
  if (disposition === "investigate") return "is-investigate";
  if (disposition === "abstained" || disposition === "rejected") return "is-abstained";
  return "is-neutral";
}

// The wording lives in one module, so the numbering page and the curation page
// describe the same trace with the same words.
function routeLabel(route: unknown): string {
  return copyRouteLabel(route, detailLanguage.value);
}

function processStageLabel(stage: unknown): string {
  return copyStageLabel(stage, detailLanguage.value);
}

function processEventDetail(event: AgentTraceEvent): string {
  return copyEventDetail(event as ProcessEventLike, detailLanguage.value);
}

function inputMetadataFields(event: AgentTraceEvent): Array<{ label: string; value: string }> {
  return copyInputMetadataFields(event as ProcessEventLike, detailLanguage.value);
}

function processEventClass(event: AgentTraceEvent): string {
  const stage = String(event.stage || "");
  const disposition = String(event.disposition || "").toLowerCase();
  if (event.valid === false || stage.includes("failed") || disposition === "abstained") {
    return "is-warning";
  }
  if (event.valid === true || disposition === "accepted") return "is-ok";
  return "";
}

function calibrationObject(res: AlignmentResult): Record<string, unknown> {
  const portfolio = asRecord(res.scientific_provenance?.cm_portfolio);
  return asRecord(portfolio.calibration);
}

function calibrationEntries(res: AlignmentResult): Array<{ group: string; value: number }> {
  const probabilities = asRecord(calibrationObject(res).probabilities);
  return ["arch", "bact", "euk"]
    .filter((group) => Number.isFinite(Number(probabilities[group])))
    .map((group) => ({ group, value: Number(probabilities[group]) }));
}

function calibrationDisposition(res: AlignmentResult): string {
  const calibration = calibrationObject(res);
  const disposition = String(calibration.disposition || "");
  const confidence = Number(calibration.confidence);
  const margin = Number(calibration.probability_margin);
  const parts = [disposition || String(calibration.status || "")].filter(Boolean);
  if (Number.isFinite(confidence)) parts.push(`confidence ${formatProbability(confidence)}`);
  if (Number.isFinite(margin)) parts.push(`margin ${formatProbability(margin)}`);
  return parts.join(" · ");
}

function formatProbability(value: number): string {
  return `${(Math.max(0, Math.min(1, value)) * 100).toFixed(1)}%`;
}

function numberingModelName(res: AlignmentResult): string {
  return String(asRecord(res.scientific_provenance?.numbering_model).name || "");
}

function numberingModelHash(res: AlignmentResult): string {
  return String(asRecord(res.scientific_provenance?.numbering_model).sha256 || "");
}

function shortHash(hash: string): string {
  return hash.length > 20 ? `${hash.slice(0, 12)}...${hash.slice(-6)}` : hash;
}

function cmScoreLabel(res: AlignmentResult): string {
  const score = asRecord(res.scientific_provenance?.cm_score);
  const parts: string[] = [];
  const bitScore = Number(score.bit_score);
  const posterior = Number(score.average_posterior);
  if (Number.isFinite(bitScore)) parts.push(`bit score ${bitScore.toFixed(2)}`);
  if (Number.isFinite(posterior)) parts.push(`posterior ${posterior.toFixed(3)}`);
  return parts.join(" · ");
}

function trnascanLabel(res: AlignmentResult): string {
  const scan = asRecord(res.scientific_provenance?.trnascan);
  const mode = String(scan.search_mode || "").trim();
  if (!mode) return "";
  const domain = String(scan.domain_hint || "").replace(/_/g, " ").trim();
  const subtype = String(scan.mitochondrial_subtype || "").replace(/_/g, " ").trim();
  const details = mode.startsWith("M:") && subtype && subtype !== "unknown"
    ? `${domain} · ${subtype}`
    : domain;
  return [mode, details].filter(Boolean).join(" · ");
}

function specialToolLabel(res: AlignmentResult): string {
  const special = asRecord(res.scientific_provenance?.specialized_route);
  const provenance = asRecord(special.tool_provenance);
  const tool = String(provenance.tool || "");
  const version = String(provenance.version || "").split(/\r?\n/)[0];
  return [tool, version].filter(Boolean).join(" · ");
}

function canonicalLikeEvidenceLabel(res: AlignmentResult): string {
  const special = asRecord(res.scientific_provenance?.specialized_route);
  const assessment = asRecord(special.canonical_like_structure);
  if (!Object.keys(assessment).length) return "";
  const counts = asRecord(assessment.pair_counts);
  const score = Number(assessment.cm_bit_score);
  const parts = [
    assessment.qualified === true ? "qualified with scope warning" : "not qualified",
    `pairs ${Number(counts.acceptor_stem || 0)}/${Number(counts.d_stem || 0)}/${Number(counts.anticodon_stem || 0)}/${Number(counts.t_stem || 0)}`,
  ];
  if (Number.isFinite(score)) parts.push(`bit score ${score.toFixed(2)}`);
  return parts.join(" · ");
}

function inputTransformLabel(res: AlignmentResult): string {
  const transform = asRecord(res.scientific_provenance?.input_transform);
  const removed = asRecord(transform.removed_interval);
  if (!Object.keys(transform).length) return "";
  const start = Number(removed.start);
  const end = Number(removed.end);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return `removed raw residues ${start}-${end}`;
  }
  return String(transform.type || "recorded").replace(/_/g, " ");
}

function scientificContractLabel(res: AlignmentResult): string {
  const provenance = res.scientific_provenance;
  const boundary = asRecord(provenance?.boundary_refinement_contract);
  if (Object.keys(boundary).length) {
    const count = Number(boundary.candidate_count);
    const parentHash = String(boundary.parent_fingerprint || "");
    const parts = ["deterministic boundary", "no LLM"];
    if (Number.isFinite(count) && count > 0) parts.push(`${count} candidates`);
    if (parentHash) parts.push(`parent ${shortHash(parentHash)}`);
    return parts.join(" · ");
  }
  const regional = asRecord(provenance?.regional_candidate_contract);
  if (!Object.keys(regional).length) return "";
  const attempted = Array.isArray(regional.llm_attempted_regions)
    ? regional.llm_attempted_regions.map(humanizeCode)
    : [];
  const parentHash = String(regional.parent_fingerprint || "");
  const parts = ["bounded regional proposal"];
  parts.push(attempted.length ? `LLM ${attempted.join(", ")}` : "LLM not called");
  if (parentHash) parts.push(`parent ${shortHash(parentHash)}`);
  return parts.join(" · ");
}

function memoryAuditLabel(res: AlignmentResult): string {
  const planning = processEvents(res).find((event) => event.stage === "planning");
  const context = asRecord(planning?.context_summary);
  const mode = String(context.memory_mode || "off");
  if (mode === "off") return "";
  const status = String(context.memory_status || "unknown");
  const count = Number(context.memory_hit_count || 0);
  const hash = String(context.memory_snapshot_sha256 || "");
  const parts = [mode.replace(/_/g, " "), status.replace(/_/g, " "), `${count} hit(s)`];
  if (hash) parts.push(shortHash(hash));
  return parts.join(" · ");
}

function specialRouteMessage(res: AlignmentResult): string {
  const special = asRecord(res.scientific_provenance?.specialized_route);
  return String(special.message || "No validated numbering candidate passed the scientific guards.");
}

function hasTechnicalProvenance(res: AlignmentResult): boolean {
  return !!(
    calibrationEntries(res).length
    || numberingModelName(res)
    || numberingModelHash(res)
    || specialToolLabel(res)
    || trnascanLabel(res)
    || canonicalLikeEvidenceLabel(res)
    || inputTransformLabel(res)
    || scientificContractLabel(res)
    || memoryAuditLabel(res)
  );
}

function traceStatusValue(region: LlmTraceRegion | null | undefined): string {
  return String(region?.final_status || region?.status || "").trim();
}

function traceAttemptCount(region: LlmTraceRegion | null | undefined): number {
  const attempts = Number(region?.attempts);
  if (Number.isFinite(attempts) && attempts >= 0) return attempts;
  return Array.isArray(region?.llm_attempts) ? region.llm_attempts.length : 0;
}

function traceStatusLabel(status: unknown): string {
  const key = String(status || "").trim().toLowerCase();
  if (!key) return "Unknown";
  if (key === "accepted_fallback_chat") return "Accepted · fallback chat";
  if (key === "accepted_deterministic") return "Accepted · deterministic";
  if (key === "accepted" || key === "llm_success") return "Accepted";
  if (key === "preview_only") return "Preview only";
  if (key === "pass_through_no_improvement") return "Pass-through · no improvement";
  if (key === "pass_through_disabled") return "Pass-through · disabled";
  if (key === "pass_through_handler_error") return "Pass-through · handler error";
  if (key === "no_improved_candidate") return "No improvement";
  if (key === "llm_failed") return "LLM failed";
  if (key === "handler_error_pass_through") return "Error fallback";
  if (key.startsWith("skipped_template_all_match")) return "Skipped · template match";
  if (key.startsWith("skipped_use_llm_off")) return "Skipped · LLM off";
  if (key.startsWith("skipped_prompt_disabled")) return "Skipped · prompt disabled";
  if (key.startsWith("skipped_")) return `Skipped · ${key.replace(/^skipped_/, "").replace(/_/g, " ")}`;
  return key.replace(/_/g, " ");
}

function traceStatusClass(status: unknown): string {
  const key = String(status || "").trim().toLowerCase();
  if (key === "accepted" || key === "llm_success" || key.startsWith("accepted_")) return "is-ok";
  if (key.includes("error") || key.includes("failed")) return "is-error";
  if (key === "preview_only") return "is-preview";
  if (key.startsWith("skipped_") || key.startsWith("pass_through_")) return "is-skip";
  return "is-neutral";
}

function cloneResults(results: AlignmentResult[]): AlignmentResult[] {
  return results.map((r) => ({
    ...r,
    nums: r.nums ? [...r.nums] : r.nums,
    seq: r.seq ? [...r.seq] : r.seq,
    refNums: r.refNums ? [...r.refNums] : r.refNums,
    ref: r.ref ? [...r.ref] : r.ref,
    warnings: r.warnings ? [...r.warnings] : r.warnings,
    structure: r.structure
      ? {
          ...r.structure,
          id_pairs: r.structure.id_pairs ? r.structure.id_pairs.map((p) => ({ ...p })) : [],
        }
      : r.structure,
    llm_trace: cloneTracePayload(r.llm_trace),
  }));
}

function isInsertionId(id: string) {
  return /i\d+$/.test(id);
}

function isBareInsertionId(id: string) {
  return /^i\d+$/.test(id);
}

function normalizeBase(raw: string) {
  const cleaned = String(raw || "-").trim().toUpperCase();
  const char = cleaned[0] || "-";
  return /[ACGTU-]/.test(char) ? char : "-";
}

function getAllIds(res: AlignmentResult) {
  return res.nums ? [...res.nums] : [];
}

function getNonInsertionIds(res: AlignmentResult) {
  return res.nums ? res.nums.filter((id) => !isInsertionId(id)) : [];
}

function getInsertionIds(res: AlignmentResult) {
  return res.nums ? res.nums.filter((id) => isInsertionId(id)) : [];
}

function getInsertAnchors(res: AlignmentResult) {
  const nonInsert = getNonInsertionIds(res);
  return nonInsert.length ? nonInsert : getAllIds(res);
}

function normalizeInsertionIds(res: AlignmentResult) {
  if (!res.nums) return;
  const maxByParent: Record<string, number> = {};
  res.nums.forEach((id) => {
    const match = String(id).match(/^(.+?)i(\d+)$/);
    if (match) {
      const parent = match[1];
      const num = Number.parseInt(match[2], 10);
      maxByParent[parent] = Math.max(maxByParent[parent] || 0, num);
    }
  });
  let lastAnchor = "";
  let changed = false;
  const nextNums = res.nums.map((id) => {
    const raw = String(id);
    if (!isInsertionId(raw)) {
      lastAnchor = raw;
      return id;
    }
    if (isBareInsertionId(raw)) {
      if (!lastAnchor) return id;
      const next = (maxByParent[lastAnchor] || 0) + 1;
      maxByParent[lastAnchor] = next;
      changed = true;
      return `${lastAnchor}i${next}`;
    }
    return id;
  });
  if (changed) {
    res.nums = nextNums;
    // keep silent; IDs are normalized in-place
  }
}

function ensureEditState(idx: number, res: AlignmentResult) {
  normalizeInsertionIds(res);
  if (!editState[idx]) {
    const nonInsert = getNonInsertionIds(res);
    const allIds = getAllIds(res);
    const insertIds = getInsertionIds(res);
    editState[idx] = {
      insertAfter: nonInsert[0] || allIds[0] || "",
      insertBase: "A",
      deleteId: allIds[0] || "",
      removeId: insertIds[0] || "",
      editBase: "A",
      clickEdit: false,
    };
  } else {
    const nonInsert = getNonInsertionIds(res);
    const allIds = getAllIds(res);
    const insertIds = getInsertionIds(res);
    if (
      editState[idx].insertAfter &&
      !nonInsert.includes(editState[idx].insertAfter)
    ) {
      editState[idx].insertAfter = nonInsert[0] || allIds[0] || "";
    }
    if (editState[idx].deleteId && !allIds.includes(editState[idx].deleteId)) {
      editState[idx].deleteId = allIds[0] || "";
    }
    if (editState[idx].removeId && !insertIds.includes(editState[idx].removeId)) {
      editState[idx].removeId = insertIds[0] || "";
    }
  }
  return editState[idx];
}

function setEditMessage(idx: number, msg: string) {
  editMessages[idx] = msg;
}

function buildSequenceFromAlignment(res: AlignmentResult) {
  if (!res.seq) return "";
  const raw = res.seq.join("");
  return raw
    .toUpperCase()
    .replace(/-/g, "")
    .replace(/T/g, "U")
    .replace(/[^AUGC]/g, "");
}

function parseCsv(csvText: string) {
  const [h, b] = String(csvText || "").trim().split(/\r?\n/);
  if (!h || !b) throw new Error("Alignment response missing CSV content.");
  return { nums: h.split(","), seq: b.split(",") };
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

function cloneTracePayload(raw: unknown): LlmTracePayload | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  try {
    return JSON.parse(JSON.stringify(raw)) as LlmTracePayload;
  } catch {
    return undefined;
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

async function realignResult(idx: number, res: AlignmentResult) {
  if (!res.seq) {
    setEditMessage(idx, "No sequence available to re-align.");
    return;
  }
  const seq = buildSequenceFromAlignment(res);
  if (!seq) {
    setEditMessage(idx, "Edited sequence is empty after removing gaps.");
    return;
  }
  realignLoading[idx] = true;
  try {
    const response = await apiFetch("/align/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_seq: seq,
        use_llm: false,
        template_name: res.template_name || undefined,
      }),
    });
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    const data = await response.json();
    const parsed = parseCsv(data.csv_content);
    res.nums = parsed.nums;
    res.seq = parsed.seq;
    res.structure = normalizeStructure((data as any).structure);
    res.llm_trace = cloneTracePayload((data as any).llm_trace);
    if (data.template_name) {
      res.template_name = data.template_name;
    }
    setEditMessage(idx, "Re-alignment completed.");
  } catch (err: any) {
    setEditMessage(idx, err.message || "Re-alignment failed.");
  } finally {
    realignLoading[idx] = false;
  }
}

function insertAfter(idx: number, res: AlignmentResult) {
  if (!res.nums || !res.seq) return;
  const state = ensureEditState(idx, res);
  const anchor = state.insertAfter;
  if (!anchor) {
    setEditMessage(idx, "No anchor ID to insert after.");
    return;
  }
  const base = normalizeBase(state.insertBase);
  const parent = isInsertionId(anchor)
    ? anchor.replace(/i\d+$/, "")
    : anchor;
  const allNums = res.nums;
  let insertPos = -1;
  const parentIdx = allNums.indexOf(parent);
  const anchorIdx = allNums.indexOf(anchor);
  const startIdx =
    parentIdx >= 0 ? parentIdx + 1 : anchorIdx >= 0 ? anchorIdx + 1 : -1;
  if (startIdx >= 0) {
    insertPos = startIdx;
    while (
      insertPos < allNums.length &&
      String(allNums[insertPos]).startsWith(parent + "i")
    ) {
      insertPos += 1;
    }
  }
  if (insertPos < 0) {
    setEditMessage(idx, "Failed to locate anchor position.");
    return;
  }
  let maxN = 0;
  allNums.forEach((id) => {
    if (String(id).startsWith(parent + "i")) {
      const match = String(id).match(/i(\d+)$/);
      if (match) {
        maxN = Math.max(maxN, Number.parseInt(match[1], 10));
      }
    }
  });
  const newId = `${parent}i${maxN + 1}`;
  allNums.splice(insertPos, 0, newId);
  res.seq.splice(insertPos, 0, base);
  state.removeId = newId;
  setEditMessage(idx, `Inserted ${base} as ${newId}.`);
}

function handleTargetClick(idx: number, res: AlignmentResult, id: string) {
  if (!res.nums || !res.seq) return;
  const state = ensureEditState(idx, res);
  if (!state.clickEdit) {
    setEditMessage(idx, "Enable click edit to change target bases.");
    return;
  }
  const pos = res.nums.indexOf(id);
  if (pos < 0) {
    setEditMessage(idx, "Selected ID not found.");
    return;
  }
  const base = normalizeBase(state.editBase);
  res.seq[pos] = base;
  setEditMessage(idx, `Set ${id} to ${base}.`);
}

function setGap(idx: number, res: AlignmentResult) {
  if (!res.nums || !res.seq) return;
  const state = ensureEditState(idx, res);
  const targetId = state.deleteId;
  if (!targetId) {
    setEditMessage(idx, "No ID selected to delete.");
    return;
  }
  const pos = res.nums.indexOf(targetId);
  if (pos < 0) {
    setEditMessage(idx, "Selected ID not found.");
    return;
  }
  res.seq[pos] = "-";
  setEditMessage(idx, `Set ${targetId} to gap (-).`);
}

function removeInsertion(idx: number, res: AlignmentResult) {
  if (!res.nums || !res.seq) return;
  const state = ensureEditState(idx, res);
  const targetId = state.removeId;
  if (!targetId) {
    setEditMessage(idx, "No insertion ID selected.");
    return;
  }
  if (!isInsertionId(targetId)) {
    setEditMessage(idx, "Only insertion IDs (iN) can be removed.");
    return;
  }
  const pos = res.nums.indexOf(targetId);
  if (pos < 0) {
    setEditMessage(idx, "Insertion ID not found.");
    return;
  }
  res.nums.splice(pos, 1);
  res.seq.splice(pos, 1);
  setEditMessage(idx, `Removed insertion ${targetId}.`);
}

/* ---------- 单条节点组装 ---------- */
function buildNodes(res: AlignmentResult) {
  if (!res.nums || !res.seq) return [];
  const supMap = new Map(res.nums.map((id, i) => [id, res.seq![i] || "-"]));
  const baseMap = res.refNums
    ? new Map(res.refNums.map((id, i) => [id, res.ref![i] || "-"]))
    : new Map<string, string>();
  return res.nums.map((id) => {
    const sup = supMap.get(id)!;
    const base = baseMap.get(id) ?? "-";
    let type: "match" | "mismatch" | "insertion" | "deletion" = "match";
    if (base === "-" && sup !== "-") type = "insertion";
    else if (base !== "-" && sup === "-") type = "deletion";
    else if (base !== sup) type = "mismatch";
    return { id, base, sup_base: sup, type };
  });
}

function sanitizeFilename(raw: string) {
  const cleaned = (raw || "").replace(/[\\/:*?"<>|]+/g, "_").trim();
  return cleaned || "seq";
}

function getSeqIndex(res: AlignmentResult, fallbackIdx: number) {
  return typeof res.seq_index === "number" ? res.seq_index : fallbackIdx;
}

function getSeqRowLabel(res: AlignmentResult, fallbackIdx: number) {
  const name = (res.seq_name || "").trim();
  const idx = getSeqIndex(res, fallbackIdx);
  return name || `Seq ${idx + 1}`;
}

function getSeqLabel(res: AlignmentResult, fallbackIdx: number) {
  const name = (res.seq_name || "").trim();
  const idx = getSeqIndex(res, fallbackIdx);
  if (name) {
    return `${name} (Seq ${idx + 1})`;
  }
  return `Seq ${idx + 1}`;
}

/* ---------- 单条下载 ---------- */
function downloadResult(res: AlignmentResult, idx: number) {
  if (!res.nums || !res.seq) return;
  exportingKind[idx] = "csv";
  const nodes = buildNodes(res);
  const idRow = ["ID", ...nodes.map((n) => n.id)].join(",");
  const baseRow = ["Template", ...nodes.map((n) => n.base)].join(",");
  const supRow = ["Target", ...nodes.map((n) => n.sup_base)].join(",");
  const fileLabel = sanitizeFilename(
    (res.seq_name || "").trim() || `seq${getSeqIndex(res, idx) + 1}`
  );
  triggerCsvDownload(
    [idRow, baseRow, supRow].join("\n"),
    `${fileLabel}_${res.template_name || "alignment"}.csv`
  );
  closeDownloadMenu(idx);
  exportingKind[idx] = "";
}

function getExportNode(
  idx: number,
  kind: "table" | "structure"
): HTMLElement | SVGElement | null {
  const root = kind === "table" ? tableExportRefs[idx] : radialExportRefs[idx];
  if (!root) return null;
  if (kind === "table") {
    return (
      root.querySelector(".alignment-wrappers") ||
      root
    ) as HTMLElement | null;
  }
  return (
    root.querySelector(".trna-container") ||
    root.querySelector("svg") ||
    root
  ) as HTMLElement | SVGElement | null;
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function exportNodeAsTransparentPng(
  node: HTMLElement | SVGElement,
  filename: string
) {
  const htmlNode = node as HTMLElement;
  htmlNode.classList.add("png-export-transparent");
  try {
    const blob = await toBlob(node as HTMLElement, {
      backgroundColor: "transparent",
      pixelRatio: 2,
      cacheBust: true,
    });
    if (!blob) throw new Error("PNG export returned empty blob.");
    triggerBlobDownload(blob, filename);
  } finally {
    htmlNode.classList.remove("png-export-transparent");
  }
}

async function downloadTablePng(res: AlignmentResult, idx: number) {
  const node = getExportNode(idx, "table");
  if (!node) return;
  exportingKind[idx] = "table-png";
  try {
    const fileLabel = sanitizeFilename(
      (res.seq_name || "").trim() || `seq${getSeqIndex(res, idx) + 1}`
    );
    await exportNodeAsTransparentPng(
      node,
      `${fileLabel}_${res.template_name || "alignment"}_numbering_table.png`
    );
    closeDownloadMenu(idx);
  } catch (err) {
    console.error(err);
  } finally {
    exportingKind[idx] = "";
  }
}

async function downloadStructurePng(res: AlignmentResult, idx: number) {
  const node = getExportNode(idx, "structure");
  if (!node) return;
  exportingKind[idx] = "structure-png";
  try {
    const fileLabel = sanitizeFilename(
      (res.seq_name || "").trim() || `seq${getSeqIndex(res, idx) + 1}`
    );
    await exportNodeAsTransparentPng(
      node,
      `${fileLabel}_${res.template_name || "alignment"}_secondary_structure.png`
    );
    closeDownloadMenu(idx);
  } catch (err) {
    console.error(err);
  } finally {
    exportingKind[idx] = "";
  }
}

/* ---------- 合并下载 & 物种树/热图 ---------- */
const vizMode = ref<"heatmap" | "tree">("heatmap");
const heatmapScheme = ref<"base" | "diff">(referenceMode.value === "per-row" ? "base" : "diff");
const treeNewick = ref<string | null>(null);
const treeError = ref<string | null>(null);
const treeLoading = ref(false);
const treeRef = ref<InstanceType<typeof PhyloTree> | null>(null);
const mergedCsv = computed(() => makeMergedCsv());

let treeFetchInFlight = false;
let treeFetchCooldownUntil = 0;
let lastTreeCsv = "";
let pendingTreeCsv = "";
let treeDebounce: number | null = null;

const standardSprinzlIds = new Set(SPRINZL_POSITION_ORDER);
const insertionIdPattern = /^(.+?)i(\d+)$/;

function buildComparisonIds(results: AlignmentResult[]): string[] {
  const insertionsByParent = new Map<string, Map<string, number>>();
  const unknownIds = new Set<string>();

  results.forEach((result) => {
    (result.nums || []).forEach((rawId) => {
      const id = String(rawId || "").trim();
      if (!id) return;
      const insertion = id.match(insertionIdPattern);
      if (insertion && standardSprinzlIds.has(insertion[1])) {
        const siblings = insertionsByParent.get(insertion[1]) ?? new Map<string, number>();
        siblings.set(id, Number.parseInt(insertion[2], 10));
        insertionsByParent.set(insertion[1], siblings);
      } else if (!standardSprinzlIds.has(id)) {
        unknownIds.add(id);
      }
    });
  });

  const ids: string[] = [];
  SPRINZL_POSITION_ORDER.forEach((id) => {
    ids.push(id);
    const insertions = insertionsByParent.get(id);
    if (insertions) {
      ids.push(
        ...[...insertions.entries()]
          .sort((left, right) => left[1] - right[1] || left[0].localeCompare(right[0]))
          .map(([insertionId]) => insertionId),
      );
    }
  });
  ids.push(...[...unknownIds].sort((left, right) => left.localeCompare(right)));
  return ids;
}

function makeMergedCsv(): string {
  const succ = successResults.value;
  if (succ.length < 1) return "";
  const allIds = buildComparisonIds(succ);
  const rows: string[][] = [];
  rows.push(["ID", ...allIds]);
  succ.forEach((r, i) => {
    const map = new Map(r.nums!.map((id, j) => [id, r.seq![j] || "-"]));
    const row = [getSeqRowLabel(r, r.seq_index ?? i)];
    allIds.forEach((id) => row.push(map.get(id) ?? "-"));
    rows.push(row);
  });
  return rows.map((r) => r.join(",")).join("\n");
}

const heatmapData = computed(() => {
  const succ = successResults.value;
  if (succ.length < 1) return { ids: [], rows: [] };
  const allIds = buildComparisonIds(succ);
  const rows = succ.map((r, i) => {
    const map = new Map(r.nums!.map((id, j) => [id, r.seq![j] || "-"]));
    return {
      name: getSeqRowLabel(r, r.seq_index ?? i),
      values: allIds.map((id) => map.get(id) ?? "-"),
    };
  });
  if (referenceMode.value === "single") {
    const hasExplicitReference = succ.some((r) => r.seq_index === 0);
    if (!hasExplicitReference) {
      const refSource = succ.find(
        (r) => r.refNums && r.ref && r.refNums.length === r.ref.length
      );
      const refValues = refSource
        ? allIds.map((id) => {
            const map = new Map(
              refSource.refNums!.map((rid, idx) => [rid, refSource.ref![idx] || "-"])
            );
            return map.get(id) ?? "-";
          })
        : rows[0]
          ? [...rows[0].values]
          : [];
      if (refValues.length) {
        rows.unshift({
          name: "Reference",
          label: "Reference",
          values: refValues,
          isReference: true,
        } as any);
      }
    }
  }
  return { ids: allIds, rows };
});

const heatmapRowReferences = computed(() => {
  if (referenceMode.value !== "per-row") return [] as string[][];
  const succ = successResults.value.filter((r) => r.nums && r.seq);
  if (!succ.length) return [];
  const ids = heatmapData.value.ids;
  return succ.map((r) => {
    if (!r.refNums || !r.ref || r.refNums.length !== r.ref.length) {
      return ids.map(() => "-");
    }
    const map = new Map(r.refNums.map((rid, idx) => [rid, r.ref![idx] || "-"]));
    return ids.map((id) => map.get(id) ?? "-");
  });
});

const heatmapReference = computed(() => {
  const refRow = heatmapData.value.rows.find((row: any) => row.isReference);
  if (refRow) return [...refRow.values];
  const explicit = successResults.value.find((r) => r.seq_index === 0);
  if (!explicit || !explicit.nums || !explicit.seq) return [];
  const map = new Map(explicit.nums.map((id, idx) => [id, explicit.seq![idx] || "-"]));
  return heatmapData.value.ids.map((id) => map.get(id) ?? "-");
});



function downloadMerged() {
  const csvText = makeMergedCsv();
  triggerCsvDownload(csvText, `merged_${successResults.value.length}seqs.csv`);
}

function retryTree() {
  const csv = mergedCsv.value;
  if (!csv) return;
  treeError.value = null;
  treeFetchCooldownUntil = 0;
  lastTreeCsv = "";
  scheduleTree(csv);
}

function scheduleTree(csv: string) {
  pendingTreeCsv = csv;
  if (treeDebounce !== null) window.clearTimeout(treeDebounce);
  treeDebounce = window.setTimeout(() => {
    treeDebounce = null;
    if (treeFetchInFlight) return;
    const current = pendingTreeCsv;
    pendingTreeCsv = "";
    fetchTree(current).catch((err) => {
      treeError.value = err?.message || "Failed to build tree.";
      treeLoading.value = false;
      treeFetchInFlight = false;
      treeFetchCooldownUntil = Date.now() + 8000;
    });
  }, 200);
}

async function fetchTree(csvText: string) {
  if (!csvText) return;
  const now = Date.now();
  if (treeFetchInFlight || now < treeFetchCooldownUntil) return;
  if (csvText === lastTreeCsv && (treeNewick.value || treeError.value)) return;
  treeFetchInFlight = true;
  treeLoading.value = true;
  lastTreeCsv = csvText;
  try {
    const fd = new FormData();
    const blob = new Blob([csvText], { type: "text/csv" });
    fd.append("file", blob, "merged.csv");
    const res = await apiFetch("/plot/tree", { method: "POST", body: fd });
    if (!res.ok) {
      throw new Error(await res.text());
    }
    treeNewick.value = await res.text();
    treeError.value = null;
    treeRef.value?.resetZoom?.();
  } catch (err: any) {
    treeError.value = err.message || "Failed to build tree.";
    treeFetchCooldownUntil = Date.now() + 8000;
  } finally {
    treeLoading.value = false;
    treeFetchInFlight = false;
    if (pendingTreeCsv && pendingTreeCsv !== lastTreeCsv) {
      scheduleTree(pendingTreeCsv);
    }
  }
}


// 树自动后台生成（同一数据只计算一次）
watch(
  () => mergedCsv.value,
  (csv) => {
    if (!csv) {
      if (treeNewick.value) treeNewick.value = null;
      if (treeError.value) treeError.value = null;
      if (treeLoading.value) treeLoading.value = false;
      lastTreeCsv = "";
      return;
    }
    if (csv !== lastTreeCsv) {
      if (treeNewick.value) treeNewick.value = null;
      if (treeError.value) treeError.value = null;
      scheduleTree(csv);
    }
  },
  { flush: "post", immediate: true }
);

/* ---------- 浏览器下载助手 ---------- */
function triggerCsvDownload(csv: string, filename: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
</script>

<style scoped>
.merge-row {
  text-align: right;
  margin-bottom: 1rem;
}
.viz-toggle {
  display: inline-flex;
  gap: 6px;
  padding: 4px;
  border-radius: 999px;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
  margin-bottom: 0.75rem;
}
.viz-btn {
  border: none;
  padding: 0.3rem 0.85rem;
  border-radius: 999px;
  cursor: pointer;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.viz-btn.active {
  background: rgba(var(--success-rgb), 0.2);
  color: var(--text-strong);
  border: 1px solid rgba(var(--success-rgb), 0.55);
}

.btn-download-big {
  background: var(--info);
  color: #fff;
  border: none;
  border-radius: 5px;
  padding: 6px 14px;
  font-size: 0.85rem;
  cursor: pointer;
}
.btn-download-big:hover {
  background: var(--info-strong);
}

.tree-panel {
  margin: 1rem 0;
}
.tree-loading {
  margin: 0.75rem 0;
  padding: 0.6rem 0.8rem;
  background: var(--surface-3);
  color: var(--text-soft);
  border-radius: 8px;
}
.tree-error {
  margin: 0.75rem 0;
  color: var(--danger);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.select-panel {
  margin: 0.5rem 0 1rem;
  padding: 0.6rem 0.75rem;
  background: var(--surface-2);
  border-radius: 8px;
  border: 1px solid var(--border-soft);
}
.select-panel summary {
  cursor: pointer;
  color: var(--text-soft);
  font-weight: 600;
  margin-bottom: 0.5rem;
}
.select-actions {
  display: flex;
  gap: 0.5rem;
  margin: 0.5rem 0;
}
.select-btn {
  border: none;
  background: rgba(var(--success-rgb), 0.25);
  color: var(--text-strong);
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}
.select-btn:hover {
  background: rgba(var(--success-rgb), 0.4);
}
.select-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 6px 12px;
  max-height: 220px;
  overflow: auto;
  padding-bottom: 0.25rem;
}
.select-item {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--text-soft);
  font-size: 0.9rem;
}
.select-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.select-error {
  color: var(--danger);
  font-size: 0.8rem;
}
.select-warn {
  color: var(--warning);
  font-size: 0.8rem;
}
.page-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
.row-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--text-soft);
  font-size: 0.84rem;
}
.row-toggle input {
  accent-color: var(--success);
}
.master-details-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text-soft);
  cursor: pointer;
}
.master-details-button:hover,
.master-details-button:focus-visible {
  border-color: rgba(var(--accent-rgb), 0.42);
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent-strong);
  outline: none;
}
.master-details-button.is-partial {
  border-color: rgba(var(--accent-rgb), 0.3);
  color: var(--accent-strong);
}
.master-details-button svg {
  width: 18px;
  height: 18px;
}
.page-size {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--text-soft);
  font-size: 0.85rem;
}
.page-size-select {
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 2px 22px 2px 6px;
}
.page-btn {
  border: none;
  background: var(--accent-soft);
  color: var(--text-strong);
  padding: 4px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}
.page-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.page-info {
  color: var(--text-soft);
  font-size: 0.9rem;
}
.empty-hint {
  color: var(--text-muted);
  margin-bottom: 1rem;
}
.name-tooltip {
  position: fixed;
  z-index: 9999;
  max-width: 280px;
  padding: 6px 8px;
  background: var(--surface-dark);
  color: var(--overlay-text, #f8fafc);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  font-size: 0.85rem;
  line-height: 1.3;
  pointer-events: none;
}

.result-block {
  margin-bottom: 1.5rem;
}
.title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.8rem;
}
.title-copy {
  min-width: 0;
}
.title-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.res-title {
  font-size: 1.1rem;
  color: var(--success);
  margin: 0.5rem 0;
}
.res-subtitle {
  margin-top: -0.2rem;
  color: var(--text-subtle);
  font-size: 0.82rem;
  line-height: 1.35;
  word-break: break-word;
}
.btn-download {
  background: var(--success);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 4px 12px;
  font-size: 0.8rem;
  cursor: pointer;
}
.btn-download:hover {
  background: var(--success-strong);
}
.download-menu {
  position: relative;
}
.download-menu summary {
  list-style: none;
}
.download-menu summary::-webkit-details-marker {
  display: none;
}
.download-menu-list {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 20;
  min-width: 190px;
  padding: 0.35rem;
  border-radius: 10px;
  border: 1px solid var(--border-soft);
  background: var(--surface-1, #fff);
  box-shadow: var(--shadow-soft);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.download-option {
  border: none;
  background: transparent;
  color: var(--text-strong);
  padding: 0.55rem 0.7rem;
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
  font-size: 0.84rem;
}
.download-option:hover {
  background: var(--surface-2);
}
.download-option:disabled {
  opacity: 0.55;
  cursor: wait;
}
.radial {
  max-width: 620px;
  margin: 0 auto 1rem;
}
.error-card {
  padding: 0.75rem 1rem;
  border-left: 4px solid var(--danger);
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: 4px;
  font-size: 0.9rem;
}
.pending-card {
  padding: 0.75rem 1rem;
  border-left: 4px solid var(--info);
  background: var(--surface-2);
  color: var(--text-soft);
  border-radius: 4px;
  font-size: 0.9rem;
}
.warning-card {
  padding: 0.7rem 0.95rem;
  border-left: 4px solid var(--warning);
  background: var(--warning-soft);
  color: var(--text-strong);
  border-radius: 4px;
  font-size: 0.9rem;
}
.run-details {
  margin: 0.75rem 0;
}
.run-details-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.55rem;
  min-height: 36px;
  padding: 0.4rem 0.65rem;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text-soft);
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 700;
}
.run-details-toggle:hover,
.run-details-toggle:focus-visible,
.run-details-toggle[aria-expanded="true"] {
  border-color: rgba(var(--accent-rgb), 0.35);
  color: var(--accent-strong);
  outline: none;
}
.run-details-toggle svg {
  width: 16px;
  height: 16px;
}
.run-details-panel {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(340px, 0.92fr);
  align-items: start;
  gap: 0;
  margin-top: 0.45rem;
  padding: 0;
  border: 1px solid var(--border-soft);
  border-radius: 7px;
  background: var(--surface-1);
  overflow: hidden;
}
.live-run-panel {
  margin-top: 0;
}
.live-state-copy {
  display: grid;
  justify-items: end;
  gap: 0.25rem;
}
.live-state-copy small {
  color: var(--text-muted);
  font-size: 0.72rem;
}
.step-inspector {
  min-width: 0;
  padding: 1rem;
  border-right: 1px solid var(--border-soft);
  color: var(--text-soft);
}
.step-inspector > strong,
.step-inspector .detail-heading-copy > strong {
  color: var(--text-strong);
}
.step-inspector p {
  margin: 0.65rem 0 0;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.input-metadata-list {
  display: grid;
  grid-template-columns: minmax(110px, 0.28fr) minmax(0, 1fr);
  gap: 0.45rem 0.85rem;
  margin: 0.9rem 0 0;
  font-size: 0.86rem;
  line-height: 1.45;
}
.input-metadata-list dt {
  color: var(--text-muted);
  font-weight: 650;
}
.input-metadata-list dd {
  min-width: 0;
  margin: 0;
  color: var(--text-strong);
  overflow-wrap: anywhere;
}
@media (max-width: 680px) {
  .input-metadata-list {
    grid-template-columns: minmax(92px, 0.35fr) minmax(0, 1fr);
    gap: 0.4rem 0.6rem;
  }
}
.step-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.8rem;
}
.llm-response-stream {
  max-height: 220px;
  margin: 0.65rem 0 0;
  padding: 0.7rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text-strong);
  font: 0.8rem/1.45 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.step-facts span {
  padding: 0.24rem 0.45rem;
  border: 1px solid var(--border-soft);
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 0.72rem;
  overflow-wrap: anywhere;
}
.live-process {
  min-width: 0;
  padding: 0.9rem 1rem;
}
.live-process > strong {
  display: block;
  margin-bottom: 0.55rem;
  color: var(--text-strong);
  font-size: 0.86rem;
}
.detail-column {
  min-width: 0;
  padding: 0.9rem 1rem;
}
.scientific-interpretation {
  padding: 0.9rem 1rem;
}
.scientific-interpretation-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.scientific-interpretation-head > strong {
  color: var(--text-strong);
  font-size: 0.9rem;
}
.detail-heading-copy {
  display: grid;
  gap: 0.18rem;
  min-width: 0;
}
.detail-heading-copy > strong {
  color: var(--text-strong);
  font-size: 0.9rem;
}
.detail-heading-copy > small {
  color: var(--text-muted);
  font-size: 0.7rem;
  line-height: 1.35;
}
.interpretation-button {
  min-height: 30px;
  padding: 0.28rem 0.62rem;
  border: 1px solid var(--border-soft);
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--text-strong);
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 700;
}
.interpretation-button:hover:not(:disabled) {
  border-color: rgba(var(--success-rgb), 0.55);
  background: rgba(var(--success-rgb), 0.1);
}
.interpretation-button:disabled {
  cursor: wait;
  opacity: 0.65;
}
.interpretation-status {
  color: var(--text-muted);
  font-size: 0.76rem;
  font-weight: 650;
}
.interpretation-loading {
  margin-top: 0.65rem;
  padding: 0.65rem 0.7rem;
  border-left: 3px solid var(--accent);
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 0.8rem;
  line-height: 1.45;
}
.interpretation-stream {
  margin-top: 0.65rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--border-soft);
}
.interpretation-stream > span {
  display: block;
  margin-bottom: 0.3rem;
  color: var(--text-soft);
  font-size: 0.72rem;
  font-weight: 700;
}
.interpretation-stream p {
  margin: 0;
  color: var(--text-strong);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.interpretation-disposition {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0.18rem 0.52rem;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  color: var(--text-soft);
  background: var(--surface-2);
  font-size: 0.72rem;
  font-weight: 750;
}
.interpretation-disposition.is-completed {
  color: var(--success);
  border-color: rgba(var(--success-rgb), 0.38);
  background: rgba(var(--success-rgb), 0.1);
}
.interpretation-disposition.is-noted,
.interpretation-disposition.is-review {
  color: var(--warning, #a16207);
  border-color: rgba(161, 98, 7, 0.36);
  background: rgba(161, 98, 7, 0.08);
}
.interpretation-disposition.is-noncanonical {
  color: var(--danger);
  border-color: rgba(var(--danger-rgb), 0.3);
  background: var(--danger-soft);
}
.interpretation-section {
  margin-top: 0.75rem;
}
.interpretation-section h4 {
  margin: 0 0 0.32rem;
  color: var(--text-soft);
  font-size: 0.76rem;
  font-weight: 750;
}
.interpretation-section p,
.interpretation-section li {
  color: var(--text-strong);
  font-size: 0.82rem;
  line-height: 1.55;
}
.interpretation-section p {
  margin: 0;
}
.interpretation-section ul {
  display: grid;
  gap: 0.28rem;
  margin: 0;
  padding-left: 1rem;
}
.interpretation-section li > span:first-child {
  display: inline;
}
.evidence-citations {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.2rem;
  margin-left: 0.35rem;
  vertical-align: baseline;
}
.evidence-citations button {
  padding: 0;
  border: 0;
  border-bottom: 1px dotted currentColor;
  background: transparent;
  color: var(--accent-strong);
  cursor: pointer;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.68rem;
  font-weight: 750;
  line-height: 1.2;
}
.evidence-citations button:hover,
.evidence-citations button:focus-visible {
  color: var(--text-strong);
  outline: none;
}
.interpretation-attention {
  padding: 0.62rem 0.7rem;
  border-left: 3px solid var(--warning, #a16207);
  background: var(--surface-2);
}
.interpretation-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.45rem;
}
.interpretation-labels span {
  padding: 0.12rem 0.38rem;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--surface-1);
  color: var(--text-soft);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.74rem;
}
.interpretation-references {
  margin-top: 0.85rem;
  padding-top: 0.65rem;
  border-top: 1px solid var(--border-soft);
}
.interpretation-references h4 {
  margin: 0 0 0.32rem;
  color: var(--text-soft);
  font-size: 0.76rem;
  font-weight: 750;
}
.interpretation-references ol {
  display: grid;
  gap: 0.42rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.interpretation-references li {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 0.1rem 0.45rem;
  color: var(--text-strong);
  font-size: 0.79rem;
  line-height: 1.45;
}
.interpretation-references .reference-marker {
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.interpretation-references a {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid var(--border-soft);
}
.interpretation-references a:hover,
.interpretation-references a:focus-visible {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.interpretation-references small {
  grid-column: 2;
  color: var(--text-muted);
  font-size: 0.68rem;
}
.interpretation-references .reference-note {
  margin: 0.5rem 0 0;
  color: var(--text-muted);
  font-size: 0.68rem;
}
.interpretation-evidence {
  margin-top: 0.75rem;
  padding-top: 0.65rem;
  border-top: 1px solid var(--border-soft);
}
.interpretation-evidence summary {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: baseline;
  gap: 0.5rem;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 0.76rem;
  font-weight: 700;
}
.interpretation-evidence summary small {
  color: var(--text-muted);
  font-size: 0.68rem;
  font-weight: 500;
}
.interpretation-evidence ul {
  display: grid;
  gap: 0.35rem;
  max-height: 440px;
  margin: 0.5rem 0 0;
  padding: 0;
  padding-right: 0.25rem;
  overflow: auto;
  list-style: none;
  scrollbar-gutter: stable;
}
.interpretation-evidence li {
  padding: 0.45rem 0.5rem;
  border-left: 2px solid transparent;
  background: rgba(var(--nav-text-rgb), 0.025);
  color: var(--text-muted);
  font-size: 0.75rem;
  line-height: 1.45;
}
.interpretation-evidence li.is-focused {
  border-left-color: var(--accent);
  background: rgba(var(--accent-rgb), 0.08);
}
.evidence-row-heading {
  display: grid;
  grid-template-columns: 25px minmax(0, 1fr);
  gap: 0.35rem;
}
.interpretation-evidence li strong {
  color: var(--text-soft);
  margin-right: 0.25rem;
}
.interpretation-evidence li small {
  display: block;
  color: var(--text-muted);
  font-size: 0.69rem;
}
.evidence-token-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.28rem;
  margin: 0.38rem 0 0.28rem 1.85rem;
}
.evidence-token-list > span {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0.08rem 0.38rem;
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text-soft);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.68rem;
}
.evidence-token-list > span.is-pair {
  border-color: rgba(var(--success-rgb), 0.28);
  color: var(--success);
}
.evidence-token-list > span.is-metric {
  border-color: rgba(var(--info-rgb), 0.28);
  color: var(--info-strong);
}
.interpretation-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
  margin-top: 0.65rem;
  color: var(--danger);
  font-size: 0.8rem;
  line-height: 1.45;
}
.trace-card {
  margin-top: 0.55rem;
  padding: 0.65rem 0.95rem;
  border-left: 4px solid var(--accent-2, #7c9cff);
  background: var(--surface-2);
  border-radius: 4px;
}
.trace-card summary {
  cursor: pointer;
  color: var(--text-soft);
  font-weight: 600;
}
.trace-header {
  margin-top: 0.55rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  color: var(--text-soft);
  font-size: 0.85rem;
}
.trace-warnings {
  margin-top: 0.45rem;
  color: var(--warning);
  font-size: 0.82rem;
}
.trace-error {
  margin-top: 0.5rem;
  color: var(--danger);
  font-size: 0.85rem;
}
.trace-loading {
  margin-top: 0.5rem;
  color: var(--text-muted);
  font-size: 0.85rem;
}
.trace-region {
  margin-top: 0.5rem;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  padding: 0.45rem 0.6rem;
}
.trace-region-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.7rem;
}
.trace-region summary {
  cursor: pointer;
  color: var(--text-soft);
  font-weight: 600;
}
.trace-region-name {
  font-size: 0.92rem;
  color: var(--text-strong);
}
.trace-region-meta {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.trace-status-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 0.13rem 0.55rem;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.01em;
  border: 1px solid transparent;
}
.trace-status-chip.is-ok {
  color: #0f5132;
  background: rgba(64, 201, 120, 0.22);
  border-color: rgba(64, 201, 120, 0.45);
}
.trace-status-chip.is-skip {
  color: #7b839a;
  background: rgba(130, 146, 171, 0.16);
  border-color: rgba(130, 146, 171, 0.36);
}
.trace-status-chip.is-preview {
  color: #7a5a00;
  background: rgba(255, 193, 7, 0.2);
  border-color: rgba(255, 193, 7, 0.42);
}
.trace-status-chip.is-error {
  color: #842029;
  background: rgba(220, 53, 69, 0.2);
  border-color: rgba(220, 53, 69, 0.42);
}
.trace-status-chip.is-neutral {
  color: var(--text-soft);
  background: rgba(124, 156, 255, 0.16);
  border-color: rgba(124, 156, 255, 0.35);
}
.trace-attempt-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 0.13rem 0.5rem;
  font-size: 0.72rem;
  color: var(--text-muted);
  border: 1px solid var(--border-soft);
  background: rgba(124, 156, 255, 0.08);
}
.trace-message {
  margin-top: 0.4rem;
  color: var(--text-strong);
  font-size: 0.84rem;
}
.trace-sub {
  margin-top: 0.4rem;
}
.trace-sub summary {
  cursor: pointer;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.trace-sub-note {
  margin-top: 0.35rem;
  padding: 0.45rem 0.55rem;
  border-radius: 6px;
  border: 1px dashed var(--border-soft);
  background: rgba(124, 156, 255, 0.08);
  color: var(--text-soft);
  font-size: 0.78rem;
}
.trace-sub-note.is-skip {
  background: rgba(130, 146, 171, 0.1);
  color: var(--text-muted);
}
.trace-sub pre {
  margin-top: 0.35rem;
  max-height: 260px;
  overflow: auto;
  padding: 0.5rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--border-soft);
  background: var(--surface-dark);
  color: var(--overlay-text, #f8fafc);
  font-size: 0.75rem;
  line-height: 1.35;
  white-space: pre-wrap;
  word-break: break-word;
}
.structure-card {
  margin-top: 0.55rem;
  padding: 0.65rem 0.95rem;
  border-left: 4px solid var(--accent);
  background: var(--surface-2);
  border-radius: 4px;
}

.agent-process {
  margin: 0;
  border-left: 1px solid var(--border-soft);
}
.agent-process:only-child {
  grid-column: 1 / -1;
  border-left: 0;
}

.agent-process-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.7rem 0;
  color: var(--text-strong);
}

.agent-process-summary-copy {
  display: grid;
  gap: 0.18rem;
  min-width: 0;
}

.agent-process-summary-copy strong {
  font-size: 0.86rem;
  font-weight: 750;
}

.agent-process-summary-copy small {
  color: var(--text-muted);
  font-size: 0.75rem;
}

.agent-process-body {
  padding: 0 0 0.75rem;
}

.process-status {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0.18rem 0.55rem;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-soft);
  font-size: 0.74rem;
  font-weight: 750;
  text-transform: capitalize;
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

.process-timeline {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.process-step {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 0.55rem;
  min-width: 0;
  padding: 0.28rem 0;
}
.process-step-button {
  position: relative;
  z-index: 1;
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 0.55rem;
  width: 100%;
  padding: 0.12rem 0.25rem 0.12rem 0;
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
.process-step.is-selected .process-step-button {
  background: rgba(var(--accent-rgb), 0.07);
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
.process-marker.is-running {
  background: var(--accent);
  animation: process-pulse 1.1s ease-in-out infinite;
}
.process-loader {
  display: inline-block;
  width: 0.72rem;
  height: 0.72rem;
  margin-left: 0.4rem;
  vertical-align: -0.1rem;
  border: 2px solid var(--border-soft);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: process-spin 0.8s linear infinite;
}
@keyframes process-spin { to { transform: rotate(360deg); } }
@keyframes process-pulse { 50% { box-shadow: 0 0 0 4px rgba(var(--accent-rgb), 0.16); } }

.process-step.is-ok .process-marker {
  background: var(--success);
}

.process-step.is-warning .process-marker {
  background: var(--warning, #a16207);
}

.process-step-copy {
  display: grid;
  grid-template-columns: minmax(130px, 0.32fr) minmax(0, 1fr);
  gap: 0.7rem;
  min-width: 0;
  line-height: 1.4;
}

.process-step-copy strong {
  color: var(--text-soft);
  font-size: 0.78rem;
}

.process-step-copy span {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.technical-details {
  margin-top: 0.7rem;
  padding-top: 0.65rem;
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
  gap: 0.6rem 1rem;
  margin-top: 0.65rem;
}

.technical-group {
  display: grid;
  gap: 0.25rem;
  min-width: 0;
}

.technical-label {
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 650;
}

.technical-group code,
.probability-list {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text-strong);
  font-size: 0.76rem;
}

.probability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.75rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.evidence-only-state {
  display: grid;
  gap: 0.25rem;
  margin: 0.75rem 0 0;
  padding: 0.75rem 0;
  border-top: 1px solid var(--border-soft);
}

.evidence-only-state strong {
  color: var(--text-strong);
  font-size: 0.88rem;
}

.evidence-only-state span {
  color: var(--text-muted);
  font-size: 0.8rem;
  line-height: 1.45;
}

@media (max-width: 680px) {
  .process-step-copy {
    grid-template-columns: 1fr;
    gap: 0.12rem;
  }

  .technical-grid {
    grid-template-columns: 1fr;
  }
}
.structure-card summary {
  cursor: pointer;
  color: var(--text-soft);
  font-weight: 600;
}
.structure-meta {
  color: var(--text-muted);
  font-weight: 500;
  margin-left: 0.35rem;
}
.structure-seq {
  display: block;
  margin-top: 0.55rem;
  font-family: "Consolas", monospace;
  font-size: 0.78rem;
  color: var(--text-strong);
  white-space: pre-wrap;
  word-break: break-all;
}
.edit-panel {
  margin: 0.75rem 0 1rem;
  padding: 0.65rem 0.85rem;
  border-radius: 8px;
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
}
.edit-panel summary {
  cursor: pointer;
  color: var(--text-soft);
  font-weight: 600;
  margin-bottom: 0.5rem;
}
.edit-grid {
  display: grid;
  gap: 0.6rem;
}
.edit-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.edit-label {
  min-width: 120px;
  color: var(--text-soft);
  font-size: 0.85rem;
}
.edit-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.82rem;
  color: var(--text-soft);
}
.edit-note {
  font-size: 0.78rem;
  color: var(--text-muted);
}
.edit-select,
.edit-input {
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 0.85rem;
}
select.edit-select {
  padding-inline-end: 24px;
}
.edit-input {
  width: 74px;
  text-transform: uppercase;
  font-family: "Consolas", monospace;
  text-align: center;
}
.edit-btn {
  border: none;
  background: var(--success);
  color: #fff;
  padding: 4px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
}
.edit-btn.ghost {
  background: var(--surface-3);
  color: var(--text-strong);
  border: 1px solid var(--border-soft);
}
.edit-message {
  margin-top: 0.5rem;
  color: var(--info-strong);
  font-size: 0.85rem;
}
.edit-hint {
  margin-top: 0.35rem;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.alignment-pane,
.radial-pane {
  background: transparent;
}
.alignment-tables {
  margin-bottom: 1rem;
}
.result-content {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  flex-wrap: wrap;
}
.result-content .alignment-pane {
  flex: 1 1 0;
}
.result-content .alignment-pane .alignment-tables {
  margin-bottom: 0;
}
.result-content .radial-pane {
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  max-width: 620px;
  overflow: hidden;
}
.result-content .radial-pane .radial {
  margin-bottom: 0;
}
@media (max-width: 980px) {
  .run-details-panel {
    grid-template-columns: 1fr;
  }
  .agent-process {
    border-top: 1px solid var(--border-soft);
    border-left: 0;
  }
  .result-content {
    flex-direction: column;
  }
  /* 竖排时 flex-basis:0 作用在高度上；radial-pane 又带 overflow:hidden，
     会失去 min-height:auto 的自动下限，整块被压成 0 高后裁掉三叶草。
     竖排下改为按内容高度排布。 */
  .result-content .radial-pane {
    flex: 0 0 auto;
    max-width: 100%;
    overflow: visible;
  }
}

@media (max-width: 768px) {
  .merge-row {
    margin-bottom: 1rem;
  }
  .btn-download-big {
    width: 100%;
    padding: 1rem;
    min-height: 48px;
    font-size: 0.95rem;
  }
  .viz-toggle {
    width: 100%;
    margin-bottom: 1rem;
  }
  .viz-btn {
    flex: 1;
    min-height: 44px;
    padding: 0.75rem;
  }
  .select-panel {
    margin-bottom: 1rem;
  }
  .select-panel summary {
    padding: 0.75rem;
    min-height: 44px;
    display: flex;
    align-items: center;
  }
  .select-actions {
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.75rem;
  }
  .select-btn {
    width: 100%;
    min-height: 44px;
    justify-content: center;
  }
  .select-list {
    padding: 0.75rem;
    gap: 0.5rem;
  }
  .select-item {
    padding: 0.75rem;
    min-height: 44px;
    display: flex;
    align-items: center;
  }
  .page-controls {
    flex-direction: column;
    gap: 0.75rem;
    padding: 0.75rem;
  }
  .page-size {
    width: 100%;
    flex-direction: column;
    align-items: stretch;
    gap: 0.4rem;
  }
  .page-size-select {
    width: 100%;
    min-height: 44px;
    font-size: 16px;
  }
  .row-toggle {
    width: 100%;
    min-height: 44px;
    display: flex;
    align-items: center;
  }
  .master-details-button {
    width: 44px;
    height: 44px;
    align-self: flex-start;
  }
  .run-details-toggle {
    width: 100%;
    min-height: 44px;
    padding: 0.65rem 0.75rem;
  }
  .run-details-panel {
    padding: 0;
  }
  .detail-column,
  .scientific-interpretation {
    padding: 0.8rem 0.75rem;
  }
  .interpretation-evidence summary {
    grid-template-columns: 1fr;
    gap: 0.1rem;
  }
  .interpretation-evidence ul {
    max-height: 360px;
  }
  .page-btn {
    min-height: 44px;
    min-width: 44px;
    padding: 0.75rem;
  }
  .page-info {
    text-align: center;
    font-size: 0.85rem;
  }
  .result-block {
    margin-bottom: 1rem;
  }
  .result-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.75rem;
  }
  .result-name {
    font-size: 0.9rem;
    width: 100%;
  }
  .result-actions {
    width: 100%;
    flex-direction: column;
    gap: 0.5rem;
  }
  .result-actions .btn-secondary {
    width: 100%;
    min-height: 44px;
    justify-content: center;
  }
  .result-summary {
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.75rem;
  }
  .summary-item {
    width: 100%;
    flex-direction: column;
    align-items: stretch;
    gap: 0.2rem;
  }
  .summary-label {
    font-size: 0.78rem;
    font-weight: 600;
  }
  .summary-value {
    font-size: 0.82rem;
  }
  .tree-panel {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .tree-loading {
    padding: 1rem;
    min-height: 44px;
    font-size: 0.9rem;
  }
  .tree-error {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
    padding: 0.75rem;
  }
}

@media (max-width: 480px) {
  .viz-toggle {
    flex-direction: column;
  }
  .viz-btn {
    width: 100%;
  }
  .page-controls {
    gap: 0.5rem;
  }
  .result-header {
    padding: 0.5rem;
  }
  .result-summary {
    padding: 0.5rem;
  }
}

:deep(.png-export-transparent) {
  background: transparent !important;
}
:deep(.png-export-transparent.trna-container) {
  background: transparent !important;
  box-shadow: none !important;
}
:deep(.png-export-transparent .trna-container) {
  background: transparent !important;
  box-shadow: none !important;
}
:deep(.alignment-wrappers.png-export-transparent) {
  width: max-content !important;
  max-width: none !important;
}
:deep(.png-export-transparent .alignment-wrappers) {
  width: max-content !important;
  max-width: none !important;
}
:deep(.png-export-transparent .alignment-table) {
  backdrop-filter: none !important;
  box-shadow: none !important;
}
</style>
