"""PubMed retrieval, normalization, enrichment, filtering, and candidate insertion."""

from __future__ import annotations

import html
import json
import math
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import metadata, storage


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
USER_AGENT = "medical-knowledge-base-skill/2.0"


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return html.unescape("".join(element.itertext())).strip()


def _first_text(element: ET.Element, path: str) -> str | None:
    value = _element_text(element.find(path))
    return value or None


def _article_ids(article: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in article.findall(".//ArticleIdList/ArticleId"):
        id_type = (node.attrib.get("IdType") or "").lower()
        value = _element_text(node)
        if id_type and value:
            result[id_type] = value
    return result


def _doi(article: ET.Element, article_ids: dict[str, str]) -> str | None:
    if article_ids.get("doi"):
        return article_ids["doi"]
    for node in article.findall("./MedlineCitation/Article/ELocationID"):
        if (node.attrib.get("EIdType") or "").lower() == "doi":
            value = _element_text(node)
            if value:
                return value
    return None


def _issns(article: ET.Element) -> tuple[str | None, str | None]:
    print_issn = _first_text(article, "./MedlineCitation/MedlineJournalInfo/ISSNLinking")
    electronic_issn = None
    for node in article.findall("./MedlineCitation/Article/Journal/ISSN"):
        value = _element_text(node)
        issn_type = (node.attrib.get("IssnType") or "").lower()
        if issn_type == "electronic":
            electronic_issn = value or electronic_issn
        elif issn_type == "print":
            print_issn = value or print_issn
    return print_issn, electronic_issn


def _publication_year(article: ET.Element) -> int | None:
    candidates = [
        _first_text(article, "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year"),
        _first_text(article, "./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate"),
        _first_text(article, "./MedlineCitation/Article/ArticleDate/Year"),
        _first_text(article, "./PubmedData/History/PubMedPubDate[@PubStatus='pubmed']/Year"),
        _first_text(article, "./BookDocument/Book/PubDate/Year"),
        _first_text(article, "./BookDocument/Book/PubDate/MedlineDate"),
        _first_text(article, "./PubmedBookData/History/PubMedPubDate[@PubStatus='pubmed']/Year"),
    ]
    for value in candidates:
        match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
        if match:
            return int(match.group(1))
    return None


def _authors(article: ET.Element) -> list[dict[str, str]]:
    result = []
    nodes = [
        *article.findall("./MedlineCitation/Article/AuthorList/Author"),
        *article.findall("./BookDocument/AuthorList/Author"),
    ]
    for node in nodes:
        collective = _first_text(node, "./CollectiveName")
        if collective:
            result.append({"name": collective, "collective_name": collective})
            continue
        last_name = _first_text(node, "./LastName") or ""
        fore_name = _first_text(node, "./ForeName") or ""
        name = " ".join(part for part in (fore_name, last_name) if part).strip()
        if name:
            result.append({"name": name, "last_name": last_name, "fore_name": fore_name})
    return result


def _abstract(article: ET.Element) -> str | None:
    sections = []
    nodes = [
        *article.findall("./MedlineCitation/Article/Abstract/AbstractText"),
        *article.findall("./MedlineCitation/OtherAbstract/AbstractText"),
        *article.findall("./BookDocument/Abstract/AbstractText"),
    ]
    for node in nodes:
        text = _element_text(node)
        if not text:
            continue
        label = (node.attrib.get("Label") or "").strip()
        sections.append(f"{label}: {text}" if label else text)
    return "\n".join(sections) or None


def normalize_pubmed_xml(xml: str | bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    records = []
    article_nodes = [
        *( (article, False) for article in root.findall(".//PubmedArticle") ),
        *( (article, True) for article in root.findall(".//PubmedBookArticle") ),
    ]
    for article, is_book in article_nodes:
        article_ids = _article_ids(article)
        pmid_path = "./BookDocument/PMID" if is_book else "./MedlineCitation/PMID"
        title_path = (
            "./BookDocument/ArticleTitle"
            if is_book
            else "./MedlineCitation/Article/ArticleTitle"
        )
        journal_path = (
            "./BookDocument/Book/BookTitle"
            if is_book
            else "./MedlineCitation/Article/Journal/Title"
        )
        pmid = _first_text(article, pmid_path) or article_ids.get("pubmed")
        issn, eissn = _issns(article)
        publication_type_nodes = (
            article.findall("./BookDocument/PublicationType")
            if is_book
            else article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType")
        )
        publication_types = [
            _element_text(node) for node in publication_type_nodes if _element_text(node)
        ]
        mesh_terms = [
            _element_text(node)
            for node in article.findall("./MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName")
            if _element_text(node)
        ]
        pmcid = article_ids.get("pmc")
        records.append(
            {
                "pmid": pmid,
                "doi": _doi(article, article_ids),
                "pmcid": pmcid,
                "title": _first_text(article, title_path) or "Untitled",
                "authors": _authors(article),
                "journal": _first_text(article, journal_path),
                "issn": issn,
                "eissn": eissn,
                "publication_year": _publication_year(article),
                "publication_types": publication_types,
                "mesh_terms": mesh_terms,
                "abstract": _abstract(article),
                "open_access_url": (
                    f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/" if pmcid else None
                ),
                "raw_pubmed_xml": ET.tostring(article, encoding="unicode"),
            }
        )
    return records


def _open_url(url: str, *, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_records(query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    search_params = urllib.parse.urlencode(
        {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(max_results)}
    )
    search_data = json.loads(_open_url(EUTILS_BASE + "esearch.fcgi?" + search_params))
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    fetch_params = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
    )
    return normalize_pubmed_xml(_open_url(EUTILS_BASE + "efetch.fcgi?" + fetch_params))


def fetch_records_by_ids(pmids: list[str]) -> list[dict[str, Any]]:
    clean_ids = [str(pmid).strip() for pmid in pmids if str(pmid).strip()]
    if not clean_ids:
        return []
    fetch_params = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(clean_ids), "retmode": "xml"}
    )
    return normalize_pubmed_xml(_open_url(EUTILS_BASE + "efetch.fcgi?" + fetch_params))


def paper_matches_filters(record: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    criteria = filters or {}
    min_if = criteria.get("min_if")
    if min_if is not None:
        minimum = float(min_if)
        if not math.isfinite(minimum):
            raise ValueError("min_if must be finite")
        if record.get("if_2025") is None:
            return False
        if record.get("if_2025_censored") and minimum > 0:
            return False
        if float(record["if_2025"]) < minimum:
            return False
    allowed_quartiles = set(criteria.get("jcr_quartiles") or [])
    quartile_codes = {
        item.get("quartile")
        for item in record.get("jcr_quartiles", [])
        if item.get("quartile")
    }
    if allowed_quartiles and not allowed_quartiles & quartile_codes:
        return False
    study_types = set(criteria.get("study_types") or [])
    if study_types and record.get("study_type") not in study_types:
        return False
    year = record.get("publication_year")
    if criteria.get("year_from") and (not year or year < int(criteria["year_from"])):
        return False
    if criteria.get("year_to") and (not year or year > int(criteria["year_to"])):
        return False
    return True


def _record_exists(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    key = metadata.dedupe_key(record)
    bibliography_key = metadata.bibliographic_key(record)
    pmid = str(record.get("pmid") or "").strip()
    doi = metadata.normalize_doi(str(record.get("doi") or ""))
    if pmid and conn.execute("select 1 from papers where pmid = ?", (pmid,)).fetchone():
        return True
    if conn.execute(
        "select 1 from candidate_papers where dedupe_key = ?", (key,)
    ).fetchone():
        return True
    if pmid and conn.execute(
        "select 1 from candidate_papers where pmid = ?", (pmid,)
    ).fetchone():
        return True
    if doi:
        doi_rows = [
            *conn.execute("select doi from papers where doi is not null").fetchall(),
            *conn.execute("select doi from candidate_papers where doi is not null").fetchall(),
        ]
        if any(metadata.normalize_doi(row["doi"]) == doi for row in doi_rows):
            return True
    year = record.get("publication_year")
    if year is None:
        paper_rows = conn.execute(
            "select title, journal, publication_year from papers"
        ).fetchall()
        candidate_rows = conn.execute(
            "select title, journal, publication_year from candidate_papers"
        ).fetchall()
    else:
        paper_rows = conn.execute(
            "select title, journal, publication_year from papers where publication_year = ?",
            (year,),
        ).fetchall()
        candidate_rows = conn.execute(
            """
            select title, journal, publication_year
            from candidate_papers
            where publication_year = ?
            """,
            (year,),
        ).fetchall()
    return any(
        metadata.bibliographic_key(dict(row)) == bibliography_key
        for row in [*paper_rows, *candidate_rows]
    )


def _insert_candidate(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    *,
    query: str,
    search_id: int | None,
    filters: dict[str, Any],
) -> int:
    raw_record = dict(record)
    raw_record["filter_decision"] = {"matched": True, "filters": filters}
    raw_record["search_provenance"] = {"query": query, "search_id": search_id}
    cur = conn.execute(
        """
        insert into candidate_papers
            (pmid, doi, title, authors_json, journal, issn, eissn,
             publication_year, study_type, if_2025, jcr_quartiles_json,
             raw_json, status, search_id, dedupe_key, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            record.get("pmid"),
            metadata.normalize_doi(record.get("doi")) or None,
            record["title"],
            json.dumps(record.get("authors", []), ensure_ascii=False),
            record.get("journal"),
            record.get("issn"),
            record.get("eissn"),
            record.get("publication_year"),
            record.get("study_type"),
            record.get("if_2025"),
            json.dumps(record.get("jcr_quartiles", []), ensure_ascii=False),
            json.dumps(raw_record, ensure_ascii=False),
            search_id,
            metadata.dedupe_key(record),
            storage.utc_now(),
        ),
    )
    return int(cur.lastrowid)


def insert_candidate_if_new(
    kb_path: str | Path,
    record: dict[str, Any],
    *,
    query: str,
    filters: dict[str, Any],
    search_id: int | None,
) -> int | None:
    with storage.connect(kb_path) as conn:
        conn.execute("begin immediate")
        if _record_exists(conn, record):
            conn.rollback()
            return None
        candidate_id = _insert_candidate(
            conn,
            record,
            query=query,
            search_id=search_id,
            filters=filters,
        )
        conn.commit()
        return candidate_id


def search_to_candidates(
    kb_path: str | Path,
    *,
    query: str,
    max_results: int = 20,
    filters: dict[str, Any] | None = None,
    search_id: int | None = None,
    jcr_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    storage.init_kb(kb_path)
    criteria = dict(filters or {})
    catalog = jcr_catalog or metadata.load_jcr_catalog()
    raw_records = fetch_records(query, max_results=max_results)
    candidate_ids: list[int] = []
    duplicates = 0
    filtered = 0
    for raw_record in raw_records:
        record = metadata.enrich_record(raw_record, catalog)
        if not paper_matches_filters(record, criteria):
            filtered += 1
            continue
        candidate_id = insert_candidate_if_new(
            kb_path,
            record,
            query=query,
            search_id=search_id,
            filters=criteria,
        )
        if candidate_id is None:
            duplicates += 1
            continue
        candidate_ids.append(candidate_id)
    return {
        "query": query,
        "total": len(raw_records),
        "created": len(candidate_ids),
        "duplicates": duplicates,
        "filtered": filtered,
        "candidate_ids": candidate_ids,
    }
