import http.client
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import kb  # noqa: E402
from kb_core import storage, web  # noqa: E402


class Client:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        parsed = json.loads(data) if "application/json" in content_type else None
        return SimpleNamespace(status=response.status, body=data, json=parsed, headers=dict(response.getheaders()))

    def get(self, path):
        return self.request("GET", path)

    def post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        return self.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

    def upload(self, path, filename, data):
        boundary = "----medical-kb-test-boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )


@pytest.fixture
def kb_path(tmp_path):
    path = tmp_path / "kb"
    storage.init_kb(path)
    config = path / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def web_server(kb_path):
    server = web.create_server(kb_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(*server.server_address)
    try:
        yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def seed_candidates(kb_path, count):
    return [
        kb.add_candidate(
            kb_path,
            {
                "pmid": str(1000 + index),
                "title": f"Candidate {index + 1}",
                "journal": "Clinical Medicine",
                "if_2025": 12.4,
                "jcr_quartiles": [{"quartile": "Q1"}],
                "study_type": "随机对照试验",
                "publication_year": 2026,
                "abstract": f"Evidence for candidate {index + 1}.",
            },
        )
        for index in range(count)
    ]


def test_read_endpoints_and_operational_page(web_server, kb_path):
    seed_candidates(kb_path, 1)
    kb.insert_paper(kb_path, {"title": "Library paper", "source": "test"})

    assert web_server.get("/api/overview").json["counts"]["pending_candidates"] == 1
    assert len(web_server.get("/api/candidates?status=pending").json["items"]) == 1
    assert len(web_server.get("/api/library").json["items"]) == 1
    assert web_server.get("/api/searches").json["items"] == []
    assert web_server.get("/api/tasks").json["items"] == []
    assert web_server.get("/api/audit").json["passed"] is True

    page = web_server.get("/")
    html = page.body.decode("utf-8")
    assert page.status == 200
    for label in ("候审区", "文献库", "本地上传", "定时任务", "任务状态"):
        assert label in html
    assert "data-view=\"candidates\"" in html
    assert "/api/candidates/approve" in html
    assert "https://" not in html


def test_candidate_approve_and_reject_endpoints(web_server, kb_path):
    candidate_ids = seed_candidates(kb_path, 2)

    approved = web_server.post_json(
        "/api/candidates/approve",
        {"ids": [candidate_ids[0]]},
    )
    preview = web_server.post_json(
        "/api/candidates/reject",
        {"ids": [candidate_ids[1]], "reason": "out of scope"},
    )
    rejected = web_server.post_json(
        "/api/candidates/reject",
        {
            "ids": [candidate_ids[1]],
            "reason": "out of scope",
            "confirm": preview.json["confirmation_token"],
        },
    )

    assert approved.status == 200
    assert approved.json["approved"] == [candidate_ids[0]]
    assert preview.status == 200
    assert preview.json["requires_confirmation"] is True
    assert rejected.status == 200
    assert rejected.json["rejected"] == [candidate_ids[1]]


def test_library_delete_previews_and_rejects_wrong_token(web_server, kb_path):
    paper_id = kb.insert_paper(kb_path, {"title": "Delete target", "source": "test"})

    preview = web_server.post_json("/api/library/delete", {"ids": [paper_id]})
    wrong = web_server.post_json(
        "/api/library/delete",
        {"ids": [paper_id], "confirm": "wrong"},
    )
    deleted = web_server.post_json(
        "/api/library/delete",
        {"ids": [paper_id], "confirm": preview.json["confirmation_token"]},
    )

    assert preview.json["requires_confirmation"] is True
    assert storage.get_paper(kb_path, paper_id) is None
    assert wrong.status == 409
    assert wrong.json["error"] == "safety_gate"
    assert deleted.json["deleted"] == [paper_id]


def test_multipart_upload_imports_file(web_server):
    response = web_server.upload(
        "/api/import",
        "paper.txt",
        b"Primary endpoint improved after treatment.",
    )

    assert response.status == 200
    assert response.json["created"] == 1
    assert response.json["paper_ids"]


def test_search_create_list_and_manual_run(web_server, kb_path, monkeypatch):
    from kb_core import scheduling

    monkeypatch.setattr(
        scheduling.pubmed,
        "search_to_candidates",
        lambda *args, **kwargs: [],
    )
    created = web_server.post_json(
        "/api/searches",
        {
            "name": "Weekly asthma",
            "query": "asthma[Title]",
            "frequency": "weekly",
            "run_first": False,
        },
    )
    search_id = created.json["saved_search_id"]
    run = web_server.post_json("/api/searches/run", {"ids": [search_id]})

    assert created.status == 200
    assert web_server.get("/api/searches").json["items"][0]["id"] == search_id
    assert run.status == 200
    assert run.json["runs"][0]["search_id"] == search_id


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("GET", "/api/unknown", None, 404),
        ("POST", "/api/candidates/approve", b"not-json", 400),
    ],
)
def test_structured_api_errors(web_server, method, path, body, expected):
    response = web_server.request(
        method,
        path,
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body or b""))},
    )

    assert response.status == expected
    assert "error" in response.json
