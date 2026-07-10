"""Local source preservation, bibliography parsing, extraction, and chunking."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from . import metadata, pubmed, storage


class ImportFormatError(ValueError):
    """Raised when a supported import file is malformed."""


HEADER_ALIASES = {
    "title": {"title", "article title", "document title", "ti"},
    "authors": {"authors", "author", "au"},
    "journal": {"journal", "journal title", "source", "publication"},
    "doi": {"doi", "digital object identifier"},
    "pmid": {"pmid", "pubmed id", "pubmedid"},
    "issn": {"issn", "print issn"},
    "eissn": {"eissn", "electronic issn", "online issn"},
    "publication_year": {"year", "publication year", "published year", "py"},
    "abstract": {"abstract", "summary", "ab"},
}


def _header_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", text)


def _field_for_header(value: Any) -> str | None:
    key = _header_key(value)
    for field, aliases in HEADER_ALIASES.items():
        if key in aliases:
            return field
    return None


def _authors(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"name": str(item)} for item in value]
    names = [name.strip() for name in re.split(r"\s*(?:;|\band\b)\s*", str(value or ""))]
    return [{"name": name} for name in names if name]


def _year(value: Any) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _record_from_mapping(raw: dict[str, Any]) -> dict[str, Any] | None:
    record: dict[str, Any] = {}
    for key, value in raw.items():
        field = _field_for_header(key)
        if field:
            record[field] = value
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "authors": _authors(record.get("authors")),
        "journal": str(record.get("journal") or "").strip() or None,
        "doi": metadata.normalize_doi(str(record.get("doi") or "")) or None,
        "pmid": str(record.get("pmid") or "").strip() or None,
        "issn": str(record.get("issn") or "").strip() or None,
        "eissn": str(record.get("eissn") or "").strip() or None,
        "publication_year": _year(record.get("publication_year")),
        "abstract": str(record.get("abstract") or "").strip() or None,
    }


def parse_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        if not rows.fieldnames:
            raise ImportFormatError("CSV file has no header row")
        return [record for raw in rows if (record := _record_from_mapping(raw))]


def _xlsx_cell_value(
    cell: ET.Element,
    namespace: dict[str, str],
    shared_strings: list[str],
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", namespace))
    raw = cell.findtext("m:v", default="", namespaces=namespace)
    if cell_type == "s" and raw:
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError):
            raise ImportFormatError("XLSX shared-string index is invalid") from None
    return raw


def _xlsx_column(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference or "")
    if not letters:
        raise ImportFormatError(f"XLSX cell reference is invalid: {reference}")
    index = 0
    for character in letters.group(0).upper():
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def parse_xlsx(path: str | Path) -> list[dict[str, Any]]:
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.findall(".//m:t", namespace))
                    for item in shared_root.findall("m:si", namespace)
                ]
            sheet_names = sorted(
                name
                for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            if not sheet_names:
                raise ImportFormatError("XLSX contains no worksheets")
            root = ET.fromstring(archive.read(sheet_names[0]))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ImportFormatError(f"Invalid XLSX file: {exc}") from exc

    rows: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", namespace):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", namespace):
            values[_xlsx_column(cell.attrib.get("r", ""))] = _xlsx_cell_value(
                cell, namespace, shared_strings
            )
        if values:
            rows.append([values.get(index, "") for index in range(max(values) + 1)])
    if not rows:
        return []
    headers = rows[0]
    records = []
    for values in rows[1:]:
        raw = {header: values[index] if index < len(values) else "" for index, header in enumerate(headers)}
        record = _record_from_mapping(raw)
        if record:
            records.append(record)
    return records


def parse_ris(path: str | Path) -> list[dict[str, Any]]:
    entries: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag = None
    for raw_line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        match = re.match(r"^([A-Z0-9]{2})  - ?(.*)$", raw_line)
        if match:
            tag, value = match.groups()
            if tag == "TY" and current:
                entries.append(current)
                current = {}
            if tag == "ER":
                if current:
                    entries.append(current)
                    current = {}
                last_tag = None
                continue
            current.setdefault(tag, []).append(value.strip())
            last_tag = tag
        elif raw_line.strip() and last_tag:
            current[last_tag][-1] += " " + raw_line.strip()
    if current:
        entries.append(current)

    records = []
    for entry in entries:
        raw = {
            "title": next(iter(entry.get("TI") or entry.get("T1") or []), ""),
            "authors": ";".join(entry.get("AU") or entry.get("A1") or []),
            "journal": next(iter(entry.get("JO") or entry.get("JF") or entry.get("T2") or []), ""),
            "doi": next(iter(entry.get("DO") or []), ""),
            "pmid": next(iter(entry.get("AN") or []), ""),
            "issn": next(iter(entry.get("SN") or []), ""),
            "year": next(iter(entry.get("PY") or entry.get("Y1") or []), ""),
            "abstract": " ".join(entry.get("AB") or []),
        }
        record = _record_from_mapping(raw)
        if record:
            records.append(record)
    return records


def _bibtex_entries(text: str) -> Iterable[str]:
    cursor = 0
    while True:
        start = text.find("@", cursor)
        if start < 0:
            return
        opening = min(
            (index for index in (text.find("{", start), text.find("(", start)) if index >= 0),
            default=-1,
        )
        if opening < 0:
            return
        open_char = text[opening]
        close_char = "}" if open_char == "{" else ")"
        depth = 1
        quoted = False
        index = opening + 1
        while index < len(text) and depth:
            character = text[index]
            if character == '"' and (index == 0 or text[index - 1] != "\\"):
                quoted = not quoted
            elif not quoted:
                if character == open_char:
                    depth += 1
                elif character == close_char:
                    depth -= 1
            index += 1
        if depth:
            raise ImportFormatError("BibTeX entry has unbalanced delimiters")
        yield text[opening + 1 : index - 1]
        cursor = index


def _bibtex_fields(entry: str) -> dict[str, str]:
    comma = entry.find(",")
    if comma < 0:
        return {}
    fields: dict[str, str] = {}
    cursor = comma + 1
    while cursor < len(entry):
        while cursor < len(entry) and (entry[cursor].isspace() or entry[cursor] == ","):
            cursor += 1
        key_match = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", entry[cursor:])
        if not key_match:
            break
        key = key_match.group(1).lower()
        cursor += key_match.end()
        if cursor >= len(entry):
            break
        if entry[cursor] in "{\"":
            opening = entry[cursor]
            closing = "}" if opening == "{" else '"'
            cursor += 1
            start = cursor
            depth = 1 if opening == "{" else 0
            while cursor < len(entry):
                character = entry[cursor]
                if opening == "{" and character == "{":
                    depth += 1
                elif character == closing:
                    if opening == "{":
                        depth -= 1
                        if depth == 0:
                            break
                    else:
                        break
                cursor += 1
            value = entry[start:cursor]
            cursor += 1
        else:
            start = cursor
            while cursor < len(entry) and entry[cursor] != ",":
                cursor += 1
            value = entry[start:cursor].strip()
        fields[key] = re.sub(r"\s+", " ", value).strip()
    return fields


def parse_bibtex(path: str | Path) -> list[dict[str, Any]]:
    records = []
    for entry in _bibtex_entries(Path(path).read_text(encoding="utf-8-sig", errors="replace")):
        fields = _bibtex_fields(entry)
        raw = {
            "title": fields.get("title"),
            "authors": fields.get("author"),
            "journal": fields.get("journal") or fields.get("booktitle"),
            "doi": fields.get("doi"),
            "pmid": fields.get("pmid"),
            "issn": fields.get("issn"),
            "year": fields.get("year"),
            "abstract": fields.get("abstract"),
        }
        record = _record_from_mapping(raw)
        if record:
            records.append(record)
    return records


def parse_pmid_list(path: str | Path) -> list[str]:
    values = [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not values or not all(re.fullmatch(r"\d{1,9}", value) for value in values):
        raise ImportFormatError("PMID lists must contain one numeric PMID per line")
    return values


def detect_import_kind(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".ris":
        return "ris"
    if suffix in {".bib", ".bibtex"}:
        return "bibtex"
    if suffix in {".pmid", ".pmids"}:
        return "pmid-list"
    if suffix == ".txt":
        try:
            parse_pmid_list(source)
            return "pmid-list"
        except (ImportFormatError, UnicodeError):
            return "text"
    if suffix in {".md", ".pdf", ".docx"}:
        return "document"
    return "unsupported"


def extract_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".txt", ".md"}:
        return source.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            import fitz  # type: ignore

            with fitz.open(source) as document:
                return "\n".join(page.get_text() for page in document)
        except (ImportError, RuntimeError, ValueError):
            return ""
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(source) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            return ""
        return "\n".join(
            node.text or "" for node in root.iter() if node.tag.endswith("}t")
        )
    return ""


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 120) -> list[str]:
    if max_chars <= 0 or overlap < 0 or max_chars <= overlap:
        raise ValueError("max_chars must be positive and exceed overlap")
    if not text.strip():
        return []
    step = max_chars - overlap
    return [text[start : start + max_chars] for start in range(0, len(text), step)]


def _preserve_source(kb_path: Path, source: Path) -> Path:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    destination = kb_path / "files" / "uploads" / f"{digest}-{source.name}"
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def _paper_exists(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    *,
    source_path: str | None = None,
) -> int | None:
    if source_path:
        row = conn.execute("select id from papers where source_path = ?", (source_path,)).fetchone()
        if row:
            return int(row["id"])
        return None
    pmid = str(record.get("pmid") or "").strip()
    if pmid:
        row = conn.execute("select id from papers where pmid = ?", (pmid,)).fetchone()
        if row:
            return int(row["id"])
    doi = metadata.normalize_doi(record.get("doi"))
    if doi:
        for row in conn.execute("select id, doi from papers where doi is not null"):
            if metadata.normalize_doi(row["doi"]) == doi:
                return int(row["id"])
    bibliography = metadata.bibliographic_key(record)
    for row in conn.execute("select id, title, journal, publication_year from papers"):
        if metadata.bibliographic_key(dict(row)) == bibliography:
            return int(row["id"])
    return None


def _insert_paper(
    kb_path: Path,
    record: dict[str, Any],
    *,
    source_path: str,
    source: str,
    dedupe_source: bool,
) -> tuple[int, bool]:
    now = storage.utc_now()
    with storage.connect(kb_path) as conn:
        conn.execute("begin immediate")
        existing = _paper_exists(
            conn,
            record,
            source_path=source_path if dedupe_source else None,
        )
        if existing is not None:
            conn.rollback()
            return existing, False
        cur = conn.execute(
            """
            insert into papers
                (pmid, doi, title, authors_json, journal, issn, eissn,
                 publication_year, study_type, if_2025, jcr_quartiles_json,
                 has_pdf, pdf_path, source, source_path, abstract,
                 created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if record.get("has_pdf") else 0,
                record.get("pdf_path"),
                source,
                source_path,
                record.get("abstract"),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid), True


def _add_chunks(
    kb_path: Path,
    paper_id: int,
    text: str,
    *,
    source_locator: str,
) -> None:
    from . import retrieval

    retrieval.add_chunks(
        kb_path,
        paper_id,
        text,
        source_locator=source_locator,
    )


def _structured_records(
    kind: str,
    path: Path,
    *,
    pubmed_fetch: Callable[[list[str]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if kind == "csv":
        records = parse_csv(path)
    elif kind == "xlsx":
        records = parse_xlsx(path)
    elif kind == "ris":
        records = parse_ris(path)
    elif kind == "bibtex":
        records = parse_bibtex(path)
    elif kind == "pmid-list":
        records = pubmed_fetch(parse_pmid_list(path))
    else:
        raise ImportFormatError(f"Unsupported structured kind: {kind}")
    if not records:
        raise ImportFormatError(f"{kind} contains no records with a title")
    return records


def import_paths(
    kb_path: str | Path,
    paths: list[str | Path],
    *,
    pubmed_fetch: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    jcr_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(kb_path)
    storage.init_kb(root)
    catalog = jcr_catalog or metadata.load_jcr_catalog()
    fetch_pmids = pubmed_fetch or pubmed.fetch_records_by_ids
    result: dict[str, Any] = {
        "created": 0,
        "duplicates": 0,
        "failed": 0,
        "paper_ids": [],
        "items": [],
        "warnings": [],
    }
    for value in paths:
        source = Path(value)
        if not source.is_file():
            raise FileNotFoundError(source)
        preserved = _preserve_source(root, source)
        kind = detect_import_kind(source)
        if kind == "unsupported":
            result["warnings"].append(
                {
                    "code": "unsupported_format",
                    "source_path": str(source),
                    "preserved_path": str(preserved),
                }
            )
            continue
        try:
            if kind in {"csv", "xlsx", "ris", "bibtex", "pmid-list"}:
                records = _structured_records(kind, preserved, pubmed_fetch=fetch_pmids)
                for raw_record in records:
                    record = metadata.enrich_record(raw_record, catalog)
                    paper_id, created = _insert_paper(
                        root,
                        record,
                        source_path=str(preserved),
                        source="local-structured",
                        dedupe_source=False,
                    )
                    result["paper_ids"].append(paper_id)
                    result["created" if created else "duplicates"] += 1
                    if created and record.get("abstract"):
                        _add_chunks(root, paper_id, record["abstract"], source_locator="abstract")
                    result["items"].append(
                        {"paper_id": paper_id, "status": "created" if created else "duplicate", "kind": kind}
                    )
            else:
                text = extract_text(preserved)
                record = {
                    "title": source.stem,
                    "authors": [],
                    "has_pdf": source.suffix.lower() == ".pdf",
                    "pdf_path": str(preserved) if source.suffix.lower() == ".pdf" else None,
                }
                paper_id, created = _insert_paper(
                    root,
                    record,
                    source_path=str(preserved),
                    source="local-upload",
                    dedupe_source=True,
                )
                result["paper_ids"].append(paper_id)
                result["created" if created else "duplicates"] += 1
                if created and text.strip():
                    text_path = root / "files" / "extracted-text" / f"{paper_id}.txt"
                    text_path.write_text(text, encoding="utf-8")
                    with storage.connect(root) as conn:
                        conn.execute(
                            "update papers set full_text_path = ?, updated_at = ? where id = ?",
                            (str(text_path), storage.utc_now(), paper_id),
                        )
                        conn.commit()
                    _add_chunks(root, paper_id, text, source_locator=str(text_path))
                result["items"].append(
                    {"paper_id": paper_id, "status": "created" if created else "duplicate", "kind": kind}
                )
        except (ImportFormatError, UnicodeError, ValueError) as exc:
            result["failed"] += 1
            result["warnings"].append(
                {
                    "code": "parse_error",
                    "source_path": str(source),
                    "preserved_path": str(preserved),
                    "message": str(exc),
                }
            )
    return result
