import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import approval, pdfs, storage  # noqa: E402


def add_candidate(kb_path, *, pmcid="PMC123", open_access_pdf_url=None):
    storage.init_kb(kb_path)
    return kb.add_candidate(
        kb_path,
        {
            "pmid": "12345678",
            "title": "Approved trial",
            "journal": "Lancet",
            "publication_year": 2026,
            "study_type": "随机对照试验",
            "abstract": "Primary endpoint improved with treatment.",
            "pmcid": pmcid,
            "open_access_pdf_url": open_access_pdf_url,
        },
    )


def tasks(kb_path):
    with storage.connect(kb_path) as conn:
        return [storage.row_to_dict(row) for row in conn.execute("select * from tasks order by id")]


def papers(kb_path):
    with storage.connect(kb_path) as conn:
        return [storage.row_to_dict(row) for row in conn.execute("select * from papers order by id")]


def test_approval_indexes_abstract_and_completes_pmc_pdf_task(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    candidate_id = add_candidate(kb_path)
    seen_urls = []

    def fake_download(url):
        seen_urls.append(url)
        return b"%PDF-1.7\nmock"

    monkeypatch.setattr(pdfs, "download_open_access_pdf", fake_download)

    result = approval.approve_candidates(kb_path, [candidate_id])

    assert result["approved"] == [candidate_id]
    assert len(result["paper_ids"]) == 1
    assert len(result["pdf_task_ids"]) == 1
    paper = papers(kb_path)[0]
    assert paper["abstract"] == "Primary endpoint improved with treatment."
    assert paper["has_pdf"] is True
    assert Path(paper["pdf_path"]).read_bytes().startswith(b"%PDF")
    with storage.connect(kb_path) as conn:
        chunk = conn.execute("select text, source_locator from chunks").fetchone()
        candidate_status = conn.execute(
            "select status from candidate_papers where id = ?", (candidate_id,)
        ).fetchone()[0]
    assert tuple(chunk) == ("Primary endpoint improved with treatment.", "abstract")
    assert candidate_status == "approved"
    assert tasks(kb_path)[0]["status"] == "completed"
    assert seen_urls == ["https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/pdf/"]


def test_pdf_task_is_not_created_when_config_disabled(tmp_path):
    kb_path = tmp_path / "kb"
    candidate_id = add_candidate(kb_path)
    config_path = kb_path / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"),
        encoding="utf-8",
    )

    result = approval.approve_candidates(kb_path, [candidate_id])

    assert result["approved"] == [candidate_id]
    assert result["pdf_task_ids"] == []
    assert tasks(kb_path) == []


def test_pdf_failure_does_not_roll_back_approved_paper(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    candidate_id = add_candidate(kb_path)

    def fail_download(url):
        raise RuntimeError("network failed")

    monkeypatch.setattr(pdfs, "download_open_access_pdf", fail_download)

    result = approval.approve_candidates(kb_path, [candidate_id])

    assert result["approved"] == [candidate_id]
    assert len(papers(kb_path)) == 1
    assert papers(kb_path)[0]["has_pdf"] is False
    task = tasks(kb_path)[0]
    assert task["status"] == "failed"
    assert task["error"]["message"] == "network failed"


def test_no_open_access_source_completes_task_as_not_found(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    candidate_id = add_candidate(kb_path, pmcid=None)
    monkeypatch.setattr(pdfs, "lookup_pmcid_for_pmid", lambda pmid: None)

    result = approval.approve_candidates(kb_path, [candidate_id])

    assert result["approved"] == [candidate_id]
    task = tasks(kb_path)[0]
    assert task["status"] == "completed"
    assert task["result"]["status"] == "not_found"
    assert papers(kb_path)[0]["has_pdf"] is False


def test_explicit_open_access_pdf_url_is_allowed_but_ordinary_url_is_not():
    assert pdfs.allowed_pdf_url(
        {"open_access_pdf_url": "https://publisher.example/article/open.pdf"}
    ) == "https://publisher.example/article/open.pdf"
    assert pdfs.allowed_pdf_url(
        {"open_access_url": "https://publisher.example/article"}
    ) is None
    assert pdfs.allowed_pdf_url({"pmcid": "PMC123/../../outside"}) is None


def test_kb_approve_candidates_keeps_list_returning_compatibility(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    candidate_id = add_candidate(kb_path)
    monkeypatch.setattr(pdfs, "download_open_access_pdf", lambda url: b"%PDF-1.7")

    approved = kb.approve_candidates(kb_path, [candidate_id])

    assert approved == [candidate_id]


def test_duplicate_candidate_does_not_roll_back_other_approvals(tmp_path):
    kb_path = tmp_path / "kb"
    duplicate_id = add_candidate(kb_path, pmcid=None)
    kb.insert_paper(
        kb_path,
        {"pmid": "12345678", "title": "Already approved", "source": "manual"},
    )
    other_id = kb.add_candidate(
        kb_path,
        {
            "pmid": "87654321",
            "title": "Independent candidate",
            "abstract": "Independent evidence.",
        },
    )

    result = approval.approve_candidates(
        kb_path,
        [duplicate_id, other_id],
        run_pdf_tasks=False,
    )

    assert result["approved"] == [other_id]
    assert result["skipped"] == [
        {"candidate_id": duplicate_id, "reason": "already in library"}
    ]
    with storage.connect(kb_path) as conn:
        statuses = dict(conn.execute("select id, status from candidate_papers"))
    assert statuses == {duplicate_id: "pending", other_id: "approved"}
    assert [paper["pmid"] for paper in papers(kb_path)] == ["12345678", "87654321"]


def test_approval_normalizes_doi_before_duplicate_check(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    kb.insert_paper(
        kb_path,
        {"doi": "10.1000/example", "title": "Existing DOI", "source": "manual"},
    )
    candidate_id = kb.add_candidate(
        kb_path,
        {"doi": "https://doi.org/10.1000/EXAMPLE", "title": "Duplicate DOI"},
    )

    result = approval.approve_candidates(
        kb_path,
        [candidate_id],
        run_pdf_tasks=False,
    )

    assert result["approved"] == []
    assert result["skipped"] == [
        {"candidate_id": candidate_id, "reason": "already in library"}
    ]
