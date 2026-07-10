import concurrent.futures
import json
import sys
import threading
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import metadata, pubmed, storage  # noqa: E402


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE" Owner="NLM">
      <PMID Version="1">12345678</PMID>
      <Article PubModel="Print-Electronic">
        <Journal>
          <ISSN IssnType="Electronic">1474-547X</ISSN>
          <JournalIssue CitedMedium="Internet">
            <PubDate><Year>2026</Year><Month>Jan</Month></PubDate>
          </JournalIssue>
          <Title>The Lancet</Title>
        </Journal>
        <ArticleTitle>Randomized trial of <i>Example</i> therapy</ArticleTitle>
        <Pagination><StartPage>1</StartPage></Pagination>
        <ELocationID EIdType="doi" ValidYN="Y">10.1000/example</ELocationID>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text.</AbstractText>
          <AbstractText Label="RESULTS">Primary endpoint improved.</AbstractText>
        </Abstract>
        <AuthorList CompleteYN="Y">
          <Author ValidYN="Y"><LastName>Smith</LastName><ForeName>Jane</ForeName><Initials>J</Initials></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType UI="D016449">Randomized Controlled Trial</PublicationType>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
      <MedlineJournalInfo><ISSNLinking>0140-6736</ISSNLinking></MedlineJournalInfo>
      <MeshHeadingList>
        <MeshHeading><DescriptorName UI="D000001" MajorTopicYN="N">Clinical Trials as Topic</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/example</ArticleId>
        <ArticleId IdType="pmc">PMC1234567</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


VARIANT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>22222222</PMID>
      <Article>
        <Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue><Title>Variant Journal</Title></Journal>
        <ArticleTitle>Article with other abstract</ArticleTitle>
      </Article>
      <OtherAbstract Type="AAMC"><AbstractText>Other abstract evidence.</AbstractText></OtherAbstract>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedBookArticle>
    <BookDocument>
      <PMID>33333333</PMID>
      <ArticleTitle>Clinical handbook chapter</ArticleTitle>
      <Abstract><AbstractText>Book abstract evidence.</AbstractText></Abstract>
      <AuthorList><Author><LastName>Chen</LastName><ForeName>Li</ForeName></Author></AuthorList>
      <PublicationType UI="D016454">Review</PublicationType>
      <Book><BookTitle>Clinical Handbook</BookTitle><PubDate><Year>2024</Year></PubDate></Book>
    </BookDocument>
    <PubmedBookData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">33333333</ArticleId>
        <ArticleId IdType="doi">10.1000/book</ArticleId>
      </ArticleIdList>
    </PubmedBookData>
  </PubmedBookArticle>
</PubmedArticleSet>
"""


def sample_catalog():
    entry = {
        "journal": "LANCET",
        "journal_key": "lancet",
        "issn": "01406736",
        "eissn": "1474547X",
        "if_2025": 109.0,
        "quartiles": [
            {
                "category": "MEDICINE, GENERAL & INTERNAL",
                "quartile": "Q1",
                "rank": "1/336",
            }
        ],
    }
    return {
        "rows": [entry],
        "by_issn": {"01406736": entry, "1474547X": entry},
        "by_title": {"lancet": entry},
    }


def normalized_record(pmid="12345678"):
    record = pubmed.normalize_pubmed_xml(SAMPLE_XML)[0]
    record["pmid"] = pmid
    return record


def test_normalize_pubmed_xml_extracts_complete_metadata():
    record = pubmed.normalize_pubmed_xml(SAMPLE_XML)[0]

    assert record["pmid"] == "12345678"
    assert record["doi"] == "10.1000/example"
    assert record["pmcid"] == "PMC1234567"
    assert record["issn"] == "0140-6736"
    assert record["eissn"] == "1474-547X"
    assert record["title"] == "Randomized trial of Example therapy"
    assert record["journal"] == "The Lancet"
    assert record["publication_year"] == 2026
    assert record["authors"] == [{"name": "Jane Smith", "last_name": "Smith", "fore_name": "Jane"}]
    assert record["publication_types"] == ["Randomized Controlled Trial", "Journal Article"]
    assert record["mesh_terms"] == ["Clinical Trials as Topic"]
    assert record["abstract"] == "BACKGROUND: Background text.\nRESULTS: Primary endpoint improved."
    assert record["open_access_url"] == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert "<PubmedArticle>" in record["raw_pubmed_xml"]


def test_normalize_pubmed_xml_supports_other_abstract_and_book_articles():
    records = pubmed.normalize_pubmed_xml(VARIANT_XML)

    assert len(records) == 2
    assert records[0]["pmid"] == "22222222"
    assert records[0]["abstract"] == "Other abstract evidence."
    assert records[1]["pmid"] == "33333333"
    assert records[1]["doi"] == "10.1000/book"
    assert records[1]["title"] == "Clinical handbook chapter"
    assert records[1]["journal"] == "Clinical Handbook"
    assert records[1]["publication_year"] == 2024
    assert records[1]["authors"][0]["name"] == "Li Chen"
    assert records[1]["abstract"] == "Book abstract evidence."
    assert records[1]["publication_types"] == ["Review"]


def test_pubmed_result_is_enriched_and_filtered():
    record = metadata.enrich_record(normalized_record(), sample_catalog())

    assert record["if_2025"] == 109.0
    assert record["jcr_quartiles"][0]["quartile"] == "Q1"
    assert record["jcr_match_method"] == "issn"
    assert record["study_type"] == "随机对照试验"
    assert pubmed.paper_matches_filters(
        record,
        {
            "min_if": 100,
            "jcr_quartiles": ["Q1"],
            "study_types": ["随机对照试验"],
            "year_from": 2025,
            "year_to": 2026,
        },
    )
    assert not pubmed.paper_matches_filters(record, {"jcr_quartiles": ["Q2"]})
    assert not pubmed.paper_matches_filters(record, {"min_if": 110})


def test_censored_jcr_if_is_preserved_and_missing_if_never_passes_if_filter():
    parsed = metadata.parse_jcr_row({"Journal": "LOW IF", "IF(2025)": "<0.1"})

    assert parsed["if_2025"] == 0.1
    assert parsed["if_2025_display"] == "<0.1"
    assert parsed["if_2025_censored"] is True
    assert not pubmed.paper_matches_filters({"if_2025": None}, {"min_if": 0})
    assert not pubmed.paper_matches_filters(parsed, {"min_if": 0.1})
    assert not pubmed.paper_matches_filters(parsed, {"min_if": 0.05})
    assert pubmed.paper_matches_filters(parsed, {"min_if": 0})


def test_jcr_catalog_cache_is_immutable_and_invalidates_when_file_changes(tmp_path):
    jcr_path = tmp_path / "jcr.csv"
    jcr_path.write_text("Journal,IF(2025)\nTEST JOURNAL,1.0\n", encoding="utf-8")

    first = metadata.load_jcr_catalog(jcr_path)
    first["rows"][0]["if_2025"] = 999.0
    second = metadata.load_jcr_catalog(jcr_path)
    jcr_path.write_text("Journal,IF(2025)\nTEST JOURNAL,2.0\n", encoding="utf-8")
    third = metadata.load_jcr_catalog(jcr_path)

    assert second["rows"][0]["if_2025"] == 1.0
    assert third["rows"][0]["if_2025"] == 2.0


def test_scheduled_rerun_does_not_create_duplicate_candidate(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [normalized_record()])

    first = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())
    second = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["duplicates"] == 1
    with storage.connect(kb_path) as conn:
        assert conn.execute("select count(*) from candidate_papers").fetchone()[0] == 1


def test_decided_candidate_states_are_not_reintroduced(tmp_path, monkeypatch):
    for index, status in enumerate(("approved", "rejected"), start=1):
        kb_path = tmp_path / status
        record = normalized_record(pmid=f"9000000{index}")
        monkeypatch.setattr(pubmed, "fetch_records", lambda *args, _record=record, **kwargs: [_record])
        first = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())
        with storage.connect(kb_path) as conn:
            conn.execute(
                "update candidate_papers set status = ? where id = ?",
                (status, first["candidate_ids"][0]),
            )
            conn.commit()

        second = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

        assert second["created"] == 0
        assert second["duplicates"] == 1


def test_candidate_persists_search_provenance_and_enrichment(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [normalized_record()])

    result = pubmed.search_to_candidates(
        kb_path,
        query="example query",
        filters={"jcr_quartiles": ["Q1"]},
        search_id=7,
        jcr_catalog=sample_catalog(),
    )

    with storage.connect(kb_path) as conn:
        candidate = storage.row_to_dict(
            conn.execute("select * from candidate_papers where id = ?", (result["candidate_ids"][0],)).fetchone()
        )
    assert candidate["search_id"] == 7
    assert candidate["if_2025"] == 109.0
    assert candidate["jcr_quartiles"][0]["quartile"] == "Q1"
    assert candidate["dedupe_key"] == "pmid:12345678"
    assert candidate["raw"]["filter_decision"] == {
        "matched": True,
        "filters": {"jcr_quartiles": ["Q1"]},
    }
    assert candidate["raw"]["search_provenance"] == {
        "query": "example query",
        "search_id": 7,
    }


def test_saved_search_passes_filters_and_search_id_to_pubmed(tmp_path, monkeypatch):
    from kb_core import scheduling

    kb_path = tmp_path / "kb"
    search_id = scheduling.create_saved_search(
        kb_path,
        name="weekly",
        query="example",
        filters={"min_if": 10, "jcr_quartiles": ["Q1"]},
        run_first=False,
    )
    calls = []

    def fake_pubmed_search(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(scheduling.pubmed, "search_to_candidates", fake_pubmed_search)

    scheduling.run_saved_searches(kb_path, search_ids=[search_id])

    assert calls == [
        {
            "query": "example",
            "max_results": 20,
            "filters": {"min_if": 10, "jcr_quartiles": ["Q1"]},
            "search_id": search_id,
        }
    ]


def test_approved_paper_is_not_reintroduced_as_candidate(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    with storage.connect(kb_path) as conn:
        conn.execute(
            """
            insert into papers (pmid, title, created_at, updated_at)
            values ('12345678', 'Already approved', '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [normalized_record()])

    result = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

    assert result["created"] == 0
    assert result["duplicates"] == 1


def test_metadata_fallback_deduplicates_candidate_without_pmid_or_doi(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    record = normalized_record()
    record["pmid"] = None
    record["doi"] = None
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [record])

    first = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())
    second = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

    assert first["created"] == 1
    assert second["duplicates"] == 1
    with storage.connect(kb_path) as conn:
        assert conn.execute("select count(*) from candidate_papers").fetchone()[0] == 1


def test_legacy_candidate_without_dedupe_key_is_still_detected(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    record = normalized_record()
    record["pmid"] = None
    record["doi"] = None
    with storage.connect(kb_path) as conn:
        conn.execute(
            """
            insert into candidate_papers
                (title, journal, publication_year, status, created_at)
            values (?, ?, ?, 'pending', '2026-01-01')
            """,
            (record["title"], record["journal"], record["publication_year"]),
        )
        conn.commit()
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [record])

    result = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

    assert result["created"] == 0
    assert result["duplicates"] == 1


def test_filters_are_applied_before_candidate_insert(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [normalized_record()])

    result = pubmed.search_to_candidates(
        kb_path,
        query="example",
        filters={"min_if": 200},
        jcr_catalog=sample_catalog(),
    )

    assert result == {
        "query": "example",
        "total": 1,
        "created": 0,
        "duplicates": 0,
        "filtered": 1,
        "candidate_ids": [],
    }
    with storage.connect(kb_path) as conn:
        assert conn.execute("select count(*) from candidate_papers").fetchone()[0] == 0


def test_chinese_titles_produce_distinct_bibliographic_dedupe_keys():
    first = {"title": "儿童哮喘随机试验", "journal": "中华医学杂志", "publication_year": 2026}
    second = {"title": "儿童湿疹随机试验", "journal": "中华医学杂志", "publication_year": 2026}

    assert metadata.dedupe_key(first) != metadata.dedupe_key(second)


def test_identifier_record_matches_legacy_approved_paper_by_bibliography(tmp_path, monkeypatch):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    record = normalized_record()
    with storage.connect(kb_path) as conn:
        conn.execute(
            """
            insert into papers (title, journal, publication_year, created_at, updated_at)
            values (?, ?, ?, '2026-01-01', '2026-01-01')
            """,
            (record["title"], record["journal"], record["publication_year"]),
        )
        conn.commit()
    monkeypatch.setattr(pubmed, "fetch_records", lambda *args, **kwargs: [record])

    result = pubmed.search_to_candidates(kb_path, query="example", jcr_catalog=sample_catalog())

    assert result["created"] == 0
    assert result["duplicates"] == 1


def test_doi_normalization_matches_url_prefixes():
    bare = metadata.dedupe_key({"doi": "10.1000/Example"})
    url = metadata.dedupe_key({"doi": "https://doi.org/10.1000/example"})
    label = metadata.dedupe_key({"doi": "doi:10.1000/example"})

    assert bare == url == label == "doi:10.1000/example"


def test_candidate_insertion_is_atomic_across_concurrent_searches(tmp_path):
    kb_path = tmp_path / "kb"
    storage.init_kb(kb_path)
    record = metadata.enrich_record(normalized_record(), sample_catalog())
    barrier = threading.Barrier(2)

    def insert_once():
        barrier.wait()
        return pubmed.insert_candidate_if_new(
            kb_path,
            record,
            query="example",
            filters={},
            search_id=None,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: insert_once(), range(2)))

    assert sum(result is not None for result in results) == 1
    with storage.connect(kb_path) as conn:
        assert conn.execute("select count(*) from candidate_papers").fetchone()[0] == 1


def test_pubmed_cli_exposes_all_filter_options():
    args = kb.build_parser().parse_args(
        [
            "pubmed",
            "search",
            "kb",
            "--query",
            "example",
            "--min-if",
            "10.5",
            "--jcr-quartile",
            "Q1",
            "--jcr-quartile",
            "Q2",
            "--study-type",
            "随机对照试验",
            "--year-from",
            "2020",
            "--year-to",
            "2026",
        ]
    )

    assert args.min_if == 10.5
    assert args.jcr_quartiles == ["Q1", "Q2"]
    assert args.study_types == ["随机对照试验"]
    assert args.year_from == 2020
    assert args.year_to == 2026


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_pubmed_cli_rejects_non_finite_minimum_if(value):
    with pytest.raises(SystemExit):
        kb.build_parser().parse_args(
            ["pubmed", "search", "kb", "--query", "example", "--min-if", value]
        )


def test_pubmed_cli_prints_structured_search_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        kb,
        "pubmed_search_result",
        lambda *args, **kwargs: {
            "query": kwargs["query"],
            "total": 3,
            "created": 1,
            "duplicates": 1,
            "filtered": 1,
            "candidate_ids": [4],
        },
    )

    exit_code = kb.main(
        ["pubmed", "search", "kb", "--query", "example", "--jcr-quartile", "Q1"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "query": "example",
        "total": 3,
        "created": 1,
        "duplicates": 1,
        "filtered": 1,
        "candidate_ids": [4],
    }
