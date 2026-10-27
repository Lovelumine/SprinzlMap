from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.agent.contracts import AlignmentRequest, CandidateAction, RootSnapshot
from backend.agent.execution import BranchExecutor, RootSnapshotBuilder
from backend.domain.common.trnascan_runtime import resolve_trnascan_search_mode


def _cm_parent(group="bact"):
    return RootSnapshot(
        template_name="",
        csv_content="1\nA\nA\n",
        numbering_row=["1"],
        template_row=["A"],
        sequence_row=["A"],
        raw_meta={
            "numbering_backend": "cmalign",
            "numbering_model": {"group": group},
            "cm_portfolio": {
                "selected_group": group,
                "selection_policy": "calibrated_domain_selector",
                "calibration": {
                    "status": "calibrated",
                    "disposition": "accepted",
                    "predicted_group": group,
                    "confidence": 0.83,
                },
            },
        },
    )


def _run_tool(monkeypatch, request, parent):
    builder = RootSnapshotBuilder()
    captured = []

    def build(downstream_request):
        captured.append(downstream_request)
        return RootSnapshot(template_name="reference", csv_content="1\nA\nA\n")

    monkeypatch.setattr(builder, "build", build)
    result = BranchExecutor(root_builder=builder).execute(
        request, parent, CandidateAction(action_type="align_legacy_reference"), "reference-1"
    )
    assert len(captured) == 1
    return captured[0], result.raw_meta["legacy_reference_tool"]["domain_routing"]


@pytest.mark.parametrize("input_hint", ["unknown", "", " UNKNOWN "])
@pytest.mark.parametrize(
    "group,domain,mode", [("bact", "bacteria", "B"), ("arch", "archaea", "A"), ("euk", "eukaryota", "E")]
)
def test_reference_tool_uses_accepted_cm_domain(monkeypatch, input_hint, group, domain, mode):
    request = AlignmentRequest(target_seq="A", domain_hint=input_hint)
    parent = _cm_parent(group)
    original_meta = deepcopy(parent.raw_meta)

    downstream, routing = _run_tool(monkeypatch, request, parent)

    assert downstream.domain_hint == domain
    assert resolve_trnascan_search_mode(downstream.domain_hint).args == (f"-{mode}",)
    assert routing == {
        "requested_domain_hint": input_hint,
        "effective_domain_hint": domain,
        "source": "cm_calibrated_domain_selector",
        "selected_group": group,
        "confidence": 0.83,
    }
    assert request.domain_hint == input_hint
    assert parent.raw_meta == original_meta


@pytest.mark.parametrize(
    "domain", ["bacteria", "archaea", "eukaryota", "mitochondrial", "chloroplast", "cyanelle", "virus", "all"]
)
def test_explicit_source_is_preserved(monkeypatch, domain):
    request = AlignmentRequest(target_seq="A", domain_hint=domain, mitochondrial_subtype="mammal")

    downstream, routing = _run_tool(monkeypatch, request, _cm_parent())

    assert downstream.domain_hint == domain
    assert downstream.mitochondrial_subtype == "mammal"
    assert routing["source"] == "request"


@pytest.mark.parametrize(
    "defect",
    ["missing", "uncalibrated", "abstained", "raw_score", "group_conflict", "model_conflict", "all", "not_cm"],
)
def test_untrusted_cm_evidence_keeps_source_unknown(monkeypatch, defect):
    parent = _cm_parent("all" if defect == "all" else "bact")
    portfolio = parent.raw_meta["cm_portfolio"]
    if defect == "missing":
        del parent.raw_meta["cm_portfolio"]
    elif defect == "uncalibrated":
        portfolio["calibration"]["status"] = "unavailable"
    elif defect == "abstained":
        portfolio["calibration"]["disposition"] = "abstained"
    elif defect == "raw_score":
        portfolio["selection_policy"] = "uncalibrated_domain_selector_unavailable"
    elif defect == "group_conflict":
        portfolio["calibration"]["predicted_group"] = "arch"
    elif defect == "model_conflict":
        parent.raw_meta["numbering_model"]["group"] = "arch"
    elif defect == "not_cm":
        parent.raw_meta["numbering_backend"] = "template_regional_dp"

    downstream, routing = _run_tool(
        monkeypatch, AlignmentRequest(target_seq="A", domain_hint="unknown"), parent
    )

    assert downstream.domain_hint == "unknown"
    assert routing["source"] == "unknown"


@pytest.mark.parametrize(
    "action_type,audit_key,reconciler",
    [
        ("repair_acceptor_tail_with_structure", "acceptor_tail_reconciliation", "reconcile_acceptor_tail"),
        ("reconcile_variable_boundary_with_structure", "variable_boundary_reconciliation", "reconcile_variable_boundary"),
    ],
)
@pytest.mark.parametrize(
    "hint,disposition,expected",
    [("unknown", "accepted", "bacteria"), ("unknown", "abstained", "unknown"), ("mitochondrial", "accepted", "mitochondrial")],
)
def test_structural_reconciliation_uses_the_same_domain_evidence(
    monkeypatch, action_type, audit_key, reconciler, hint, disposition, expected
):
    parent = _cm_parent()
    parent.raw_meta["cm_portfolio"]["calibration"]["disposition"] = disposition
    original_meta = deepcopy(parent.raw_meta)
    request = AlignmentRequest(target_seq="A", domain_hint=hint, mitochondrial_subtype="mammal")
    calls = []

    def scan(sequence, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            struct_file_sequence=sequence,
            secondary_structure=".",
            type="Ala",
            anticodon="UGC",
            search_mode=resolve_trnascan_search_mode(kwargs["domain_hint"]).flag,
            domain_hint=kwargs["domain_hint"],
        )

    monkeypatch.setattr("backend.agent.execution.branch_executor.run_trnascan_directly", scan)
    monkeypatch.setattr(
        f"backend.agent.execution.branch_executor.{reconciler}",
        lambda **kwargs: {"resolved": False, "reason": "insufficient_structure_evidence"},
    )
    result = BranchExecutor().execute(
        request,
        parent,
        CandidateAction(
            action_type=action_type,
            allowed_regions=["Variable loop"] if reconciler == "reconcile_variable_boundary" else ["Aminoacyl arm 5' end", "Aminoacyl arm 3' end"],
            success_condition="validated_structure_projection",
            rollback_condition="retain_parent_on_failure",
        ),
        "structure-1",
    )

    assert len(calls) == 1
    assert calls[0]["domain_hint"] == expected
    assert calls[0]["mitochondrial_subtype"] == "mammal"
    audit = result.raw_meta[audit_key]
    assert audit["domain_routing"]["effective_domain_hint"] == expected
    assert audit["tool_observation"]["domain_hint"] == expected
    assert request.domain_hint == hint
    assert parent.raw_meta == original_meta
