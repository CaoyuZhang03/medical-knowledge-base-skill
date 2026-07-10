import sys
import zipfile
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import imports, storage  # noqa: E402


def write_inline_xlsx(path: Path) -> None:
    rows = [
        ["title", "authors", "journal", "doi", "pmid", "year", "abstract"],
        ["First trial", "Jane Smith; Li Chen", "Lancet", "10.1/first", "1001", "2025", "First abstract"],
        ["Second review", "Alex Doe", "BMJ", "10.1/second", "1002", "2024", "Second abstract"],
    ]
    row_xml = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(values):
            column = chr(ord("A") + column_index)
            cells.append(
                f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def write_docx(path: Path, text: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def make_structured_files(tmp_path: Path) -> dict[str, Path]:
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "title,authors,journal,doi,pmid,year,abstract\n"
        "First trial,Jane Smith;Li Chen,Lancet,10.1/first,1001,2025,First abstract\n"
        "Second review,Alex Doe,BMJ,10.1/second,1002,2024,Second abstract\n",
        encoding="utf-8",
    )
    xlsx_path = tmp_path / "papers.xlsx"
    write_inline_xlsx(xlsx_path)
    ris_path = tmp_path / "papers.ris"
    ris_path.write_text(
        "TY  - JOUR\nTI  - First trial\nAU  - Smith, Jane\nJO  - Lancet\nDO  - 10.1/first\nPY  - 2025\nAB  - First abstract\nER  -\n"
        "TY  - JOUR\nTI  - Second review\nAU  - Doe, Alex\nJO  - BMJ\nDO  - 10.1/second\nPY  - 2024\nAB  - Second abstract\nER  -\n",
        encoding="utf-8",
    )
    bib_path = tmp_path / "papers.bib"
    bib_path.write_text(
        "@article{first, title={First trial}, author={Smith, Jane and Chen, Li}, journal={Lancet}, year={2025}, doi={10.1/first}, abstract={First abstract}}\n"
        "@article{second, title={Second review}, author={Doe, Alex}, journal={BMJ}, year={2024}, doi={10.1/second}, abstract={Second abstract}}\n",
        encoding="utf-8",
    )
    pmid_path = tmp_path / "papers.pmids"
    pmid_path.write_text("1001\n1002\n", encoding="utf-8")
    return {"csv": csv_path, "xlsx": xlsx_path, "ris": ris_path, "bibtex": bib_path, "pmid-list": pmid_path}


def fake_pmid_fetch(pmids):
    return [
        {
            "pmid": pmid,
            "title": f"PMID {pmid}",
            "journal": "Test Journal",
            "publication_year": 2025,
            "authors": [],
            "publication_types": ["Journal Article"],
            "mesh_terms": [],
            "abstract": f"Evidence for {pmid}",
        }
        for pmid in pmids
    ]


def test_detect_and_parse_every_structured_format(tmp_path):
    files = make_structured_files(tmp_path)

    assert imports.detect_import_kind(files["csv"]) == "csv"
    assert imports.detect_import_kind(files["xlsx"]) == "xlsx"
    assert imports.detect_import_kind(files["ris"]) == "ris"
    assert imports.detect_import_kind(files["bibtex"]) == "bibtex"
    assert imports.detect_import_kind(files["pmid-list"]) == "pmid-list"
    assert [row["title"] for row in imports.parse_csv(files["csv"])] == ["First trial", "Second review"]
    assert [row["title"] for row in imports.parse_xlsx(files["xlsx"])] == ["First trial", "Second review"]
    assert [row["title"] for row in imports.parse_ris(files["ris"])] == ["First trial", "Second review"]
    assert [row["title"] for row in imports.parse_bibtex(files["bibtex"])] == ["First trial", "Second review"]
    assert imports.parse_pmid_list(files["pmid-list"]) == ["1001", "1002"]


def test_structured_csv_import_creates_one_approved_paper_per_row(tmp_path):
    files = make_structured_files(tmp_path)
    kb_path = tmp_path / "kb"

    result = imports.import_paths(kb_path, [files["csv"]])

    assert result["created"] == 2
    assert result["duplicates"] == 0
    with storage.connect(kb_path) as conn:
        papers = [storage.row_to_dict(row) for row in conn.execute("select * from papers order by id")]
        chunks = conn.execute("select text from chunks order by id").fetchall()
    assert [paper["title"] for paper in papers] == ["First trial", "Second review"]
    assert all(paper["source_path"] for paper in papers)
    assert [row["text"] for row in chunks] == ["First abstract", "Second abstract"]


def test_pmid_list_imports_user_selected_records_as_approved(tmp_path):
    files = make_structured_files(tmp_path)
    kb_path = tmp_path / "kb"

    result = imports.import_paths(kb_path, [files["pmid-list"]], pubmed_fetch=fake_pmid_fetch)

    assert result["created"] == 2
    with storage.connect(kb_path) as conn:
        papers = conn.execute("select pmid, title from papers order by pmid").fetchall()
    assert [tuple(row) for row in papers] == [("1001", "PMID 1001"), ("1002", "PMID 1002")]


def test_text_import_preserves_source_indexes_all_text_and_deduplicates(tmp_path):
    source = tmp_path / "notes.txt"
    text = "甲" * 3500
    source.write_text(text, encoding="utf-8")
    kb_path = tmp_path / "kb"

    first = imports.import_paths(kb_path, [source])
    second = imports.import_paths(kb_path, [source])

    assert first["created"] == 1
    assert second["duplicates"] == 1
    with storage.connect(kb_path) as conn:
        paper = storage.row_to_dict(conn.execute("select * from papers").fetchone())
        chunks = [row["text"] for row in conn.execute("select text from chunks order by id")]
    assert Path(paper["source_path"]).is_file()
    assert Path(paper["full_text_path"]).read_text(encoding="utf-8") == text
    reconstructed = chunks[0] + "".join(chunk[120:] for chunk in chunks[1:])
    assert reconstructed == text


def test_chunk_text_preserves_long_content_with_overlap():
    source = "0123456789" * 350

    chunks = imports.chunk_text(source, max_chars=1200, overlap=120)

    assert len(chunks) >= 3
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert chunks[0][-120:] == chunks[1][:120]
    assert chunks[0] + "".join(chunk[120:] for chunk in chunks[1:]) == source


def test_chunk_text_preserves_outer_whitespace():
    source = "  leading\n" + "x" * 1400 + "\ntrailing  "

    chunks = imports.chunk_text(source, max_chars=500, overlap=50)

    assert chunks[0] + "".join(chunk[50:] for chunk in chunks[1:]) == source


def test_docx_and_pdf_import_extract_text_and_record_pdf_status(tmp_path):
    fitz = pytest.importorskip("fitz")
    docx_path = tmp_path / "paper.docx"
    write_docx(docx_path, "DOCX evidence")
    pdf_path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "PDF evidence")
    document.save(pdf_path)
    document.close()

    result = imports.import_paths(tmp_path / "kb", [docx_path, pdf_path])

    assert result["created"] == 2
    with storage.connect(tmp_path / "kb") as conn:
        papers = [storage.row_to_dict(row) for row in conn.execute("select * from papers order by id")]
        chunk_texts = [row["text"] for row in conn.execute("select text from chunks order by id")]
    assert papers[0]["has_pdf"] is False
    assert papers[1]["has_pdf"] is True
    assert Path(papers[1]["pdf_path"]).is_file()
    assert any("DOCX evidence" in text for text in chunk_texts)
    assert any("PDF evidence" in text for text in chunk_texts)


def test_supported_but_malformed_structured_file_is_preserved_with_warning(tmp_path):
    source = tmp_path / "broken.csv"
    source.write_text("unknown,value\na,b\n", encoding="utf-8")

    result = imports.import_paths(tmp_path / "kb", [source])

    assert result["created"] == 0
    assert result["failed"] == 1
    assert result["warnings"][0]["code"] == "parse_error"
    assert Path(result["warnings"][0]["preserved_path"]).is_file()


def test_unsupported_file_is_preserved_and_reported_without_paper(tmp_path):
    source = tmp_path / "archive.xyz"
    source.write_bytes(b"opaque")
    kb_path = tmp_path / "kb"

    result = imports.import_paths(kb_path, [source])

    assert result["created"] == 0
    assert result["warnings"][0]["code"] == "unsupported_format"
    assert Path(result["warnings"][0]["preserved_path"]).read_bytes() == b"opaque"
    with storage.connect(kb_path) as conn:
        assert conn.execute("select count(*) from papers").fetchone()[0] == 0


def test_kb_import_compatibility_returns_paper_ids(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("Evidence note", encoding="utf-8")

    paper_ids = kb.import_files(tmp_path / "kb", [source])

    assert len(paper_ids) == 1
