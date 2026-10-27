# SprinzlMap

SprinzlMap is an auditable scientific-agent implementation for assigning a
Sprinzl coordinate to every residue of a mature tRNA sequence. The language
model selects bounded repair actions from structured observations. Scientific
tools generate coordinate candidates, and deterministic validators decide
whether a candidate may be accepted or must be flagged for review.

This repository contains the source code used for the manuscript:

**SprinzlMap: an auditable scientific agent for tRNA Sprinzl-coordinate
mapping**

## Scope

The supported task is Sprinzl-coordinate recovery for mature, near-mature and
cloverleaf-compatible tRNAs. Gene discovery, arbitrary RNA alignment, and
unconstrained sequence generation are outside this repository's scope.

The core execution contract is:

1. deterministic input checks establish sequence type and structural context;
2. covariance models and template/regional dynamic programming produce
   candidate coordinates;
3. the language-model planner chooses only allowlisted supplementary tools;
4. hard guards validate sequence preservation, coordinate order, insertion
   continuity, structural pairing, CCA state and anticodon anchoring;
5. `accepted` and review-flagged outputs retain their candidates, tool
   observations, rule checks and provenance.

## Repository contents

- `APP/backend/`: Flask API, agent harness, scientific tools, validation rules,
  benchmark infrastructure and gold-curation service.
- `APP/web/`: Vue 3 source for the sequence workspace, comparison workspace and
  gold-curation interface.
- `benchmark/`: benchmark runner, control workflows and summary utilities.
- `research/paper/scripts/`: scripts used to compute manuscript statistics,
  build the Source Data workbook and render figures.
- `research/third_party/trnaviz/`: the four unmodified numbering covariance
  models used by the current implementation.
- `tests/`: tests for the agent contracts, alignment rules, benchmark protocol
  and persistence layer.

Large benchmark data, frozen model outputs, source data and AlphaFold 3 files
are distributed separately in the versioned data archive:

`Data DOI: TO BE ASSIGNED`

## Requirements

Python 3.10 or newer is recommended. Core Python dependencies are listed in
`requirements.txt`. Figure regeneration has additional optional dependencies in
`requirements-figures.txt`; some NMR/cryo-EM rendering scripts require a local
PyMOL installation.

External executables used by the full workflow include:

- Infernal 1.1.5 (`cmalign`) for the numbering covariance models;
- tRNAscan-SE 2.0 for structural annotation;
- Parasail through its Python package;
- LibreOffice and a LaTeX installation only for document/figure export.

## Data installation

Download and extract the Zenodo data archive into `artifacts/`. For the full
backend workflow, copy the template table to the path expected by the code:

```bash
mkdir -p data
cp artifacts/templates/tRNAs_flat.tsv data/tRNAs_flat.tsv
```

The archive also contains the 2,530-sequence cohort, residue-level reference
coordinates, frozen per-case outputs, run manifests, source data for Figs.
2-6, AlphaFold 3 models and confidence metrics, and experimental readthrough
measurements.

## Run the backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=APP
cp .env.example .env
python -m backend.app --host 0.0.0.0 --port 5679
```

LLM credentials, database credentials and object-store credentials must be
provided through environment variables or an untracked local `.env` file. No
credentials are included in this repository.

## Run the web application

```bash
cd APP/web
pnpm install
pnpm dev
```

## Test

```bash
PYTHONPATH=APP python -m pytest tests -q
```

Tests that require the full template table, MySQL or MinIO are skipped only
when their external dependency is unavailable.

## Reproducibility

The benchmark is based on a fixed 2,530-sequence cohort. Predictions are frozen
before scoring, and scores are recomputed against the archived reference
coordinates. The main run and four control runs are identified by:

- `20261020-164533-deepseekv4-flash-guangdong`
- `20261023-041911-control-cm-direct`
- `20261023-043053-control-harness-cm-root`
- `20261023-043053-control-harness-retrieval-dp`
- `20261023-043053-control-fixed-planner`

See `research/paper/scripts/README.md` and the data archive for the exact source
files and hashes.

## Citation

Citation metadata are provided in `CITATION.cff`. Please cite both the software
release and the associated data DOI.

## Third-party material

The numbering covariance models under `research/third_party/trnaviz/` are
unmodified files from the tRNAviz project. They retain their upstream
GPL-3.0 license in that directory. See `THIRD_PARTY_NOTICES.md`.
