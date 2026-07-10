import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import retrieval, storage  # noqa: E402


def indexed_paper(tmp_path, text, *, title="Indexed paper"):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    paper_id = kb.insert_paper(kb_path, {"title": title, "source": "test"})
    retrieval.add_chunks(kb_path, paper_id, text, source_locator="abstract")
    return kb_path, paper_id


def test_chinese_question_retrieves_relevant_chunk(tmp_path):
    kb_path, paper_id = indexed_paper(
        tmp_path,
        "度普利尤单抗可降低严重哮喘急性发作",
    )
    other_id = kb.insert_paper(kb_path, {"title": "Unrelated", "source": "test"})
    retrieval.add_chunks(kb_path, other_id, "高血压患者应监测血压", source_locator="abstract")

    packet = retrieval.retrieve_evidence(
        kb_path,
        question="度普利尤单抗对哮喘急性发作有什么影响？",
    )

    assert packet["evidence"][0]["paper_id"] == paper_id
    assert packet["answer_policy"].startswith("Use only this evidence")


def test_selected_document_scope_excludes_better_global_match(tmp_path):
    kb_path, selected_id = indexed_paper(tmp_path, "Treatment evidence is limited.")
    global_id = kb.insert_paper(kb_path, {"title": "Global match", "source": "test"})
    retrieval.add_chunks(
        kb_path,
        global_id,
        "Dupilumab treatment substantially reduced asthma exacerbations.",
        source_locator="abstract",
    )

    packet = retrieval.retrieve_evidence(
        kb_path,
        question="dupilumab asthma exacerbations",
        doc_ids=[selected_id],
    )

    assert all(item["paper_id"] == selected_id for item in packet["evidence"])
    assert packet["scope"] == {"doc_ids": [selected_id]}


def test_fallback_retrieval_works_without_fts5_table(tmp_path):
    kb_path, paper_id = indexed_paper(tmp_path, "Primary endpoint improved after treatment.")
    with storage.connect(kb_path) as conn:
        conn.execute("drop table if exists chunks_fts")
        conn.commit()

    packet = retrieval.retrieve_evidence(kb_path, question="primary endpoint")

    assert packet["evidence"][0]["paper_id"] == paper_id
    assert packet["retrieval_method"] == "scored_fallback"


def test_reindex_rebuilds_chunks_from_abstract_and_extracted_text(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    full_text = kb_path / "files" / "extracted-text" / "source.txt"
    full_text.write_text("Secondary outcome remained stable.", encoding="utf-8")
    paper_id = kb.insert_paper(
        kb_path,
        {"title": "Reindexed paper", "source": "test"},
    )
    with storage.connect(kb_path) as conn:
        conn.execute(
            "update papers set abstract = ?, full_text_path = ? where id = ?",
            ("Primary endpoint improved.", str(full_text), paper_id),
        )
        conn.commit()

    preview = retrieval.preview_reindex(kb_path, [paper_id])
    result = retrieval.reindex_library(kb_path, [paper_id])

    assert preview["paper_ids"] == [paper_id]
    assert preview["source_documents"] == 2
    assert result["rebuilt"] == [paper_id]
    assert result["chunks_created"] == 2
    assert retrieval.retrieve_evidence(kb_path, question="primary endpoint")["evidence"]
    assert retrieval.retrieve_evidence(kb_path, question="secondary outcome")["evidence"]


def test_reindex_selected_ids_leaves_other_chunks_intact(tmp_path):
    kb_path, first_id = indexed_paper(tmp_path, "First old text", title="First")
    second_id = kb.insert_paper(kb_path, {"title": "Second", "source": "test"})
    retrieval.add_chunks(kb_path, second_id, "Second retained text", source_locator="abstract")
    with storage.connect(kb_path) as conn:
        conn.execute("update papers set abstract = ? where id = ?", ("First new text", first_id))
        conn.commit()

    retrieval.reindex_library(kb_path, [first_id])

    assert retrieval.retrieve_evidence(kb_path, question="first new")["evidence"][0]["paper_id"] == first_id
    assert retrieval.retrieve_evidence(kb_path, question="second retained")["evidence"][0]["paper_id"] == second_id


def test_reindex_preview_reports_requested_ids_that_do_not_exist(tmp_path):
    kb_path, paper_id = indexed_paper(tmp_path, "Existing evidence")

    preview = retrieval.preview_reindex(kb_path, [paper_id, 9999])
    result = retrieval.reindex_library(kb_path, [paper_id, 9999])

    assert preview["missing_ids"] == [9999]
    assert result["missing_ids"] == [9999]


def test_reindex_preview_does_not_extract_source_documents(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    source = kb_path / "files" / "uploads" / "paper.pdf"
    source.write_bytes(b"placeholder")
    paper_id = kb.insert_paper(kb_path, {"title": "Source paper", "source": "test"})
    with storage.connect(kb_path) as conn:
        conn.execute("update papers set source_path = ? where id = ?", (str(source), paper_id))
        conn.commit()
    monkeypatch.setattr(
        retrieval.imports,
        "extract_text",
        lambda path: (_ for _ in ()).throw(AssertionError("preview extracted source")),
    )

    preview = retrieval.preview_reindex(kb_path, [paper_id])

    assert preview["source_documents"] == 1
    assert preview["without_sources"] == []


def test_search_terms_are_deduplicated_and_include_chinese_bigrams():
    assert retrieval.search_terms("Asthma asthma 严重哮喘") == [
        "asthma",
        "严重",
        "重哮",
        "哮喘",
    ]


def test_reindex_help_requires_explicit_scope_and_offers_preview():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "kb.py"), "library", "reindex", "--help"],
        capture_output=True,
        check=True,
        text=True,
    )

    assert "--ids" in completed.stdout
    assert "--all" in completed.stdout
    assert "--preview" in completed.stdout
