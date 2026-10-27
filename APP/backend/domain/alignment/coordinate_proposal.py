"""Ask the model for a numbering row, then let the Harness decide about it.

Every other Agent action hands the row to a deterministic tool: cmalign, a
template alignment, a DP over one region.  The model only chooses among them,
so telling it the Sprinzl rules cannot move a coordinate.  This module closes
that gap in the one place it can be closed safely.  The model writes labels,
and everything after that is the same check every other candidate passes.

Three limits keep it honest:

* the row must cover every input residue, so a proposal cannot shorten it;
* only residues in the regions the Agent was told to repair may change;
* the returned row is not trusted -- ``hard_guards`` and the online ranker see
  it exactly as they see a tool candidate, and it is discarded unless it scores
  better.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from backend.domain.alignment.number_to_region import number_to_region
from backend.domain.alignment.sprinzl_coordinates import (
    INSERTION_LABEL_RE,
    SPRINZL_PARENT_INDEX,
)
from backend.domain.alignment.sprinzl_numbering_manual import manual_text
from backend.infra.llm.json_client import (
    JsonCompletionError,
    JsonTruncatedError,
    StructuredJsonLLMClient,
)
from backend.infra.llm.provider_config import MAX_COMPLETION_TOKENS
from backend.domain.alignment.runtime_helpers import runtime_int

GAP = "-"

# A window is a handful of residues, but the reference rules ride along in the
# same request and the gateway reasons before it answers.
DEFAULT_PROPOSAL_MAX_TOKENS = 6144

# A label is legal only if it is a canonical position or an insertion under a
# canonical parent.  Anything else would be rejected by the guards anyway;
# catching it here buys the model one correction instead of one lost step.
_CANONICAL_LABELS = frozenset(SPRINZL_PARENT_INDEX)


@dataclass(slots=True)
class ProposalResult:
    """What the model answered and whether the Harness can use it."""

    status: str
    labels: List[str] = field(default_factory=list)
    changed: List[Dict[str, str]] = field(default_factory=list)
    error: str = ""
    attempts: int = 0
    notes: List[str] = field(default_factory=list)

    def as_meta(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "accepted_candidate": bool(self.labels),
            "changed_positions": list(self.changed),
            "changed_count": len(self.changed),
            "attempts": self.attempts,
            "error": self.error,
            "notes": list(self.notes),
        }


def is_legal_label(label: str) -> bool:
    text = str(label or "").strip()
    if not text or text == GAP:
        return True
    if text in _CANONICAL_LABELS:
        return True
    match = INSERTION_LABEL_RE.match(text)
    return bool(match) and match.group(1) in _CANONICAL_LABELS


def region_for_label(label: str) -> str:
    try:
        return number_to_region(str(label or "").strip())
    except ValueError:
        return ""


def residue_table(
    sequence_row: Sequence[str],
    numbering_row: Sequence[str],
    *,
    hidden_windows: Sequence[Mapping[str, int]] = (),
) -> Dict[str, str]:
    """Two aligned strings the model can read column by column.

    A list of objects would triple the tokens for the same information, and the
    column index is what has to line up with the answer.
    """
    hidden: set[int] = set()
    for window in list(hidden_windows or []):
        try:
            start = max(1, int(window["from"]))
            end = min(len(numbering_row), int(window["to"]))
        except (KeyError, TypeError, ValueError):
            continue
        hidden.update(range(start - 1, end))
    current_labels = [
        "<EDIT>" if index in hidden else (str(label or "").strip() or GAP)
        for index, label in enumerate(numbering_row)
    ]
    return {
        "positions": " ".join(str(i + 1) for i in range(len(sequence_row))),
        "bases": " ".join(str(b or "").upper().replace("T", "U") for b in sequence_row),
        "current_labels": " ".join(current_labels),
        "hidden_label_positions": " ".join(str(index + 1) for index in sorted(hidden)),
    }


def edit_windows(
    numbering_row: Sequence[str],
    allowed_regions: Sequence[str],
    *,
    pad: int = 1,
) -> List[Dict[str, int]]:
    """Contiguous residue ranges the proposal is allowed to rewrite.

    The window is padded by one residue on each side so a boundary can actually
    move: the disputed residue is usually the one the current row assigned to the
    wrong end of a region.
    """
    allowed = {str(region) for region in allowed_regions if str(region)}
    flagged = [
        index
        for index, label in enumerate(numbering_row)
        if region_for_label(label) in allowed and region_for_label(label)
    ]
    if not flagged:
        return []
    last = len(numbering_row) - 1
    spans: List[List[int]] = []
    for index in flagged:
        low, high = max(0, index - pad), min(last, index + pad)
        if spans and low <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], high)
        else:
            spans.append([low, high])
    return [{"from": low + 1, "to": high + 1} for low, high in spans]


def _messages(
    *,
    sequence_row: Sequence[str],
    numbering_row: Sequence[str],
    allowed_regions: Sequence[str],
    structure_pairs: Sequence[Sequence[Any]],
    validation: Mapping[str, Any],
    reference_notes: Sequence[str],
    prior_violations: Sequence[str],
) -> List[Dict[str, str]]:
    system = (
        "You are the coordinate specialist inside a scientific tRNA alignment "
        "Harness. Renumber the edit windows of the input sequence using the "
        "reference rules below. Residues outside those windows keep the labels "
        "they already have, so propose labels only for the window ranges. You "
        "may not change the bases, the row length, or the rules. Return one JSON "
        "object only.\n\n" + manual_text()
    )
    windows = edit_windows(numbering_row, allowed_regions)
    user_payload = {
        "input_sequence": "".join(str(b or "").upper().replace("T", "U") for b in sequence_row),
        # The model must infer a replacement from sequence and structure.  The
        # old labels are useful outside the edit window, but showing them inside
        # the window made the proposal action copy its parent row verbatim.
        "residues": residue_table(
            sequence_row, numbering_row, hidden_windows=windows
        ),
        "allowed_regions": list(allowed_regions),
        # The model rewrites these ranges and nothing else.  Labels outside them
        # are copied from the parent, so it cannot break a region nobody asked
        # it to touch and cannot change the length of the row.
        "edit_windows": windows,
        # Residue indices, not coordinates: the pairing comes from the fold of
        # the input, and the labels it would be described with are exactly what
        # the model is being asked to change.
        "structure_pairs": _pair_list(structure_pairs),
        "current_candidate_validation": dict(validation or {}),
        "reference_notes": list(reference_notes or []),
        "checks_your_last_row_failed": list(prior_violations or []),
        "response_schema": {
            "edits": (
                "array of {from, to, labels}; one entry per edit window, from and "
                "to are 1-based residue positions inclusive, and labels is an "
                "array of exactly (to - from + 1) Sprinzl labels such as 35 or "
                "53i1, or '-' when a residue carries no coordinate"
            ),
            "reason_code": "short machine-readable code",
        },
    }
    if prior_violations:
        user_payload["instruction"] = (
            "Your previous row failed the checks listed above, and the "
            "ACCEPTANCE paragraph says what each code means. Fix those checks "
            "and leave every other residue unchanged."
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True, sort_keys=True)},
    ]


def _pair_list(structure_pairs: Sequence[Any], *, limit: int = 90) -> List[List[int]]:
    """Normalise ``id_pairs`` entries to ``[left_index, right_index]``."""
    pairs: List[List[int]] = []
    for item in list(structure_pairs or [])[: limit * 2]:
        if isinstance(item, Mapping):
            left = item.get("left_index")
            right = item.get("right_index")
        else:
            try:
                left, right = item[0], item[1]
            except (IndexError, TypeError):
                continue
        try:
            pairs.append([int(left), int(right)])
        except (TypeError, ValueError):
            continue
        if len(pairs) >= limit:
            break
    return pairs


def _normalize_labels(raw: Any, *, expected: int) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("labels must be an array")
    labels = [str(item or "").strip() or GAP for item in raw]
    if len(labels) != expected:
        raise ValueError(f"labels has {len(labels)} entries, expected {expected}")
    illegal = [label for label in labels if not is_legal_label(label)]
    if illegal:
        raise ValueError(
            "illegal Sprinzl labels: " + ",".join(sorted(set(illegal))[:8])
        )
    return labels


def _apply_edits(
    payload: Mapping[str, Any],
    *,
    numbering_row: Sequence[str],
    windows: Sequence[Mapping[str, int]],
) -> List[str]:
    """Splice the model's window labels onto a copy of the parent row.

    Everything outside a window is copied, which is what makes an out-of-scope
    edit impossible rather than merely punishable.
    """
    row = [str(label or "").strip() or GAP for label in numbering_row]
    if "labels" in payload:
        # Tolerate a model that answers with the whole row anyway; the scope
        # check below still has to agree with it.
        return _normalize_labels(payload.get("labels"), expected=len(row))
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty array of window rewrites")
    allowed = {(int(w["from"]), int(w["to"])) for w in windows}
    answered: set[tuple[int, int]] = set()
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise ValueError("each edit must be an object")
        try:
            start, end = int(edit.get("from")), int(edit.get("to"))
        except (TypeError, ValueError):
            raise ValueError("each edit needs 1-based integer from and to")
        if (start, end) not in allowed:
            inside = [
                (low, high)
                for low, high in allowed
                if low <= start and end <= high
            ]
            if not inside:
                raise ValueError(
                    "edit "
                    f"{start}-{end} is not one of the windows "
                    + ", ".join(f"{low}-{high}" for low, high in sorted(allowed))
                )
        labels = _normalize_labels(edit.get("labels"), expected=end - start + 1)
        if start < 1 or end > len(row) or start > end:
            raise ValueError(f"edit {start}-{end} falls outside the sequence")
        row[start - 1 : end] = labels
        answered.add((start, end))
    missing = sorted(allowed - answered)
    if missing:
        raise ValueError(
            "no labels returned for window(s) "
            + ", ".join(f"{low}-{high}" for low, high in missing)
        )
    return row


def _scope_report(
    labels: Sequence[str],
    numbering_row: Sequence[str],
    *,
    allowed_regions: Sequence[str],
) -> Dict[str, Any]:
    """Which residues the proposal moved, and which regions that touches."""
    allowed = {str(region) for region in allowed_regions if str(region)}
    changed: List[Dict[str, str]] = []
    outside: set[str] = set()
    for index, (new, old) in enumerate(zip(labels, numbering_row)):
        old_label = str(old or "").strip() or GAP
        if new == old_label:
            continue
        changed.append(
            {"position": str(index + 1), "from": old_label, "to": new}
        )
        # Both ends of an edit have to sit in a flagged region.  Checking only
        # the new label would let a residue be pulled out of a region the Agent
        # never asked to repair.
        for label in (new, old_label):
            region = region_for_label(label)
            if region and allowed and region not in allowed:
                outside.add(region)
    return {
        "changed": changed,
        "outside_allowed_regions": sorted(outside),
        "touched_regions": sorted(
            {
                region
                for entry in changed
                for region in (
                    region_for_label(entry["to"]),
                    region_for_label(entry["from"]),
                )
                if region
            }
        ),
    }


def propose_row(
    *,
    client: StructuredJsonLLMClient | None = None,
    runtime_values_override: Mapping[str, Any] | None = None,
    sequence_row: Sequence[str],
    numbering_row: Sequence[str],
    allowed_regions: Sequence[str],
    structure_pairs: Sequence[Sequence[Any]] = (),
    validation: Mapping[str, Any] | None = None,
    reference_notes: Sequence[str] = (),
    prior_violations: Sequence[str] = (),
    max_tokens: int | None = None,
    attempts: int = 2,
    usage_collector: List[Dict[str, Any]] | None = None,
) -> ProposalResult:
    """One bounded request for a corrected row.  Returns no labels on any doubt."""
    if not sequence_row or not numbering_row:
        return ProposalResult(status="no_parent_row")
    if not allowed_regions:
        return ProposalResult(status="no_repair_regions")

    windows = edit_windows(numbering_row, allowed_regions)
    if not windows:
        return ProposalResult(status="no_edit_window")

    llm = client or StructuredJsonLLMClient()
    messages = _messages(
        sequence_row=sequence_row,
        numbering_row=numbering_row,
        allowed_regions=allowed_regions,
        structure_pairs=structure_pairs,
        validation=validation or {},
        reference_notes=reference_notes,
        prior_violations=prior_violations,
    )
    values = {
        **dict(runtime_values_override or {}),
        "LLM_USAGE_CASE_ID": str(
            (runtime_values_override or {}).get("LLM_USAGE_CASE_ID") or "coordinate_proposal"
        ),
    }
    # The answer is a handful of labels, but the gateway reasons before it
    # writes, and the reference rules sit in the same request.  The budget is
    # stated once here so a run can record which one it used.
    budget = runtime_int(
        "AGENT_PROPOSAL_MAX_TOKENS",
        DEFAULT_PROPOSAL_MAX_TOKENS,
        128,
        MAX_COMPLETION_TOKENS,
        runtime_values=values,
    )
    max_tokens = int(max_tokens) if max_tokens else budget
    last_error = ""
    budget = int(max_tokens)
    for attempt in range(1, max(1, attempts) + 1):
        try:
            completion = llm.complete(
                messages=messages,
                runtime_values_override=values,
                source="agent_coordinate_proposal",
                max_tokens=budget,
            )
        except JsonTruncatedError as exc:
            # The gateway spent the budget on reasoning and never wrote the row.
            # That is a budget problem, so the retry asks for more room rather
            # than losing the attempt.
            if attempt >= attempts:
                return ProposalResult(
                    status="llm_truncated",
                    error=f"{type(exc).__name__}:{exc}",
                    attempts=attempt,
                )
            budget = min(MAX_COMPLETION_TOKENS, max(1024, budget * 3))
            # Nothing was written, so there is no payload to read.  Falling
            # through to the usage block here raised UnboundLocalError and
            # turned every truncated proposal into an action failure.
            continue
        except JsonCompletionError as exc:
            return ProposalResult(
                status="llm_error",
                error=f"{type(exc).__name__}:{exc}",
                attempts=attempt,
            )
        if usage_collector is not None:
            usage_collector.append(
                {
                    "source": "agent_coordinate_proposal",
                    "provider": completion.provider,
                    "model": completion.model,
                    "usage": dict(completion.usage or {}),
                    "elapsed_sec": completion.elapsed_sec,
                }
            )
        payload = dict(completion.payload or {})
        try:
            labels = _apply_edits(
                payload, numbering_row=numbering_row, windows=windows
            )
        except ValueError as exc:
            last_error = str(exc)
            if attempt >= attempts:
                break
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": _json_dumps(payload),
                },
                {
                    "role": "user",
                    "content": (
                        f"Rejected: {last_error}. Return one JSON object with an "
                        "'edits' array holding every window: "
                        + json.dumps(windows)
                    ),
                },
            ]
            continue

        scope = _scope_report(labels, numbering_row, allowed_regions=allowed_regions)
        if scope["outside_allowed_regions"]:
            last_error = (
                "proposal edited regions outside the allowed set: "
                + ",".join(scope["outside_allowed_regions"])
            )
            if attempt >= attempts:
                break
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": _json_dumps(payload),
                },
                {
                    "role": "user",
                    "content": (
                        f"Rejected: {last_error}. Only these regions may change: "
                        + ", ".join(allowed_regions)
                        + ". Return the full labels array again."
                    ),
                },
            ]
            continue
        if not scope["changed"]:
            return ProposalResult(
                status="no_change", attempts=attempt, error="proposal identical to parent"
            )
        return ProposalResult(
            status="proposed",
            labels=labels,
            changed=scope["changed"],
            attempts=attempt,
            notes=[
            f"changed_regions={','.join(sorted(allowed_regions))}",
            f"edit_windows={json.dumps(windows)}",
        ],
        )

    return ProposalResult(status="rejected", attempts=attempts, error=last_error)


def _json_dumps(payload: Mapping[str, Any]) -> str:

    return json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)[:4000]


__all__ = [
    "GAP",
    "ProposalResult",
    "edit_windows",
    "is_legal_label",
    "propose_row",
    "region_for_label",
    "residue_table",
]
