"""Map observed run evidence onto literature queries.

Queries are written in English because both the local PDF corpus and public
indices are English; a Chinese interface never sends its own words to search.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

DEFAULT_CATEGORIES: Sequence[str] = (
    "coordinate_standard",
    "detection_tool",
    "structure_alignment_method",
    "atypical_trna",
    "modification_function_disease",
    "engineered_trna",
)

# A Sprinzl region plus the reason it is being rechecked.
REGION_QUERIES: Dict[str, Tuple[str, ...]] = {
    "acceptor_stem": (
        "tRNA acceptor stem base pairs positions 1 to 7 and 66 to 72",
        "tRNA discriminator base N73 identity",
    ),
    "acceptor_tail": (
        "tRNA CCA addition 3 prime end CCA-adding enzyme",
        "mature tRNA 3' terminal CCA encoded or added post-transcriptionally",
    ),
    "d_arm": (
        "tRNA D arm base pairs 10 to 13 22 to 25 core folding",
        "tRNA D-loop non-canonical base pairs tertiary structure",
    ),
    "d_loop": (
        "tRNA D loop variable nucleotides 14 to 21",
    ),
    "anticodon_stem": (
        "tRNA anticodon stem positions 27 to 31 39 to 43",
    ),
    "anticodon_loop": (
        "tRNA anticodon loop positions 32 to 38 decoding",
        "tRNA anticodon wobble base pairing codon recognition",
    ),
    "variable_loop": (
        "tRNA variable loop length variation positions 44 to 48",
        "tRNA class I class II variable arm long short type",
    ),
    "t_arm": (
        "tRNA T arm base pairs 49 to 53 61 to 65",
        "tRNA T loop psi54 T55 C56 A58 tertiary interactions",
    ),
    "t_stem": (
        "tRNA T arm base pairs 49 to 53 61 to 65",
    ),
    "numbering_alignment": (
        "Sprinzl numbering of transfer RNA sequences standard positions",
        "tRNA secondary structure alignment common numbering scheme",
    ),
}

SITUATION_QUERIES: Dict[str, Tuple[str, ...]] = {
    "mitochondrial": (
        "animal mitochondrial tRNA missing D arm or T arm structure",
        "mitochondrial tRNA atypical secondary structure annotation",
    ),
    "viral": (
        "viral transfer RNA like gene structure",
    ),
    "intron": (
        "pre-tRNA intron removal and mature tRNA numbering",
    ),
    "selenocysteine": (
        "selenocysteine tRNA long acceptor stem elongator structure",
    ),
    "archaeal": (
        "archaeal tRNA gene intron maturation numbering",
    ),
    "cca_dispute": (
        "tRNA CCA tail 74 75 76 position assignment",
    ),
    "low_similarity_template": (
        "twilight zone RNA sequence alignment accuracy",
    ),
    "engineering": (
        "engineered suppressor tRNA anticodon identity element",
    ),
    "agent_review": (
        "large language model agent tool calling scientific workflow",
    ),
}

# Public indices need operator syntax, not a sentence: Europe PMC reads an
# unquoted sentence as a loose word list and returns recent papers that merely
# mention tRNA.  These queries are written for the index.
PUBLIC_QUERIES: Dict[str, str] = {
    "acceptor_stem": 'tRNA AND ("acceptor stem" AND ("base pair" OR identity))',
    "acceptor_tail": 'tRNA AND ("CCA-adding enzyme" OR "CCA addition")',
    "d_arm": 'tRNA AND "D-arm" AND (folding OR tertiary)',
    "d_loop": 'tRNA AND "D-loop"',
    "anticodon_stem": 'tRNA AND "anticodon stem"',
    "anticodon_loop": 'tRNA AND "anticodon loop" AND (decoding OR modification)',
    "variable_loop": 'tRNA AND "variable arm" AND (class I OR class II OR length)',
    "t_arm": 'tRNA AND ("T-loop" OR "T-arm") AND (tertiary OR "base pair")',
    "t_stem": 'tRNA AND "T-stem"',
    "numbering_alignment": 'tRNA AND (Sprinzl OR "standard numbering" OR "consistent numbering")',
    "mitochondrial": '("mitochondrial tRNA") AND ("D arm" OR "T arm" OR "atypical" OR shape)',
    "viral": '"viral tRNA" OR (virus AND "tRNA mimic")',
    "intron": 'tRNA AND intron AND (maturation OR "pre-tRNA")',
    "selenocysteine": '("selenocysteine tRNA" OR "Sec tRNA") AND (structure OR elongator)',
    "archaeal": 'archaeal tRNA AND (intron OR "post-transcriptional")',
    "cca_dispute": 'tRNA AND ("discriminator base" OR N73) AND CCA',
    "low_similarity_template": '"RNA" AND (alignment AND ("twilight zone" OR benchmark))',
    "engineering": '"suppressor tRNA" OR ("engineered tRNA" AND (anticodon OR identity))',
    "agent_review": '("large language model" OR LLM) AND (agent AND (workflow OR tool))',
}



# number_to_region() and the run table report regions with display names such as
# "D loop + D stem"; the query tables above use short keys, so the two vocabularies
# are joined here rather than at every call site.
DISPLAY_REGION_KEYS: Dict[str, Tuple[str, ...]] = {
    "Aminoacyl arm 5' end": ("acceptor_stem",),
    "Aminoacyl arm 3' end": ("acceptor_tail", "t_arm"),
    "D loop + D stem": ("d_arm", "d_loop"),
    "Anticodon loop + Anticodon stem": ("anticodon_stem", "anticodon_loop"),
    "Variable loop": ("variable_loop",),
    "T loop + T stem": ("t_arm",),
}


def keys_for_display_regions(regions: Iterable[str]) -> List[str]:
    """Translate reported region names into query keys, preserving order."""
    keys: List[str] = []
    for region in regions or ():
        name = str(region or "").strip()
        for key in DISPLAY_REGION_KEYS.get(name, ()):
            if key not in keys:
                keys.append(key)
    return keys


def public_query(kind: str, fallback: str) -> str:
    """Operator-style query for the public index, or the sentence as a last resort."""
    return PUBLIC_QUERIES.get(str(kind or "").lower()) or fallback


def queries_for_regions(regions: Iterable[str]) -> List[Tuple[str, str]]:
    """Return (query, kind) pairs for the regions a reviewer is looking at."""
    pairs: List[Tuple[str, str]] = []
    for region in regions or ():
        key = str(region or "").strip().lower()
        for query in REGION_QUERIES.get(key, ()):
            pairs.append((query, key))
    return pairs


def queries_for_situations(kinds: Iterable[str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for kind in kinds or ():
        key = str(kind or "").strip().lower()
        for query in SITUATION_QUERIES.get(key, ()):
            pairs.append((query, key))
    return pairs


def evidence_situations(evidence: Dict[str, object]) -> List[str]:
    """Infer which literature topics matter from a completed run's facts."""
    situations: List[str] = []
    lowered = {str(key).lower(): str(value).lower() for key, value in (evidence or {}).items()
               if isinstance(value, (str, int, float, bool))}
    domain = lowered.get("domain") or lowered.get("domain_hint") or lowered.get("effective_domain") or ""
    if "mitochond" in domain:
        situations.append("mitochondrial")
    if "virus" in domain:
        situations.append("viral")
    if "archae" in domain:
        situations.append("archaeal")
    if lowered.get("trna_class") in {"selenocysteine", "sec"}:
        situations.append("selenocysteine")
    if lowered.get("intron_status") in {"present", "true"}:
        situations.append("intron")
    if "missing_cca" in lowered.get("terminal_cca", "") or lowered.get("terminal_cca") in {"absent", "false"}:
        situations.append("cca_dispute")
    if lowered.get("template_similarity", "high") in {"low", "medium"}:
        situations.append("low_similarity_template")
    if "engineered" in lowered.get("sequence_origin", "") or "suppress" in lowered.get("note", ""):
        situations.append("engineering")
    return situations


def queries_for_evidence(evidence: Dict[str, object]) -> List[Tuple[str, str]]:
    regions = [str(item) for item in (evidence or {}).get("conflict_regions") or ()]
    if not regions:
        regions = ["numbering_alignment"]
    # Region queries say where to look and situation queries say what kind of
    # molecule this is.  Unbounded region lists crowd out the situations, so the
    # regions are capped and both groups always get a turn.
    pairs = queries_for_regions(regions[:3])
    pairs += queries_for_situations(evidence_situations(evidence))
    deduped: List[Tuple[str, str]] = []
    seen = set()
    for query, kind in pairs:
        if query in seen:
            continue
        seen.add(query)
        deduped.append((query, kind))
    return deduped


def category_for_kind(kind: str) -> Tuple[str, ...]:
    mapping = {
        "mitochondrial": ("atypical_trna", "database_resource"),
        "viral": ("atypical_trna",),
        "archaeal": ("atypical_trna", "detection_tool"),
        "intron": ("detection_tool", "coordinate_standard"),
        "selenocysteine": ("atypical_trna",),
        "cca_dispute": ("coordinate_standard", "modification_function_disease"),
        "low_similarity_template": ("structure_alignment_method", "evaluation_benchmark"),
        "engineering": ("engineered_trna", "modification_function_disease"),
        "agent_review": ("agent_method", "ai_context"),
        "acceptor_tail": ("coordinate_standard", "modification_function_disease"),
        "variable_loop": ("coordinate_standard", "atypical_trna"),
    }
    return mapping.get(str(kind).lower(), DEFAULT_CATEGORIES)
