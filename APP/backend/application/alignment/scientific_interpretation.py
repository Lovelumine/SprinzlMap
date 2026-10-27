"""Evidence-grounded interpretation for a completed tRNA mapping.

This module sits after coordinate selection.  It never changes coordinates or
the system disposition: it packages observations from the completed run and
asks the configured model to explain those observations in a user-selected language.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import logging
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Mapping

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.variable_boundary import assess_variable_bridge
from backend.domain.literature import topics as literature_topics
from backend.infra.llm.json_client import JsonCompletionError, StructuredJsonLLMClient


ROOT = Path(__file__).resolve().parents[4]
LOGGER = logging.getLogger(__name__)
CACHE_PATH = ROOT / "logs" / "scientific_interpretations.jsonl"
SCHEMA_VERSION = "scientific-interpretation-v5"
_CACHE_LIMIT = 2048
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOADED = False

# 生成解释时的 token 额度。mimo-v2.5-pro 会把思考 token 计入同一额度，
# 2000 时实测 24% 的请求思考耗尽额度导致正文为空/半截；8192 已实测 15/15 通过。
# 取 16384 作为余量：模型上限为 128K，但额度按“实际生成”计费，未用部分不花钱，
# 因此留足余量只增加安全边际，不增加成本。
# 注意：StructuredJsonLLMClient 会用 min(此处, LLM_MAX_TOKENS) 收敛，故 .env 需同步放宽。
_INTERPRETATION_MAX_TOKENS = 16384

_VALID_BASES = frozenset({"A", "C", "G", "U", "N"})
_INSERTION_RE = re.compile(r"^(.+?)i(\d+)$")
_VARIABLE_STEM5_RE = re.compile(r"^V1([1-7])$")
_VARIABLE_STEM3_RE = re.compile(r"^V2([1-7])$")
_EXPLICIT_COORDINATE_RE = re.compile(
    r"(?:sprinzl\s*)?(?:coordinate(?:s)?|position(?:s)?|label(?:s)?|anchor(?:s)?|"
    r"koordinate(?:n)?|position(?:en)?|repere(?:s)?|coordonnees?|"
    r"坐标|位点|编号|锚点)"
    r"\s*(?:at|from|of|is|are|the|last|standard|bei|von|a|de|为|是|在|\()?\s*"
    r"([VD]?\d+(?:i\d+)?)(?:\s*(?:-|\u2013|\u2014|to|through|bis|a|至|到)\s*([VD]?\d+(?:i\d+)?))?",
    re.IGNORECASE,
)
_DISTINCTIVE_COORDINATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:V\d+|D\d+|\d+i\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_LANGUAGES = {
    "en": {
        "name": "English",
        "instruction": "Write every narrative field in clear scientific English.",
    },
    "zh-CN": {
        "name": "Simplified Chinese",
        "instruction": "Write every narrative field in clear, natural Simplified Chinese; keep Sprinzl labels unchanged.",
    },
    "de": {
        "name": "German",
        "instruction": "Write every narrative field in clear scientific German; keep Sprinzl labels unchanged.",
    },
    "fr": {
        "name": "French",
        "instruction": "Write every narrative field in clear scientific French; keep Sprinzl labels unchanged.",
    },
}

_REGION_LABELS = {
    "Aminoacyl arm 5' end": "5' acceptor arm",
    "D loop + D stem": "D region",
    "Anticodon loop + Anticodon stem": "anticodon arm and loop",
    "Variable loop": "variable region",
    "T loop + T stem": "T arm and loop",
    "Aminoacyl arm 3' end": "3' acceptor end",
}

_DISPOSITIONS = {
    "completed": "Completed",
    "completed_with_note": "Completed with note",
    "review_required": "Review required",
    "non_canonical_structure": "Non-canonical structure",
}

# References are attached after the narrative exists.  The interpretation model may
# only restate recorded evidence, so literature is a separate field for the reader.
_DEFAULT_CITATION_LIMIT = 8
_POSTERIOR_SIMILARITY_BAND = 0.95

_TOOL_LABELS = {
    "align_numbering_cm": "covariance-model structural alignment",
    "align_legacy_reference": "template-guided alignment",
    "reconcile_variable_boundary_with_structure": "independent variable-region structure analysis",
    "repair_acceptor_tail_with_structure": "acceptor-end structure analysis",
    "refine_boundaries": "local boundary analysis",
    "run_region_dp": "regional dynamic-programming alignment",
    "retrieve_template": "template retrieval",
    "search_literature": "reference lookup on the flagged region",
    "propose_coordinate_revision": (
        "model-proposed relabelling of the flagged regions, accepted only when "
        "the hard guards pass and the candidate scores higher"
    ),
}


class ScientificInterpretationError(RuntimeError):
    """Raised when an explanation cannot be generated or validated."""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_base(value: Any) -> str:
    base = str(value or "").strip().upper().replace("T", "U")
    return base if base in _VALID_BASES else "-"


def _normalise_sequence(value: Any) -> str:
    return "".join(
        base
        for base in str(value or "").upper().replace("T", "U")
        if base in _VALID_BASES
    )


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_interpretation_language(value: Any) -> str:
    language = str(value or "en").strip() or "en"
    aliases = {
        "en-US": "en",
        "en-GB": "en",
        "zh": "zh-CN",
        "zh_CN": "zh-CN",
        "zh-Hans": "zh-CN",
    }
    language = aliases.get(language, language)
    if language not in _LANGUAGES:
        raise ValueError(
            "Field `language` must be one of: " + ", ".join(_LANGUAGES)
        )
    return language


def _fact(
    facts: list[dict[str, Any]],
    code: str,
    text: str,
    *,
    source: str,
    data: Mapping[str, Any] | None = None,
) -> str:
    fact_id = f"E{len(facts) + 1}"
    facts.append(
        {
            "id": fact_id,
            "code": code,
            "text": text,
            "source": source,
            "data": dict(data or {}),
        }
    )
    return fact_id


def _occupied_coordinates(labels: Iterable[Any], bases: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    residue_index = 0
    for column_index, (raw_label, raw_base) in enumerate(zip(labels, bases), start=1):
        base = _safe_base(raw_base)
        label = str(raw_label or "").strip()
        if not label or base == "-":
            continue
        residue_index += 1
        try:
            region = _REGION_LABELS.get(number_to_region(label), "unclassified region")
        except ValueError:
            region = "unclassified region"
        rows.append(
            {
                "residue_index": residue_index,
                "column_index": column_index,
                "label": label,
                "base": base,
                "region": region,
            }
        )
    return rows


def _coordinate_rows(
    labels: Iterable[str],
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    wanted = set(labels)
    return [
        {
            "label": str(row.get("label") or ""),
            "base": str(row.get("base") or ""),
            "residue_index": int(row.get("residue_index") or 0),
        }
        for row in rows
        if str(row.get("label") or "") in wanted
    ]


def _structure_pair_labels(
    structure: Mapping[str, Any],
    occupied_rows: Iterable[Mapping[str, Any]],
) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for item in _as_list(structure.get("id_pairs")):
        record = _as_dict(item)
        left = str(record.get("left_id") or "").strip()
        right = str(record.get("right_id") or "").strip()
        if left and right and left != right:
            pairs.add(frozenset((left, right)))
    if pairs:
        return pairs

    # The curation cache stores one simplified structure token per residue.
    # Recover its nested pairs against the current candidate without consulting
    # A/C reference coordinates.
    rows = [dict(item) for item in occupied_rows]
    raw_tokens = _as_list(structure.get("structure_by_residue"))
    tokens = [str(item or "").strip() for item in raw_tokens]
    if len(tokens) != len(rows):
        return pairs
    stack: list[str] = []
    for row, token in zip(rows, tokens):
        label = str(row.get("label") or "").strip()
        if token in {">", "(", "{", "["}:
            stack.append(label)
        elif token in {"<", ")", "}", "]"} and stack:
            left = stack.pop()
            if left and label and left != label:
                pairs.add(frozenset((left, label)))
    return pairs


def _tool_summary(agent: Mapping[str, Any]) -> list[str]:
    trace = _as_list(agent.get("trace_events"))
    actions: list[str] = []
    for item in trace:
        event = _as_dict(item)
        raw = str(
            event.get("tool")
            or event.get("tool_name")
            or event.get("action_type")
            or event.get("action_id")
            or ""
        ).strip()
        label = _TOOL_LABELS.get(raw)
        if label and label not in actions:
            actions.append(label)
    return actions[:5]


def _system_disposition(
    *,
    agent: Mapping[str, Any],
    scientific: Mapping[str, Any],
    warnings: Iterable[Any],
    variable_bridge: Mapping[str, Any],
) -> str:
    route = str(agent.get("route") or "").strip().lower()
    specialized = _as_dict(scientific.get("specialized_route"))
    specialized_route = str(specialized.get("route") or "").strip().lower()
    if bool(agent.get("out_of_scope")) or route in {
        "mitochondrial",
        "selenocysteine",
        "archaeal_ncintron",
    } or specialized_route in {"mitochondrial", "selenocysteine", "archaeal_ncintron"}:
        return "non_canonical_structure"

    summary = _as_dict(agent.get("work_state_summary"))
    disposition = str(
        summary.get("disposition") or agent.get("disposition") or ""
    ).strip().lower()
    guard = agent.get("hard_guard_passed")
    if guard is None:
        guard = summary.get("best_valid")
    bridge_status = str(variable_bridge.get("status") or "").strip().lower()
    warning_text = " ".join(str(item or "").lower() for item in warnings)
    unresolved = any(
        marker in warning_text
        for marker in ("unresolved", "requires_review", "review_required", "investigate")
    )
    if disposition in {"investigate", "abstained"} or guard is False or bridge_status in {
        "conflict",
        "requires_review",
    } or unresolved:
        return "review_required"
    if list(warnings):
        return "completed_with_note"
    return "completed"


def build_interpretation_facts(
    *,
    sequence: str,
    labels: Iterable[Any],
    bases: Iterable[Any],
    structure: Mapping[str, Any] | None = None,
    agent: Mapping[str, Any] | None = None,
    scientific_provenance: Mapping[str, Any] | None = None,
    warnings: Iterable[Any] = (),
    record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an answer-free, provenance-bearing evidence contract."""

    normalized_sequence = _normalise_sequence(sequence)
    labels_list = [str(item or "").strip() for item in labels]
    bases_list = [_safe_base(item) for item in bases]
    structure_data = _as_dict(structure)
    agent_data = _as_dict(agent)
    scientific = _as_dict(scientific_provenance)
    record_data = _as_dict(record)
    warning_list = [str(item or "").strip() for item in warnings if str(item or "").strip()]
    rows = _occupied_coordinates(labels_list, bases_list)
    facts: list[dict[str, Any]] = []

    _fact(
        facts,
        "input_sequence",
        f"The submitted sequence contains {len(normalized_sequence)} nucleotides.",
        source="input",
        data={"sequence_length": len(normalized_sequence)},
    )
    mapping_count = len(rows)
    _fact(
        facts,
        "candidate_mapping_coverage",
        f"The current candidate assigns Sprinzl-style labels to {mapping_count} residues in sequence order.",
        source="candidate_coordinates",
        data={"mapped_residue_count": mapping_count, "sequence_length": len(normalized_sequence)},
    )

    by_region: dict[str, list[str]] = {}
    for row in rows:
        by_region.setdefault(str(row["region"]), []).append(str(row["label"]))
    for region in (
        "5' acceptor arm",
        "D region",
        "anticodon arm and loop",
        "variable region",
        "T arm and loop",
        "3' acceptor end",
    ):
        region_labels = by_region.get(region, [])
        if region_labels:
            _fact(
                facts,
                f"region_{region.replace(' ', '_').replace(chr(39), '')}",
                f"The candidate contains {len(region_labels)} labelled residues in the {region}.",
                source="candidate_coordinates",
                data={
                    "region": region,
                    "labels": region_labels,
                    "coordinates": _coordinate_rows(region_labels, rows),
                },
            )

    occupied_labels = {str(row["label"]) for row in rows}
    anticodon_labels = [label for label in ("34", "35", "36") if label in occupied_labels]
    if len(anticodon_labels) == 3:
        _fact(
            facts,
            "anticodon_coordinates_present",
            "The candidate contains the three anticodon coordinates 34, 35, and 36.",
            source="candidate_coordinates",
            data={
                "labels": anticodon_labels,
                "coordinates": _coordinate_rows(anticodon_labels, rows),
            },
        )

    terminal_labels = [label for label in ("73", "74", "75", "76") if label in occupied_labels]
    if terminal_labels:
        _fact(
            facts,
            "terminal_coordinates_present",
            "The candidate contains terminal coordinates " + ", ".join(terminal_labels) + ".",
            source="candidate_coordinates",
            data={
                "labels": terminal_labels,
                "coordinates": _coordinate_rows(terminal_labels, rows),
            },
        )
    if normalized_sequence.endswith("CCA"):
        _fact(
            facts,
            "terminal_cca_observed",
            "The input sequence ends with the nucleotide triplet CCA.",
            source="input",
            data={"terminal_triplet": "CCA"},
        )

    pair_labels = _structure_pair_labels(structure_data, rows)
    structure_source = str(structure_data.get("annotation_source") or "").strip()
    secondary_structure = str(structure_data.get("secondary_structure") or "").strip()
    row_by_label = {str(row["label"]): row for row in rows}
    pair_records: list[dict[str, Any]] = []
    for pair in pair_labels:
        pair_rows = sorted(
            (row_by_label[label] for label in pair if label in row_by_label),
            key=lambda row: int(row["residue_index"]),
        )
        if len(pair_rows) != 2:
            continue
        left, right = pair_rows
        pair_records.append(
            {
                "left_label": left["label"],
                "left_base": left["base"],
                "left_residue_index": left["residue_index"],
                "right_label": right["label"],
                "right_base": right["base"],
                "right_residue_index": right["residue_index"],
            }
        )
    pair_records.sort(key=lambda item: (item["left_residue_index"], item["right_residue_index"]))
    if pair_labels:
        _fact(
            facts,
            "structure_pair_links",
            f"The available structure annotation contains {len(pair_labels)} labelled base-pair links.",
            source=structure_source or "structure_annotation",
            data={"pair_count": len(pair_labels), "pairs": pair_records},
        )
    elif secondary_structure or _as_list(structure_data.get("structure_by_residue")):
        _fact(
            facts,
            "structure_available_without_labelled_pairs",
            "A secondary-structure annotation is available, but it does not provide labelled coordinate-pair links for this candidate.",
            source=structure_source or "structure_annotation",
            data={},
        )
    else:
        _fact(
            facts,
            "structure_unavailable",
            "No secondary-structure annotation is available for this interpretation.",
            source="structure_annotation",
            data={},
        )

    paired_variable_stem = False
    for pair in pair_labels:
        if len(pair) != 2:
            continue
        left, right = tuple(pair)
        left_match = _VARIABLE_STEM5_RE.match(left)
        right_match = _VARIABLE_STEM3_RE.match(right)
        reverse_left = _VARIABLE_STEM3_RE.match(left)
        reverse_right = _VARIABLE_STEM5_RE.match(right)
        if (left_match and right_match and left_match.group(1) == right_match.group(1)) or (
            reverse_left and reverse_right and reverse_left.group(1) == reverse_right.group(1)
        ):
            paired_variable_stem = True
            break
    variable_labels = [label for label in occupied_labels if label.startswith("V")]
    if pair_labels and paired_variable_stem:
        _fact(
            facts,
            "paired_extra_variable_arm",
            "The structure annotation contains paired V-series stem coordinates, supporting an additional variable-arm stem.",
            source=structure_source or "structure_annotation",
            data={"labels": sorted(variable_labels)},
        )
    elif pair_labels:
        _fact(
            facts,
            "no_paired_extra_variable_arm",
            "The labelled structure annotation does not contain a paired V-series stem for an additional variable arm.",
            source=structure_source or "structure_annotation",
            data={"labels": sorted(variable_labels)},
        )

    variable_bridge: dict[str, Any] = {}
    try:
        variable_bridge = assess_variable_bridge(labels_list, bases_list, require_t_stem_anchor=True)
    except Exception:
        variable_bridge = {}
    bridge_status = str(variable_bridge.get("status") or "")
    bridge_labels = [str(item) for item in _as_list(variable_bridge.get("observed_labels"))]
    if bridge_status in {"conflict", "requires_review"}:
        count = int(variable_bridge.get("bridge_residue_count") or len(bridge_labels))
        _fact(
            facts,
            "variable_bridge_requires_review",
            (
                f"The interval after the last variable-region anchor and before coordinate 49 contains "
                f"{count} occupied residues ({', '.join(bridge_labels) or 'no labels reported'}); "
                "the candidate requires a variable-region boundary review."
            ),
            source="variable_boundary_rule",
            data={
                "status": bridge_status,
                "labels": bridge_labels,
                "residue_indices": _as_list(variable_bridge.get("residue_indices")),
                "coordinates": _coordinate_rows(bridge_labels, rows),
            },
        )
    insertions = [str(row["label"]) for row in rows if _INSERTION_RE.match(str(row["label"]))]
    if insertions:
        insertion_descriptions = []
        insertion_rows: list[dict[str, Any]] = []
        for label in insertions[:12]:
            match = _INSERTION_RE.match(label)
            if match:
                insertion_descriptions.append(f"{label} after {match.group(1)}")
            row_index = next(
                (index for index, row in enumerate(rows) if str(row["label"]) == label),
                -1,
            )
            row = rows[row_index] if row_index >= 0 else {}
            previous_row = rows[row_index - 1] if row_index > 0 else {}
            next_row = rows[row_index + 1] if 0 <= row_index < len(rows) - 1 else {}
            insertion_rows.append(
                {
                    "label": label,
                    "base": row.get("base"),
                    "residue_index": row.get("residue_index"),
                    "parent_label": match.group(1) if match else "",
                    "previous_anchor": previous_row.get("label"),
                    "next_anchor": next_row.get("label"),
                }
            )
        _fact(
            facts,
            "alignment_insertions_present",
            "The candidate includes alignment-extension labels: " + ", ".join(insertion_descriptions) + ".",
            source="candidate_coordinates",
            data={"labels": insertions, "coordinates": insertion_rows},
        )

    cm_score = _as_dict(scientific.get("cm_score"))
    try:
        posterior = float(cm_score.get("average_posterior"))
    except (TypeError, ValueError):
        posterior = None
    if posterior is not None and 0.0 <= posterior <= 1.0:
        _fact(
            facts,
            "cm_average_posterior",
            f"The covariance-model mean posterior probability for the current alignment is {posterior:.3f}.",
            source="covariance_model",
            data={"average_posterior": round(posterior, 6)},
        )

    structure_meta = _as_dict(structure_data.get("structure_meta"))
    trnascan = _as_dict(scientific.get("trnascan"))
    isotype = str(structure_meta.get("trnascan_isotype") or structure_meta.get("isotype") or "").strip()
    anticodon = str(structure_meta.get("trnascan_anticodon") or structure_meta.get("anticodon") or "").strip().upper()
    if isotype or anticodon:
        pieces = []
        if isotype:
            pieces.append(f"isotype {isotype}")
        if anticodon:
            pieces.append(f"anticodon {anticodon}")
        _fact(
            facts,
            "trnascan_annotation",
            "tRNAscan-SE reported " + " and ".join(pieces) + ".",
            source="tRNAscan-SE",
            data={"isotype": isotype, "anticodon": anticodon},
        )
    elif trnascan:
        search_mode = str(trnascan.get("search_mode") or "").strip()
        if search_mode:
            _fact(
                facts,
                "trnascan_search_mode",
                f"tRNAscan-SE was run in search mode {search_mode}.",
                source="tRNAscan-SE",
                data={"search_mode": search_mode},
            )

    tools = _tool_summary(agent_data)
    if tools:
        _fact(
            facts,
            "agent_tool_trace",
            "The recorded analysis used " + ", ".join(tools) + ".",
            source="agent_trace",
            data={"tools": tools},
        )

    work_state = _as_dict(agent_data.get("work_state_summary"))
    soft_risk_review = _as_dict(work_state.get("soft_risk_review"))
    for reason in _as_list(soft_risk_review.get("reasons")):
        reason_data = _as_dict(reason)
        reason_code = str(reason_data.get("code") or "").strip()
        if not reason_code:
            continue
        labels = [
            str(item)
            for field in ("observed_labels", "observed_tail_labels", "bridge_observed")
            for item in _as_list(reason_data.get(field))
            if str(item or "") in occupied_labels
        ]
        labels = list(dict.fromkeys(labels))
        region = str(reason_data.get("region") or "").strip()
        missing_labels: list[str] = []
        if reason_code == "cm_average_posterior_below_threshold":
            try:
                observed = float(reason_data.get("observed"))
                threshold = float(reason_data.get("threshold"))
            except (TypeError, ValueError):
                continue
            text = (
                f"The recorded review trigger compared a mean CM posterior of {observed:.3f} "
                f"with the configured review threshold of {threshold:.3f}."
            )
        elif reason_code == "cm_variable_boundary_structure_ambiguity":
            bridge = [str(item) for item in _as_list(reason_data.get("bridge_observed"))]
            text = (
                "The recorded variable-boundary review trigger covers occupied coordinates "
                + (", ".join(labels) or "not reported")
                + "; the observed bridge coordinates are "
                + (", ".join(bridge) or "not reported")
                + "."
            )
        elif reason_code == "cm_acceptor_tail_boundary_ambiguity":
            missing_labels = ["73"] if reason_data.get("missing_discriminator_73") else []
            text = (
                "The recorded acceptor-end review trigger covers occupied coordinates "
                + (", ".join(labels) or "not reported")
                + (" and reports no occupied coordinate 73." if reason_data.get("missing_discriminator_73") else ".")
            )
        else:
            text = (
                "The recorded review trigger "
                + reason_code.replace("_", " ")
                + (f" concerns the {region}." if region else ".")
            )
        _fact(
            facts,
            "recorded_review_trigger",
            text,
            source="run_observation",
            data={
                "reason_code": reason_code,
                "region": region,
                "labels": labels,
                "missing_labels": missing_labels,
                "coordinates": _coordinate_rows(labels, rows),
                "bridge_observed": _as_list(reason_data.get("bridge_observed")),
                "bridge_expected": _as_list(reason_data.get("bridge_expected")),
                "observed": reason_data.get("observed"),
                "threshold": reason_data.get("threshold"),
            },
        )

    if warning_list:
        _fact(
            facts,
            "run_warnings_present",
            f"The completed run returned {len(warning_list)} warning signal(s).",
            source="run_validation",
            data={"warning_count": len(warning_list), "warning_codes": warning_list},
        )

    disposition = _system_disposition(
        agent=agent_data,
        scientific=scientific,
        warnings=warning_list,
        variable_bridge=variable_bridge,
    )
    _fact(
        facts,
        "system_disposition",
        f"The rule-based system disposition is {_DISPOSITIONS[disposition]}.",
        source="deterministic_validation",
        data={"code": disposition},
    )

    candidate = {
        "sequence": normalized_sequence,
        "coordinates": [{"label": row["label"], "base": row["base"]} for row in rows],
    }
    candidate_hash = _json_hash(candidate)
    attention_codes = {
        "variable_bridge_requires_review",
        "alignment_insertions_present",
        "no_paired_extra_variable_arm",
        "recorded_review_trigger",
    }
    attention_labels = list(
        dict.fromkeys(
            str(label)
            for fact in facts
            if fact["code"] in attention_codes
            for field in ("labels", "missing_labels")
            for label in _as_list(_as_dict(fact.get("data")).get(field))
            if str(label or "") in occupied_labels or field == "missing_labels"
        )
    )
    facts_payload = {
        "schema_version": SCHEMA_VERSION,
        "record_id": str(record_data.get("id") or record_data.get("seqname") or "").strip(),
        "sequence_hash": _json_hash(normalized_sequence),
        "candidate_hash": candidate_hash,
        "system_disposition": {"code": disposition, "label": _DISPOSITIONS[disposition]},
        "candidate": {
            "sequence_length": len(normalized_sequence),
            "mapped_residue_count": mapping_count,
            "coordinates": candidate["coordinates"],
        },
        "attention_coordinate_labels": attention_labels,
        "evidence": facts,
    }
    facts_payload["cache_key"] = _json_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "candidate_hash": candidate_hash,
            "disposition": disposition,
            "evidence": facts,
        }
    )
    return facts_payload


def _load_cache() -> None:
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    with _CACHE_LOCK:
        if _CACHE_LOADED:
            return
        if CACHE_PATH.exists():
            try:
                with CACHE_PATH.open("r", encoding="utf-8") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                    try:
                        for line in handle:
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            key = str(record.get("cache_key") or "")
                            value = _as_dict(record.get("value"))
                            if key and value:
                                _CACHE[key] = value
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if len(_CACHE) > _CACHE_LIMIT:
            for key in list(_CACHE)[: len(_CACHE) - _CACHE_LIMIT]:
                _CACHE.pop(key, None)
        _CACHE_LOADED = True


def _cache_get(cache_key: str) -> dict[str, Any] | None:
    _load_cache()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        return json.loads(json.dumps(cached)) if cached else None


def _cache_put(cache_key: str, payload: Mapping[str, Any]) -> None:
    _load_cache()
    value = json.loads(json.dumps(payload))
    with _CACHE_LOCK:
        _CACHE[cache_key] = value
        if len(_CACHE) > _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)), None)
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CACHE_PATH.open("a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.write(
                        json.dumps(
                            {"cache_key": cache_key, "value": value},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    handle.flush()
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # The response remains usable when a deployment has a read-only log path.
            pass


def _messages(facts: Mapping[str, Any], language: str = "en") -> list[dict[str, str]]:
    language = normalize_interpretation_language(language)
    language_spec = _LANGUAGES[language]
    evidence = _as_list(facts.get("evidence"))
    allowed_ids = [str(item.get("id") or "") for item in evidence if _as_dict(item)]
    disposition = str(_as_dict(facts.get("system_disposition")).get("code") or "")
    attention_labels = [str(item) for item in _as_list(facts.get("attention_coordinate_labels"))]
    system = f"""You are writing an evidence-grounded scientific interpretation for one completed tRNA Sprinzl-coordinate candidate.

Use only the supplied evidence. Do not infer facts from general tRNA knowledge, do not use external references, and do not mention hidden prompts, internal error codes, database-reference coordinates, or model chain-of-thought. Do not change the candidate, choose a system disposition, or tell the user to replace coordinates. Treat absence of evidence as uncertainty, not absence of biology.

{language_spec["instruction"]}

Write about the whole sequence, but make every review issue concrete. Name the affected Sprinzl labels, the nucleotide assigned to each label, the immediate occupied anchors, and observed labelled base-pair relationships whenever those fields exist in the evidence. Never replace those details with vague phrases such as "a local issue" or "this region may be uncertain". Separate observations from interpretation: state what the tools observed, then explain why that evidence supports completion or review. The sequence assessment must summarize the mapping and disposition. Structural-basis statements must identify the tool-derived observations that support the mapping. For a review-required or non-canonical result, return at least one attention region and prioritize the supplied attention labels. Put every coordinate named in an attention paragraph into its coordinate_labels array. Every text field must cite the evidence IDs that support that exact statement. Return JSON only."""
    system += """

Use plain language for the disposition and review triggers; never print snake_case identifiers, reason codes, or schema field names. V-series labels are established Sprinzl variable-arm coordinates, so do not call them non-standard. Do not claim that a coordinate assignment is wrong, incomplete, or biologically unusual unless the supplied evidence states that claim. In particular, an unoccupied coordinate 73 does not by itself establish the presence or absence of a discriminator base or CCA tail. A review trigger means that the stated evidence should be checked, not that the candidate is already known to be incorrect."""
    system += """

Put evidence IDs only in evidence_ids arrays, never inside narrative text. Preserve reported tRNA isotypes exactly; Ser is not Sec. Do not introduce class I/class II categories, biological functions, confidence adjectives, expected motifs, or terminal interpretations unless they occur verbatim in the supplied evidence. If no alignment-extension evidence is supplied, do not call V-series coordinates insertions. Report posterior values only numerically and relative to a supplied threshold."""
    user = json.dumps(
        {
            "required_output": {
                "sequence_assessment": {"text": "string", "evidence_ids": allowed_ids[:2]},
                "structural_basis": [
                    {"text": "string", "evidence_ids": allowed_ids[:2]}
                ],
                "attention_regions": [
                    {
                        "region": "string",
                        "title": "string",
                        "text": "string",
                        "coordinate_labels": ["string"],
                        "evidence_ids": allowed_ids[:2],
                    }
                ],
            },
            "constraints": {
                "language": language_spec["name"],
                "language_code": language,
                "sequence_assessment_max_words": 90,
                "structural_basis_max_items": 4,
                "structural_basis_max_words_each": 70,
                "attention_regions_max_items": 3,
                "attention_region_max_words_each": 95,
                "attention_regions_empty_when_no_specific_issue": True,
                "system_disposition": disposition,
                "required_attention_coordinate_labels": attention_labels,
                "allowed_evidence_ids": allowed_ids,
            },
            "evidence_contract": facts,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", value))


def _clean_text(value: Any, *, max_words: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ScientificInterpretationError("The interpretation model returned an empty explanation field")
    if _word_count(text) > max_words:
        raise ScientificInterpretationError("the interpretation model's explanation exceeded the configured length")
    if re.search(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text):
        raise ScientificInterpretationError("the interpretation model's explanation exposed an internal identifier")
    if re.search(r"\bnon[- ]standard\s+V(?:-series|\s+series)", text, re.IGNORECASE):
        raise ScientificInterpretationError("The interpretation model incorrectly characterized V-series coordinates")
    return text


def _validate_grounded_text(text: str, facts: Mapping[str, Any]) -> str:
    if re.search(r"(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])", text):
        raise ScientificInterpretationError("The interpretation model placed evidence IDs inside narrative text")

    evidence = _as_list(facts.get("evidence"))
    evidence_codes = {str(_as_dict(item).get("code") or "") for item in evidence}
    lowered = text.lower()
    unsupported_patterns: list[tuple[bool, str, str]] = [
        (
            "alignment_insertions_present" not in evidence_codes,
            r"\b(?:insertion|inserted|alignment[- ]extension)\b",
            "The interpretation model claimed an insertion without insertion evidence",
        ),
        (
            "terminal_cca_observed" not in evidence_codes,
            r"\b(?:cca(?:\s+tail|\s+terminus)?|discriminator(?:\s+base)?)\b",
            "The interpretation model interpreted the terminus without CCA evidence",
        ),
        (
            True,
            r"\b(?:class\s+(?:i|ii|1|2)\s+tRNAs?|tRNA\s+class\s+(?:i|ii|1|2))\b",
            "The interpretation model introduced an unsupported tRNA class",
        ),
        (
            True,
            r"\b(?:strong|weak|high|low)[- ]confidence\b|\bstrong model support\b",
            "The interpretation model added an unsupported confidence adjective",
        ),
        (
            True,
            r"\bas expected\b|\bcharacteristic of\b",
            "The interpretation model added an unsupported biological generalization",
        ),
    ]
    for applies, pattern, message in unsupported_patterns:
        if applies and re.search(pattern, lowered, re.IGNORECASE):
            raise ScientificInterpretationError(message)

    trnascan_facts = [
        _as_dict(item)
        for item in evidence
        if str(_as_dict(item).get("code") or "") == "trnascan_annotation"
    ]
    reported_isotypes = {
        str(_as_dict(_as_dict(item).get("data")).get("isotype") or "").strip().lower()
        for item in trnascan_facts
    }
    if "ser" in reported_isotypes and re.search(r"\b(?:sec|selenocysteine)\b", lowered):
        raise ScientificInterpretationError("The interpretation model changed the reported Ser isotype to Sec")
    return text


def _validated_evidence_ids(value: Any, allowed: set[str]) -> list[str]:
    ids = [str(item or "").strip() for item in _as_list(value)]
    if not ids or any(item not in allowed for item in ids):
        raise ScientificInterpretationError("the interpretation model's explanation cited unsupported evidence")
    return list(dict.fromkeys(ids))


def _validate_coordinate_labels(value: Any, allowed: set[str]) -> list[str]:
    labels = [str(item or "").strip() for item in _as_list(value)]
    if any(not item or item not in allowed for item in labels):
        raise ScientificInterpretationError("the interpretation model's explanation named an unsupported coordinate")
    return list(dict.fromkeys(labels))


def _validate_coordinate_claims(text: str, allowed: set[str]) -> str:
    """Reject explicit coordinate claims that are absent from the candidate."""

    claimed: set[str] = set()
    for match in _EXPLICIT_COORDINATE_RE.finditer(text):
        claimed.update(item for item in match.groups() if item)
    claimed.update(_DISTINCTIVE_COORDINATE_RE.findall(text))
    unsupported = sorted(label for label in claimed if label not in allowed)
    if unsupported:
        raise ScientificInterpretationError(
            "the interpretation model's explanation named unsupported coordinates in prose: "
            + ", ".join(unsupported)
        )
    return text


def _validate_interpretation(payload: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    allowed_ids = {
        str(item.get("id") or "")
        for item in _as_list(facts.get("evidence"))
        if _as_dict(item)
    }
    candidate_labels = {
        str(item.get("label") or "")
        for item in _as_list(_as_dict(facts.get("candidate")).get("coordinates"))
        if _as_dict(item)
    }
    evidence_labels = {
        str(label or "")
        for item in _as_list(facts.get("evidence"))
        for label in _as_list(_as_dict(_as_dict(item).get("data")).get("missing_labels"))
        if str(label or "")
    }
    allowed_labels = candidate_labels | evidence_labels
    assessment = _as_dict(payload.get("sequence_assessment"))
    assessment_text = _validate_grounded_text(
        _clean_text(assessment.get("text"), max_words=90), facts
    )
    output = {
        "sequence_assessment": {
            "text": _validate_coordinate_claims(assessment_text, allowed_labels),
            "evidence_ids": _validated_evidence_ids(assessment.get("evidence_ids"), allowed_ids),
        },
        "structural_basis": [],
        "attention_regions": [],
    }
    basis = _as_list(payload.get("structural_basis"))
    if not basis or len(basis) > 4:
        raise ScientificInterpretationError("The interpretation model must return one to four structural-basis statements")
    for item in basis:
        record = _as_dict(item)
        basis_text = _validate_grounded_text(
            _clean_text(record.get("text"), max_words=70), facts
        )
        output["structural_basis"].append(
            {
                "text": _validate_coordinate_claims(basis_text, allowed_labels),
                "evidence_ids": _validated_evidence_ids(record.get("evidence_ids"), allowed_ids),
            }
        )
    attention = _as_list(payload.get("attention_regions"))
    if len(attention) > 3:
        raise ScientificInterpretationError("The interpretation model returned too many attention regions")
    disposition = str(_as_dict(facts.get("system_disposition")).get("code") or "")
    required_attention_labels = {
        str(item or "") for item in _as_list(facts.get("attention_coordinate_labels")) if str(item or "")
    }
    if disposition in {"review_required", "non_canonical_structure"} and not attention:
        raise ScientificInterpretationError(
            "The interpretation model must identify at least one concrete attention region for a review result"
        )
    returned_attention_labels: set[str] = set()
    for item in attention:
        record = _as_dict(item)
        region = _clean_text(record.get("region"), max_words=8)
        title = _clean_text(record.get("title") or region, max_words=12)
        attention_text = _validate_grounded_text(
            _clean_text(record.get("text"), max_words=95), facts
        )
        coordinate_labels = _validate_coordinate_labels(
            record.get("coordinate_labels"), allowed_labels
        )
        if disposition in {"review_required", "non_canonical_structure"} and not coordinate_labels:
            raise ScientificInterpretationError(
                "The interpretation model must name concrete candidate coordinates for a review result"
            )
        returned_attention_labels.update(coordinate_labels)
        output["attention_regions"].append(
            {
                "region": region,
                "title": title,
                "text": _validate_coordinate_claims(attention_text, allowed_labels),
                "coordinate_labels": coordinate_labels,
                "evidence_ids": _validated_evidence_ids(record.get("evidence_ids"), allowed_ids),
            }
        )
    if required_attention_labels and not returned_attention_labels.intersection(required_attention_labels):
        raise ScientificInterpretationError(
            "The interpretation model did not address the rule-identified candidate coordinates"
        )
    return output


def _env_flag(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _citation_limit() -> int:
    try:
        value = int(str(os.getenv("LITERATURE_MAX_CITATIONS") or "").strip())
    except ValueError:
        return _DEFAULT_CITATION_LIMIT
    return max(0, min(value, 20))


def _literature_evidence(
    evidence: list[dict[str, Any]],
    *,
    agent: Mapping[str, Any] | None = None,
    scientific_provenance: Mapping[str, Any] | None = None,
    record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn recorded observations into the literature questions worth asking.

    Only review signals select a region.  A run that simply occupies coordinates
    66-76 says nothing that needs looking up; a run that flags the acceptor end
    does, and that is where a reader wants the CCA literature.
    """
    facts = [_as_dict(item) for item in evidence]
    codes = {str(item.get("code") or "") for item in facts}
    region_names: list[str] = []
    for item in facts:
        code = str(item.get("code") or "")
        data = _as_dict(item.get("data"))
        if code == "recorded_review_trigger" and data.get("region"):
            region_names.append(str(data["region"]))
        elif code == "variable_bridge_requires_review":
            region_names.append("Variable loop")
        elif code == "alignment_insertions_present":
            for label in _as_list(data.get("labels")):
                try:
                    region_names.append(number_to_region(str(label)))
                except ValueError:
                    continue

    scientific = _as_dict(scientific_provenance)
    agent_data = _as_dict(agent)
    record_data = _as_dict(record)
    # A reviewer page also knows where this run disagrees with the current gold
    # standard.  That answer-aware hint is used only to choose which literature to
    # show; it never enters the interpretation model's input.
    region_names.extend(str(item) for item in _as_list(record_data.get("diff_regions")) if str(item or "").strip())
    trnascan = _as_dict(scientific.get("trnascan"))
    specialized_route = str(_as_dict(scientific.get("specialized_route")).get("route") or "")
    route = str(agent_data.get("route") or "")
    domain_hints = " ".join(
        str(part).lower()
        for part in (
            trnascan.get("domain_hint"),
            trnascan.get("mitochondrial_subtype"),
            record_data.get("domain_hint"),
            record_data.get("cohort_source"),
            record_data.get("input_type"),
            specialized_route,
            route,
        )
        if part
    )

    query: dict[str, Any] = {
        "conflict_regions": literature_topics.keys_for_display_regions(region_names),
        "domain": domain_hints,
        "trna_class": str(record_data.get("trna_class") or specialized_route or "").lower(),
        "intron_status": "present" if "intron" in domain_hints else "",
        "terminal_cca": "absent" if "terminal_cca_observed" not in codes else "present",
        "sequence_origin": str(record_data.get("sequence_origin") or "").lower(),
        "note": str(record_data.get("note") or ""),
    }
    # A curation record also knows how the reference itself ends.  When the stored
    # numbering stops before 74, the CCA question is already live for that case.
    reference_category = str(record_data.get("terminal_reference_category") or "").strip().lower()
    if reference_category and reference_category != "complete_cca":
        query["terminal_cca"] = "missing_cca"

    posterior = None
    for item in facts:
        if str(item.get("code") or "") != "cm_average_posterior":
            continue
        try:
            posterior = float(_as_dict(item.get("data")).get("average_posterior"))
        except (TypeError, ValueError):
            posterior = None
        break
    # In the 2530-case batch the covariance-model mean posterior spans 0.888 to 1.000
    # with a median of 0.998.  A sampled 120 cases below 0.95 averaged 88.3%
    # per-residue accuracy against 98.4% above it, so that band is where an unusual
    # alignment is worth checking against the alignment-quality literature.
    if posterior is not None and posterior < _POSTERIOR_SIMILARITY_BAND:
        query["template_similarity"] = "medium"
    return query


def _corpus_stamp() -> str:
    """Which index build the references came from; empty when the corpus is absent."""
    try:
        from backend.domain.literature import repository as literature_repository

        return literature_repository.corpus_stamp()
    except Exception:
        return ""


def _collect_citations(query: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Look up references for one completed run; never fail the interpretation."""
    limit = _citation_limit()
    if limit <= 0 or not _env_flag("LITERATURE_ENABLED"):
        return [], ""
    try:
        from backend.domain.literature import search as literature_search

        return (
            literature_search.citations_for_evidence(
                query,
                total_limit=limit,
                include_public=_env_flag("LITERATURE_PUBLIC_SEARCH"),
            ),
            _corpus_stamp(),
        )
    except Exception:  # a missing reference list must never lose an explanation
        LOGGER.exception("literature lookup failed")
        return [], ""


def _citation_lookup(
    facts: Mapping[str, Any],
    agent: Mapping[str, Any] | None,
    scientific_provenance: Mapping[str, Any] | None,
    record: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], str]:
    query = _literature_evidence(
        list(_as_dict(facts).get("evidence") or []),
        agent=agent,
        scientific_provenance=scientific_provenance,
        record=record,
    )
    return _collect_citations(query)


def generate_scientific_interpretation(
    *,
    sequence: str,
    labels: Iterable[Any],
    bases: Iterable[Any],
    structure: Mapping[str, Any] | None = None,
    agent: Mapping[str, Any] | None = None,
    scientific_provenance: Mapping[str, Any] | None = None,
    warnings: Iterable[Any] = (),
    record: Mapping[str, Any] | None = None,
    language: str = "en",
    status_callback: Any = None,
) -> dict[str, Any]:
    """Generate or retrieve one interpretation for the whole sequence."""

    language = normalize_interpretation_language(language)

    facts = build_interpretation_facts(
        sequence=sequence,
        labels=labels,
        bases=bases,
        structure=structure,
        agent=agent,
        scientific_provenance=scientific_provenance,
        warnings=warnings,
        record=record,
    )
    cache_key = _json_hash({"facts": facts["cache_key"], "language": language})
    cached = _cache_get(cache_key)
    if cached:
        cached["cached"] = True
        # The narrative is reused as stored; only the reference list is re-checked,
        # and only when the literature index itself has been rebuilt.
        stamp = _corpus_stamp()
        if "citations" not in cached or (stamp and str(cached.get("citations_corpus") or "") != stamp):
            citations, fresh_stamp = _citation_lookup(
                facts, agent, scientific_provenance, record
            )
            cached["citations"] = citations
            cached["citations_corpus"] = fresh_stamp
            _cache_put(cache_key, cached)
        return cached

    # mimo-v2.5-pro 的思考 token 与正文 token 共用 max_completion_tokens：
    # 上限过小时思考会把额度吃光，正文为空或半截（finish_reason=length），
    # 表现为 "LLM response was not a JSON object" / "Unterminated string"。
    # 因此给足额度（思考实测可达 ~2000），并把单次尝试的墙钟预算放宽。
    runtime_values = {
        "LLM_PROVIDER": "mimo",
        "LLM_USAGE_CASE_ID": str(_as_dict(record).get("id") or _as_dict(record).get("seqname") or "scientific_interpretation"),
        "LLM_HARD_BUDGET": 180,
    }
    if callable(status_callback):
        runtime_values["_status_callback"] = status_callback
    client = StructuredJsonLLMClient()
    messages = _messages(facts, language)
    completion = None
    interpretation = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            completion = client.complete(
                messages=messages,
                runtime_values_override=runtime_values,
                source="scientific_interpretation",
                max_tokens=_INTERPRETATION_MAX_TOKENS,
            )
            interpretation = _validate_interpretation(completion.payload, facts)
            break
        except (JsonCompletionError, ScientificInterpretationError) as exc:
            last_error = exc
            if attempt >= 2:
                break
            correction_messages = list(messages)
            if completion is not None:
                correction_messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(completion.payload, ensure_ascii=False),
                    }
                )
            correction_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Revise the previous JSON because it failed output validation: "
                        + str(exc)
                        + ". Preserve supported observations, remove the unsupported claim, and return a shorter complete JSON object."
                    ),
                }
            )
            messages = correction_messages
    if completion is None or interpretation is None:
        raise ScientificInterpretationError(str(last_error or "the interpretation model did not return an explanation")) from last_error

    citations, stamp = _citation_lookup(facts, agent, scientific_provenance, record)
    response = {
        "schema_version": SCHEMA_VERSION,
        "cached": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system_disposition": dict(facts["system_disposition"]),
        "interpretation": interpretation,
        "evidence": list(facts["evidence"]),
        "citations": citations,
        "citations_corpus": stamp,
        "provenance": {
            "provider": completion.provider,
            "model": completion.model,
            "candidate_hash": facts["candidate_hash"],
            "evidence_version": SCHEMA_VERSION,
            "language": language,
        },
    }
    _cache_put(cache_key, response)
    return response


__all__ = [
    "SCHEMA_VERSION",
    "ScientificInterpretationError",
    "build_interpretation_facts",
    "generate_scientific_interpretation",
    "normalize_interpretation_language",
]
