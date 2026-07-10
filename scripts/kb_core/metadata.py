"""Biomedical metadata normalization, JCR enrichment, and study typing."""

from __future__ import annotations

import csv
import copy
import functools
import hashlib
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JCR_PATH = SKILL_ROOT / "assets" / "data" / "JCR2025-UTF8.csv"


def normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^the\s+", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_issn(value: str | None) -> str:
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def normalize_doi(value: str | None) -> str:
    doi = (value or "").strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def parse_jcr_row(raw: dict[str, str]) -> dict[str, Any]:
    quartiles = []
    for index in range(1, 7):
        category = (raw.get(f"Category_{index}") or "").strip()
        quartile = (raw.get(f"IF Quartile(2025)_{index}") or "").strip()
        rank = (raw.get(f"IF Rank(2025)_{index}") or "").strip()
        if category or quartile or rank:
            quartiles.append({"category": category, "quartile": quartile, "rank": rank})
    if_value = None
    if_censored = False
    if_raw = (raw.get("IF(2025)") or "").strip()
    if if_raw:
        try:
            if_censored = if_raw.startswith("<")
            if_value = float(if_raw[1:] if if_censored else if_raw)
        except ValueError:
            pass
    journal = (raw.get("Journal") or "").strip()
    return {
        "journal": journal,
        "journal_key": normalize_text(journal),
        "issn": normalize_issn(raw.get("ISSN")),
        "eissn": normalize_issn(raw.get("EISSN")),
        "web_of_science": (raw.get("Web of Science") or "").strip(),
        "if_2025": if_value,
        "if_2025_display": if_raw or None,
        "if_2025_censored": if_censored,
        "quartiles": quartiles,
    }


@functools.lru_cache(maxsize=4)
def _load_jcr_catalog_cached(
    path: str,
    content_sha256: str,
) -> dict[str, Any]:
    del content_sha256
    rows: list[dict[str, Any]] = []
    by_issn: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            entry = parse_jcr_row(raw)
            rows.append(entry)
            for value in (entry.get("issn"), entry.get("eissn")):
                if value:
                    by_issn[value] = entry
            if entry.get("journal_key"):
                by_title[entry["journal_key"]] = entry
    return {"rows": rows, "by_issn": by_issn, "by_title": by_title}


def load_jcr_catalog(path: str | Path = DEFAULT_JCR_PATH) -> dict[str, Any]:
    resolved = Path(path).resolve()
    with resolved.open("rb") as handle:
        content_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    catalog = _load_jcr_catalog_cached(str(resolved), content_sha256)
    return copy.deepcopy(catalog)


def match_jcr(
    catalog: dict[str, Any],
    *,
    journal: str | None = None,
    issn: str | None = None,
    eissn: str | None = None,
) -> dict[str, Any] | None:
    for label, value in (("issn", issn), ("eissn", eissn)):
        key = normalize_issn(value)
        if key and key in catalog["by_issn"]:
            match = dict(catalog["by_issn"][key])
            match["match_method"] = label
            return match
    title_key = normalize_text(journal)
    if title_key and title_key in catalog["by_title"]:
        match = dict(catalog["by_title"][title_key])
        match["match_method"] = "journal"
        return match
    return None


STUDY_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("随机对照试验", ["randomized controlled trial", "randomised controlled trial"]),
    ("非随机对照试验", ["controlled clinical trial", "non-randomized", "nonrandomized"]),
    ("观察性研究", ["observational study", "cohort", "cross-sectional", "case-control"]),
    ("病例报告/病例系列报告", ["case reports", "case report", "case series"]),
    ("Meta分析", ["meta-analysis", "meta analysis"]),
    ("系统性综述", ["systematic review"]),
    ("指南/共识", ["practice guideline", "guideline", "consensus development conference", "consensus"]),
    ("信件/讲义", ["letter", "lecture"]),
    ("回顾性研究", ["retrospective studies", "retrospective study"]),
    ("期刊论文", ["journal article"]),
    ("社论/评论", ["editorial", "comment"]),
    ("文献综述", ["review"]),
    ("会议内容", ["congress", "conference"]),
    ("勘误", ["published erratum", "erratum", "correction"]),
    ("临床研究", ["clinical trial", "clinical study", "clinical research"]),
]


def classify_study_type(
    publication_types: list[str] | None,
    mesh_terms: list[str] | None = None,
) -> str:
    haystack = " | ".join([*(publication_types or []), *(mesh_terms or [])]).lower()
    for label, needles in STUDY_TYPE_RULES:
        if any(needle in haystack for needle in needles):
            return label
    return "unknown"


def bibliographic_key(record: dict[str, Any]) -> str:
    identity = "|".join(
        normalize_text(str(record.get(key) or ""))
        for key in ("title", "journal", "publication_year")
    )
    return "meta:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def dedupe_key(record: dict[str, Any]) -> str:
    pmid = str(record.get("pmid") or "").strip()
    if pmid:
        return f"pmid:{pmid}"
    doi = normalize_doi(str(record.get("doi") or ""))
    if doi:
        return f"doi:{doi}"
    return bibliographic_key(record)


def enrich_record(record: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(record)
    enriched["study_type"] = classify_study_type(
        enriched.get("publication_types"), enriched.get("mesh_terms")
    )
    match = match_jcr(
        catalog,
        journal=enriched.get("journal"),
        issn=enriched.get("issn"),
        eissn=enriched.get("eissn"),
    )
    if match:
        enriched["if_2025"] = match.get("if_2025")
        enriched["if_2025_display"] = match.get("if_2025_display")
        enriched["if_2025_censored"] = bool(match.get("if_2025_censored"))
        enriched["jcr_quartiles"] = list(match.get("quartiles") or [])
        enriched["jcr_match_method"] = match.get("match_method")
        enriched["jcr_journal"] = match.get("journal")
    else:
        enriched.setdefault("if_2025", None)
        enriched.setdefault("if_2025_display", None)
        enriched.setdefault("if_2025_censored", False)
        enriched.setdefault("jcr_quartiles", [])
        enriched["jcr_match_method"] = None
        enriched["jcr_journal"] = None
    enriched["dedupe_key"] = dedupe_key(enriched)
    return enriched
