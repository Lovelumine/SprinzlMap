<template>
  <main class="home">
    <section class="hero-workspace">
      <div class="hero-copy">
        <p class="eyebrow">SprinzlMap</p>
        <h1>Verifiable Sprinzl numbering for tRNA research</h1>
        <p class="hero-desc">
          Structure-aware numbering with a verifiable Agent and scientific tool harness.
        </p>
        <div class="hero-actions">
          <a class="btn btn-primary" href="/sprinzl-align.html">Start Numbering</a>
          <a class="btn btn-outline" href="/reference.html">View API</a>
        </div>
      </div>
      <div class="hero-preview">
        <HomeAlignmentPreview variant="hero" />
      </div>
    </section>

    <section class="capability-band">
      <article v-for="cap in capabilities" :key="cap.title" class="cap-item">
        <h3>{{ cap.title }}</h3>
        <p>{{ cap.desc }}</p>
        <a v-if="cap.link" :href="cap.link">{{ cap.linkText }} &rarr;</a>
      </article>
    </section>

    <section class="workflow-strip">
      <div class="workflow-inner">
        <div v-for="step in steps" :key="step.num" class="wf-step">
          <span class="wf-num">{{ step.num }}</span>
          <span class="wf-text">{{ step.title }}</span>
        </div>
      </div>
    </section>

    <section class="api-strip">
      <div class="api-inner">
        <div class="api-text">
          <h2>Programmatic access</h2>
          <p>REST endpoints for batch alignment, template export, and result retrieval.</p>
        </div>
        <a class="btn btn-outline" href="/reference.html">API Reference &rarr;</a>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import HomeAlignmentPreview from "./HomeAlignmentPreview.vue";

const capabilities = [
  {
    title: "Sprinzl Numbering",
    desc: "Structure-homologous candidates, Agent-guided tool selection, and deterministic validation.",
    link: "/sprinzl-align.html",
    linkText: "Open Numbering Workspace",
  },
  {
    title: "Reference Comparison",
    desc: "Compare engineered tRNA sequences against a shared reference in Sprinzl coordinates.",
    link: "/design-bench.html",
    linkText: "Open Comparison Workspace",
  },
  {
    title: "Agent Diagnostics",
    desc: "Tool decisions, candidate observations, guard outcomes, and scientific provenance.",
  },
  {
    title: "Batch & Export",
    desc: "Align hundreds of sequences via batch endpoints. Export FASTA, CSV, and structure views.",
    link: "/reference.html",
    linkText: "API docs",
  },
];

const steps = [
  { num: "01", title: "Input contract" },
  { num: "02", title: "Agent plans" },
  { num: "03", title: "Scientific tools" },
  { num: "04", title: "Hard guards" },
  { num: "05", title: "Coordinates" },
];
</script>

<style scoped>
:global(body) {
  margin: 0;
}

.home {
  display: flex;
  flex-direction: column;
  color: var(--text-strong);
  width: 100%;
}

/* ── Hero workspace ── */
.hero-workspace {
  display: grid;
  grid-template-columns: minmax(320px, 0.42fr) minmax(0, 0.58fr);
  gap: 28px;
  align-items: start;
  max-width: 1400px;
  margin: 0 auto;
  padding: 36px 32px 32px;
  width: 100%;
  box-sizing: border-box;
}

.hero-copy {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 84px 0 10px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--accent-strong);
  margin: 0;
}

.hero-copy h1 {
  margin: 0;
  max-width: 620px;
  font-size: clamp(2rem, 3.4vw, 3rem);
  font-weight: 760;
  line-height: 1.08;
}

.hero-desc {
  margin: 0;
  max-width: 540px;
  font-size: 1rem;
  line-height: 1.65;
  color: var(--text-soft);
}

.hero-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.hero-preview {
  min-width: 0;
  align-self: stretch;
}

/* ── Capability band ── */
.capability-band {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--border-soft);
  border-top: 1px solid var(--border-soft);
  border-bottom: 1px solid var(--border-soft);
}

.cap-item {
  background: var(--surface-1);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cap-item h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.cap-item p {
  margin: 0;
  font-size: 0.85rem;
  color: var(--text-muted);
  line-height: 1.5;
}

.cap-item a {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--accent-strong);
  text-decoration: none;
  margin-top: auto;
  padding-top: 6px;
}

.cap-item a:hover {
  color: var(--accent);
}

/* ── Workflow strip ── */
.workflow-strip {
  padding: 30px 32px;
}

.workflow-inner {
  max-width: 1120px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}

.wf-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  border-radius: 999px;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

.wf-num {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--accent);
  opacity: 0.6;
}

.wf-text {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text-soft);
}

/* ── API strip ── */
.api-strip {
  padding: 0 32px 72px;
}

.api-inner {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 32px;
  border-radius: 12px;
  background: var(--surface-1);
  border: 1px solid var(--border-soft);
  box-shadow: var(--shadow-soft);
}

.api-text h2 {
  margin: 0 0 6px;
  font-size: 1.2rem;
  font-weight: 600;
}

.api-text p {
  margin: 0;
  font-size: 0.9rem;
  color: var(--text-muted);
}

/* ── Buttons ── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  padding: 10px 22px;
  font-weight: 600;
  font-size: 0.9rem;
  text-decoration: none;
  transition: transform 0.15s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.btn-primary {
  background: var(--accent);
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.3);
}

.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(var(--accent-rgb), 0.4);
}

.btn-outline {
  border: 1px solid var(--border-strong);
  color: var(--text-strong);
  background: var(--surface-1);
}

.btn-outline:hover {
  transform: translateY(-1px);
  border-color: var(--accent);
}

/* ── Responsive ── */
@media (min-width: 601px) and (max-width: 960px) {
  .hero-workspace {
    grid-template-columns: 1fr;
    padding: 24px 20px;
  }

  .hero-copy {
    padding: 40px 0 20px;
  }

  .capability-band {
    grid-template-columns: repeat(2, 1fr);
  }

}

@media (max-width: 600px) {
  .hero-workspace {
    grid-template-columns: 1fr;
    padding: 20px 16px;
    gap: 20px;
  }

  .hero-copy {
    padding: 20px 0 10px;
  }

  .hero-copy h1 {
    font-size: clamp(1.75rem, 8vw, 2.25rem);
    line-height: 1.15;
  }

  .hero-desc {
    font-size: 0.95rem;
    line-height: 1.6;
  }

  .hero-actions {
    flex-direction: column;
    gap: 12px;
  }

  .btn {
    width: 100%;
    padding: 14px 24px;
    min-height: 44px;
    text-align: center;
    justify-content: center;
  }

  .capability-band {
    grid-template-columns: 1fr;
  }

  .cap-item {
    padding: 20px 16px;
  }

  .workflow-strip {
    padding: 24px 16px;
  }

  .workflow-inner {
    flex-direction: column;
    gap: 8px;
  }

  .wf-step {
    width: 100%;
    justify-content: center;
    padding: 12px 16px;
  }

  .api-strip {
    padding: 0 16px 48px;
  }

  .api-inner {
    flex-direction: column;
    text-align: center;
    padding: 24px 16px;
    gap: 16px;
  }

  .api-text h2 {
    font-size: 1.1rem;
  }

  .api-text p {
    font-size: 0.85rem;
  }
}
</style>
