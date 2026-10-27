from __future__ import annotations

import re
from typing import Iterable


SPRINZL_MATCH_ORDER = (
    "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "10", "11", "12", "13", "14", "15", "16", "17", "17a", "18", "19",
    "20", "20a", "20b", "21", "22", "23", "24", "25", "26",
    "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38",
    "39", "40", "41", "42", "43", "44", "45",
    "V11", "V12", "V13", "V14", "V15", "V16", "V17",
    "V1", "V2", "V3", "V4", "V5",
    "V27", "V26", "V25", "V24", "V23", "V22", "V21",
    "46", "47", "48",
    "49", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60",
    "61", "62", "63", "64", "65",
    "66", "67", "68", "69", "70", "71", "72", "73", "74", "75", "76",
)
SPRINZL_PARENT_ORDER = ("-1", *SPRINZL_MATCH_ORDER)
SPRINZL_PARENT_INDEX = {label: index for index, label in enumerate(SPRINZL_PARENT_ORDER)}
INSERTION_LABEL_RE = re.compile(r"^(.+?)i(\d+)$")


def labels_from_consensus_structure(ss_cons: str) -> tuple[str, ...]:
    labels: list[str] = []
    match_index = 0
    insertion_index = 0
    leading_insertion_count = 0
    previous_match = ""

    for token in str(ss_cons or ""):
        if token == ".":
            if previous_match:
                insertion_index += 1
                labels.append(f"{previous_match}i{insertion_index}")
            else:
                leading_insertion_count += 1
                labels.append("-1" if leading_insertion_count == 1 else f"-1i{leading_insertion_count - 1}")
            continue
        if match_index >= len(SPRINZL_MATCH_ORDER):
            raise ValueError(
                "Consensus structure contains more match states than the Sprinzl model: "
                f"{match_index + 1}>{len(SPRINZL_MATCH_ORDER)}"
            )
        previous_match = SPRINZL_MATCH_ORDER[match_index]
        labels.append(previous_match)
        match_index += 1
        insertion_index = 0

    if match_index != len(SPRINZL_MATCH_ORDER):
        raise ValueError(
            "Consensus structure match-state count does not match the Sprinzl model: "
            f"{match_index}!={len(SPRINZL_MATCH_ORDER)}"
        )
    return tuple(labels)


def label_kind(label: str) -> str:
    value = str(label or "").strip()
    if value == "-1" or value.startswith("-1i"):
        return "flank"
    if INSERTION_LABEL_RE.match(value):
        return "insertion"
    if value.startswith("V"):
        return "variable-dialect"
    if value in SPRINZL_PARENT_INDEX:
        return "standard"
    return "unknown"


def order_key(label: str) -> tuple[int, int, int]:
    value = str(label or "").strip()
    insertion = INSERTION_LABEL_RE.match(value)
    if insertion:
        parent = insertion.group(1)
        if parent not in SPRINZL_PARENT_INDEX:
            raise ValueError(f"Unknown insertion parent: {label}")
        return SPRINZL_PARENT_INDEX[parent], 1, int(insertion.group(2))
    if value not in SPRINZL_PARENT_INDEX:
        raise ValueError(f"Unknown Sprinzl label: {label}")
    return SPRINZL_PARENT_INDEX[value], 0, 0


def labels_are_monotonic(labels: Iterable[str]) -> bool:
    keys = [order_key(label) for label in labels]
    return all(left < right for left, right in zip(keys, keys[1:]))


__all__ = [
    "INSERTION_LABEL_RE",
    "SPRINZL_MATCH_ORDER",
    "SPRINZL_PARENT_INDEX",
    "SPRINZL_PARENT_ORDER",
    "label_kind",
    "labels_are_monotonic",
    "labels_from_consensus_structure",
    "order_key",
]
