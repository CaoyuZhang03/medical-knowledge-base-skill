#!/usr/bin/env python3
"""Run deterministic gold-set checks for the medical knowledge-base skill."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import kb

ROOT = Path(__file__).resolve().parents[1]


def load_cases(task: str) -> list[dict]:
    return json.loads((ROOT / "evals" / "gold" / task / "cases.json").read_text(encoding="utf-8"))


def eval_query_generation_prompt() -> dict:
    prompt = (ROOT / "assets" / "prompts" / "search-query-generation.md").read_text(encoding="utf-8")
    cases = load_cases("query_generation")
    passed = 0
    failures = []
    for case in cases:
        ok = all(term in prompt for term in case["required_prompt_terms"])
        passed += int(ok)
        if not ok:
            failures.append(case["id"])
    return {"task": "query_generation", "passed": passed, "total": len(cases), "failures": failures}


def eval_jcr_matching() -> dict:
    catalog = kb.load_jcr_catalog(ROOT / "assets" / "data" / "JCR2025-UTF8.csv")
    cases = load_cases("jcr_matching")
    passed = 0
    failures = []
    for case in cases:
        match = kb.match_jcr(catalog, journal=case.get("journal"), issn=case.get("issn"), eissn=case.get("eissn"))
        ok = bool(match) and match["journal"] == case["expected_journal"] and match["quartiles"][0]["quartile"] == case["expected_quartile"]
        passed += int(ok)
        if not ok:
            failures.append(case["id"])
    return {"task": "jcr_matching", "passed": passed, "total": len(cases), "failures": failures}


def eval_study_type_classification() -> dict:
    cases = load_cases("study_type_classification")
    passed = 0
    failures = []
    for case in cases:
        got = kb.classify_study_type(case.get("publication_types", []), case.get("mesh_terms", []))
        ok = got == case["expected"]
        passed += int(ok)
        if not ok:
            failures.append({"id": case["id"], "got": got, "expected": case["expected"]})
    return {"task": "study_type_classification", "passed": passed, "total": len(cases), "failures": failures}


def eval_candidate_state() -> dict:
    cases = load_cases("candidate_state")
    passed = 0
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        kb_path = Path(tmp) / "kb"
        kb.init_kb(kb_path)
        for case in cases:
            cid = kb.add_candidate(kb_path, case["candidate"])
            if case["action"] == "reject":
                kb.reject_candidates(kb_path, [cid], reason="eval")
                ok = len(kb.list_papers(kb_path)) == 0
            else:
                kb.approve_candidates(kb_path, [cid])
                ok = any(p["pmid"] == case["candidate"]["pmid"] for p in kb.list_papers(kb_path))
            passed += int(ok)
            if not ok:
                failures.append(case["id"])
    return {"task": "candidate_state", "passed": passed, "total": len(cases), "failures": failures}


def eval_qa_retrieval() -> dict:
    cases = load_cases("qa_retrieval")
    passed = 0
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        kb_path = Path(tmp) / "kb"
        kb.init_kb(kb_path)
        paper_id = kb.insert_paper(kb_path, {"title": "Asthma biologics review", "pmid": "999", "source": "eval"})
        kb.add_chunk(kb_path, paper_id=paper_id, chunk_text="Dupilumab reduced exacerbations in severe asthma.", source_locator="abstract")
        for case in cases:
            packet = kb.retrieve_evidence(kb_path, question=case["question"], limit=case.get("limit", 3))
            ok = packet["evidence"] and case["expected_phrase"] in packet["evidence"][0]["text"]
            passed += int(ok)
            if not ok:
                failures.append(case["id"])
    return {"task": "qa_retrieval", "passed": passed, "total": len(cases), "failures": failures}


def main() -> int:
    results = [
        eval_query_generation_prompt(),
        eval_jcr_matching(),
        eval_study_type_classification(),
        eval_candidate_state(),
        eval_qa_retrieval(),
    ]
    failed = [result for result in results if result["passed"] != result["total"]]
    report = {"passed": not failed, "tasks": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
