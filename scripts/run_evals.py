#!/usr/bin/env python3
"""Run deterministic gold-set checks for the medical knowledge-base skill."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

import kb
from kb_core import scheduling

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
                candidate = next(item for item in kb.list_candidates(kb_path) if item["id"] == cid)
                ok = candidate["status"] == "rejected" and not any(
                    paper.get("pmid") == case["candidate"].get("pmid")
                    for paper in kb.list_papers(kb_path)
                )
            else:
                result = kb.approve_candidates_result(kb_path, [cid])
                candidate = next(item for item in kb.list_candidates(kb_path) if item["id"] == cid)
                ok = (
                    candidate["status"] == "approved"
                    and bool(result["approved"])
                    and any(
                        paper.get("pmid") == case["candidate"].get("pmid")
                        for paper in kb.list_papers(kb_path)
                    )
                )
            passed += int(ok)
            if not ok:
                failures.append(case["id"])
    return {"task": "candidate_state", "passed": passed, "total": len(cases), "failures": failures}


def eval_scheduling() -> dict:
    cases = load_cases("scheduling")
    passed = 0
    failures = []
    baseline = dt.datetime(2026, 7, 10, 8, 30, tzinfo=dt.timezone.utc)
    for case in cases:
        got = scheduling.next_run_at(baseline, case["frequency"])
        expected = baseline + dt.timedelta(days=case["expected_days"])
        ok = got == expected
        passed += int(ok)
        if not ok:
            failures.append({"id": case["id"], "got": got.isoformat(), "expected": expected.isoformat()})
    return {"task": "scheduling", "passed": passed, "total": len(cases), "failures": failures}


def eval_import() -> dict:
    cases = load_cases("import")
    passed = 0
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        kb_path = base / "kb"
        kb.init_kb(kb_path)
        for case in cases:
            source = base / case["filename"]
            source.write_text(case["content"], encoding="utf-8")
            result = kb.import_result(kb_path, [source])
            packet = kb.retrieve_evidence(kb_path, question=case["question"], limit=3)
            evidence_text = "\n".join(item["text"] for item in packet["evidence"])
            ok = result["created"] == case["expected_created"] and case["expected_phrase"] in evidence_text
            passed += int(ok)
            if not ok:
                failures.append(
                    {
                        "id": case["id"],
                        "created": result["created"],
                        "evidence_count": len(packet["evidence"]),
                    }
                )
    return {"task": "import", "passed": passed, "total": len(cases), "failures": failures}


def eval_qa_retrieval() -> dict:
    cases = load_cases("qa_retrieval")
    passed = 0
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        kb_path = Path(tmp) / "kb"
        kb.init_kb(kb_path)
        paper_id = kb.insert_paper(kb_path, {"title": "Asthma biologics review", "pmid": "999", "source": "eval"})
        kb.add_chunk(
            kb_path,
            paper_id=paper_id,
            chunk_text=(
                "Dupilumab reduced exacerbations in severe asthma. "
                "度普利尤单抗降低严重哮喘急性发作。"
            ),
            source_locator="abstract",
        )
        for case in cases:
            packet = kb.retrieve_evidence(kb_path, question=case["question"], limit=case.get("limit", 3))
            evidence_text = "\n".join(item["text"] for item in packet["evidence"])
            ok = case["expected_phrase"] in evidence_text
            passed += int(ok)
            if not ok:
                failures.append(case["id"])
    return {"task": "qa_retrieval", "passed": passed, "total": len(cases), "failures": failures}


def _route_fixture(prompt: str) -> tuple[bool, str | None, bool]:
    text = prompt.lower()
    durable = "知识库" in text or "候审区" in text or "文献库" in text
    if not durable:
        return False, None, False
    if "审计" in text or "一致性" in text:
        return True, "quality-audit", False
    if "候审" in text and any(word in text for word in ("批准", "拒绝", "同意")):
        return True, "candidate-review", True
    if "定时" in text or "每周" in text or "更新任务" in text:
        return True, "scheduled-updates", True
    if "pdf" in text and any(word in text for word in ("获取", "全文", "缺少")):
        return True, "pdf-acquisition", True
    if any(word in text for word in ("删除", "重建", "文献库页面")):
        return True, "library-management", True
    if any(word in text for word in ("回答", "问答")):
        return True, "knowledge-qa", False
    if "pubmed" in text or "检索" in text:
        return True, "pubmed-discovery", True
    if any(word in text for word in ("导入", "上传")):
        return True, "import-local-files", True
    if any(word in text for word in ("创建", "初始化", "新建")):
        return True, "init-kb", False
    return True, None, False


def eval_routing() -> dict:
    cases = load_cases("routing")
    passed = 0
    failures = []
    registry = (ROOT / "references" / "mode-registry.md").read_text(encoding="utf-8")
    for case in cases:
        triggered, mode, expect_ui = _route_fixture(case["prompt"])
        mode_registered = case["expected_mode"] is None or case["expected_mode"] in registry
        ok = (
            triggered == case["should_trigger"]
            and mode == case["expected_mode"]
            and expect_ui == case["expect_ui"]
            and mode_registered
        )
        passed += int(ok)
        if not ok:
            failures.append(
                {
                    "id": case["id"],
                    "got": {"trigger": triggered, "mode": mode, "expect_ui": expect_ui},
                }
            )
    return {"task": "routing", "passed": passed, "total": len(cases), "failures": failures}


def main() -> int:
    results = [
        eval_query_generation_prompt(),
        eval_jcr_matching(),
        eval_study_type_classification(),
        eval_candidate_state(),
        eval_scheduling(),
        eval_import(),
        eval_qa_retrieval(),
        eval_routing(),
    ]
    failed = [result for result in results if result["passed"] != result["total"]]
    report = {"passed": not failed, "tasks": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
