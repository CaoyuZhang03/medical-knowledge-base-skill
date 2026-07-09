import csv
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402


def test_init_kb_creates_expected_layout(tmp_path):
    kb_path = tmp_path / "my-kb"

    result = kb.init_kb(kb_path)

    assert result["kb_path"] == str(kb_path)
    assert (kb_path / "kb.sqlite").is_file()
    assert (kb_path / "config.yaml").is_file()
    assert (kb_path / "kb-passport.yaml").is_file()
    assert (kb_path / "files" / "uploads").is_dir()
    assert (kb_path / "files" / "pdfs").is_dir()
    assert (kb_path / "files" / "extracted-text").is_dir()
    assert (kb_path / "logs").is_dir()

    with sqlite3.connect(kb_path / "kb.sqlite") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table', 'view')"
            )
        }
    assert {"papers", "candidate_papers", "saved_searches", "tasks", "chunks"}.issubset(tables)


def test_jcr_matching_prefers_issn_and_preserves_all_quartiles(tmp_path):
    jcr_path = tmp_path / "jcr.csv"
    with jcr_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Journal",
                "ISSN",
                "EISSN",
                "Web of Science",
                "IF(2025)",
                "Category_1",
                "IF Quartile(2025)_1",
                "IF Rank(2025)_1",
                "Category_2",
                "IF Quartile(2025)_2",
                "IF Rank(2025)_2",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Journal": "LANCET",
                "ISSN": "0140-6736",
                "EISSN": "1474-547X",
                "Web of Science": "SCIE",
                "IF(2025)": "109",
                "Category_1": "MEDICINE, GENERAL & INTERNAL",
                "IF Quartile(2025)_1": "Q1",
                "IF Rank(2025)_1": "1/336",
                "Category_2": "PUBLIC HEALTH",
                "IF Quartile(2025)_2": "Q1",
                "IF Rank(2025)_2": "2/443",
            }
        )

    catalog = kb.load_jcr_catalog(jcr_path)
    match = kb.match_jcr(catalog, journal="The Lancet", issn="0140-6736", eissn=None)

    assert match["journal"] == "LANCET"
    assert match["if_2025"] == 109.0
    assert match["match_method"] == "issn"
    assert match["quartiles"] == [
        {
            "category": "MEDICINE, GENERAL & INTERNAL",
            "quartile": "Q1",
            "rank": "1/336",
        },
        {"category": "PUBLIC HEALTH", "quartile": "Q1", "rank": "2/443"},
    ]


@pytest.mark.parametrize(
    ("publication_types", "mesh_terms", "expected"),
    [
        (["Randomized Controlled Trial"], [], "随机对照试验"),
        (["Controlled Clinical Trial"], [], "非随机对照试验"),
        (["Observational Study"], [], "观察性研究"),
        (["Case Reports"], [], "病例报告/病例系列报告"),
        (["Meta-Analysis"], [], "Meta分析"),
        (["Systematic Review"], [], "系统性综述"),
        (["Practice Guideline"], [], "指南/共识"),
        (["Letter"], [], "信件/讲义"),
        (["Retrospective Studies"], [], "回顾性研究"),
        (["Journal Article"], [], "期刊论文"),
        (["Editorial"], [], "社论/评论"),
        (["Review"], [], "文献综述"),
        (["Congress"], [], "会议内容"),
        (["Published Erratum"], [], "勘误"),
        ([], ["Clinical Trial"], "临床研究"),
    ],
)
def test_classify_study_type(publication_types, mesh_terms, expected):
    assert kb.classify_study_type(publication_types, mesh_terms) == expected


def test_candidates_require_explicit_approval_before_library_insert(tmp_path):
    kb_path = tmp_path / "my-kb"
    kb.init_kb(kb_path)

    candidate_id = kb.add_candidate(
        kb_path,
        {
            "pmid": "12345",
            "title": "Trial of a therapy",
            "journal": "LANCET",
            "publication_year": 2026,
            "study_type": "随机对照试验",
        },
    )

    assert kb.list_papers(kb_path) == []
    kb.reject_candidates(kb_path, [candidate_id], reason="not relevant")
    assert kb.list_papers(kb_path) == []

    candidate_id = kb.add_candidate(
        kb_path,
        {
            "pmid": "67890",
            "title": "Second trial",
            "journal": "LANCET",
            "publication_year": 2026,
            "study_type": "随机对照试验",
        },
    )
    approved = kb.approve_candidates(kb_path, [candidate_id])

    assert approved == [candidate_id]
    papers = kb.list_papers(kb_path)
    assert len(papers) == 1
    assert papers[0]["pmid"] == "67890"


def test_qa_retrieve_returns_evidence_packet_for_selected_document(tmp_path):
    kb_path = tmp_path / "my-kb"
    kb.init_kb(kb_path)
    paper_id = kb.insert_paper(
        kb_path,
        {
            "pmid": "24680",
            "title": "Dupilumab long term efficacy",
            "journal": "JAMA Dermatology",
            "publication_year": 2025,
            "study_type": "临床研究",
        },
    )
    kb.add_chunk(
        kb_path,
        paper_id=paper_id,
        chunk_text="Dupilumab improved long-term eczema severity in children.",
        source_locator="abstract",
    )

    packet = kb.retrieve_evidence(
        kb_path,
        question="What improved eczema severity?",
        doc_ids=[paper_id],
        limit=3,
    )

    assert packet["question"] == "What improved eczema severity?"
    assert packet["scope"]["doc_ids"] == [paper_id]
    assert packet["evidence"][0]["paper_id"] == paper_id
    assert "Dupilumab improved" in packet["evidence"][0]["text"]


def test_topic_pubmed_search_requires_confirmation(tmp_path):
    kb_path = tmp_path / "my-kb"
    kb.init_kb(kb_path)

    with pytest.raises(kb.SafetyGateError, match="confirm"):
        kb.pubmed_search(kb_path, topic="dupilumab pediatric atopic dermatitis", confirmed=False)


def test_pdf_fetch_skips_records_without_open_access_identifier(tmp_path):
    kb_path = tmp_path / "my-kb"
    kb.init_kb(kb_path)
    paper_id = kb.insert_paper(
        kb_path,
        {
            "title": "No PMID local document",
            "journal": "Local Notes",
            "source": "manual",
        },
    )

    results = kb.fetch_open_access_pdfs(kb_path, doc_id=paper_id)

    assert results == [
        {
            "paper_id": paper_id,
            "status": "skipped",
            "reason": "missing PMID; upload PDF manually or add a PMCID-capable record",
        }
    ]
    assert kb.list_papers(kb_path)[0]["has_pdf"] is False
