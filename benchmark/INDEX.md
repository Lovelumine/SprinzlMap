# Benchmark Layout

The canonical gold standard is stored in MySQL. The repository keeps neither a
`benchmark/gold/` data directory nor a `benchmark/runs/` output directory:
offline exports are temporary, identified by their query parameters and the
SHA-256 recorded in a run manifest.

## Current Runs

Run output is written case by case into MySQL (index) and MinIO (traces). See
[`../docs/architecture/benchmark-run-storage.md`](../docs/architecture/benchmark-run-storage.md)
for tables, prefixes, endpoints and how to materialize a copy.

Main cohort and ablation controls, all scored against the same frozen gold
(`gold_sha256` `fe94efd2…`) and the same 2,530-sequence roster
(`selected_seqnames_sha256` `6f00cb8e…`):

- `20261020-164533-deepseekv4-flash-guangdong` — the manuscript's main run:
  language-model planner over the tool Harness, 21 recorded technical retry
  attempts, `sealed`.
- `20261023-041911-control-cm-direct` — covariance-model alignment with no
  Harness, `sealed`.
- `20261023-043053-control-harness-cm-root` — Harness with a covariance-model
  starting candidate, retrieval/DP fallback, no language model, `sealed`.
- `20261023-043053-control-harness-retrieval-dp` — Harness with template
  retrieval/DP candidate and no language model (measures what happens without
  structural evidence).
- `20261023-043053-control-fixed-planner` — main cohort with a deterministic
  action selector in place of the language-model planner.

No other batch is stored. `20261019-173732-deepseekv4-flash-guangdong` (2,531
cases, a merged composite) and the model-only control
`20261023-0515-control-raw-llm` (72 of 2,530 cases, never finished) were deleted
from MySQL and MinIO on 2026-10-23. The 1019 batch contributed no manuscript
number; the model-only control's figures in the manuscript therefore have no
stored evidence and have to be re-run before they can be cited again.

Every run above is written case by case to MySQL and MinIO and carries its own
`inputs/` objects, so a comparison can be rebuilt on another machine. List them
with `python benchmark/scripts/run_flow_control_suite.py --status`.

## Scripts and Evidence

- `scripts/`: benchmark runner and audit/reporting tools.
- `scripts/census_variable_pattern_ceiling.py`: measures, without any model call,
  how much of the residual variable-region error a regenerated coordinate row
  could still reach; results in
  [`../research/paper/results/variable_pattern_ceiling/`](../research/paper/results/variable_pattern_ceiling/).
- `REPORT_SPEC.md`: report and figure output format.
- `RULES.md`: numbering benchmark execution rules.

Historical runs and diagnostic outputs were removed from the working tree; they
remain reachable through Git history only. The curation API and website open the
main cohort by default and take any other registered run id as a query
parameter; an unregistered run id returns 404, and predictions are never loaded
from an external archive.

The manuscript's fixed Figure 2 cohort contains 2,530 cases; see
[`../research/paper/results/figure2/README.md`](../research/paper/results/figure2/README.md).
