from __future__ import annotations

from typing import Any, Dict


def normalize_sequence(raw: str) -> str:
    return "".join(str(raw or "").upper().split()).replace("T", "U")


def assess_input_evidence(
    *,
    target_seq: str,
    input_type: str = "unknown",
    domain_hint: str = "unknown",
    cca_status: str = "unknown",
    intron_status: str = "unknown",
    trna_class: str = "unknown",
) -> Dict[str, Any]:
    """Separate direct sequence observations from biological claims."""

    raw_upper = str(target_seq or "").upper()
    sequence = normalize_sequence(target_seq)
    input_type_norm = str(input_type or "unknown").strip().lower() or "unknown"
    domain_norm = str(domain_hint or "unknown").strip().lower() or "unknown"
    cca_norm = str(cca_status or "unknown").strip().lower() or "unknown"
    intron_norm = str(intron_status or "unknown").strip().lower() or "unknown"
    class_norm = str(trna_class or "unknown").strip().lower() or "unknown"
    invalid_symbols = sorted(set(sequence) - set("ACGUN"))
    terminal_triplet = sequence[-3:] if len(sequence) >= 3 else sequence
    terminal_cca_observed = terminal_triplet == "CCA"

    claim_conflicts: list[str] = []
    if cca_norm == "input_encoded" and not terminal_cca_observed:
        claim_conflicts.append("cca_claim_input_encoded_but_terminal_cca_missing")
    if (
        cca_norm == "inferred_post_transcriptional"
        and input_type_norm == "mature"
        and not terminal_cca_observed
    ):
        claim_conflicts.append(
            "cca_claim_post_transcriptional_but_mature_terminal_cca_missing"
        )
    if (
        cca_norm == "absent"
        and input_type_norm == "mature"
        and terminal_cca_observed
    ):
        claim_conflicts.append("cca_claim_absent_but_mature_input_ends_cca")
    if intron_norm == "present" and input_type_norm == "mature":
        claim_conflicts.append("intron_claim_present_but_input_type_is_mature")

    cca_assignment_allowed = bool(
        terminal_cca_observed
        and (
            cca_norm == "input_encoded"
            or (
                input_type_norm == "mature"
                and cca_norm == "inferred_post_transcriptional"
            )
        )
    )
    if cca_norm == "input_encoded":
        cca_assignment_basis = "explicit_input_tail_claim"
    elif input_type_norm == "mature" and terminal_cca_observed:
        cca_assignment_basis = "terminal_motif_observed_but_provenance_unconfirmed"
    else:
        cca_assignment_basis = "not_established"

    intron_auto_detection_eligible = bool(
        intron_norm == "unknown"
        and input_type_norm in {"gene_body", "pre_trna"}
        and domain_norm == "archaea"
    )

    return {
        "sequence_length": len(sequence),
        "alphabet": (
            "mixed"
            if "T" in raw_upper and "U" in raw_upper
            else "DNA"
            if "T" in raw_upper
            else "RNA"
            if "U" in raw_upper
            else "unknown"
        ),
        "invalid_symbols": invalid_symbols,
        "sequence_observations": {
            "terminal_triplet": terminal_triplet,
            "terminal_cca_observed": terminal_cca_observed,
        },
        "biological_claims": {
            "input_type": input_type_norm,
            "domain_hint": domain_norm,
            "cca_status": cca_norm,
            "intron_status": intron_norm,
            "trna_class": class_norm,
        },
        "cca_evidence": {
            "terminal_cca_observed": terminal_cca_observed,
            "status_claim": cca_norm,
            "coordinate_assignment_allowed": cca_assignment_allowed,
            "coordinate_assignment_basis": cca_assignment_basis,
            "genomic_or_post_transcriptional_origin": (
                "post_transcriptional_claim"
                if cca_norm == "inferred_post_transcriptional"
                else "unknown"
            ),
        },
        "intron_evidence": {
            "status_claim": intron_norm,
            "automatic_detection_eligible": intron_auto_detection_eligible,
            "detection_scope": (
                "archaeal_pre_trna_or_gene_body"
                if intron_auto_detection_eligible
                else "not_scheduled"
            ),
        },
        "claim_conflicts": claim_conflicts,
    }


__all__ = ["assess_input_evidence", "normalize_sequence"]
