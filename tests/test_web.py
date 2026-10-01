import time

from dcrank import web
from tests.fakes import FakeGallery, make_client


def test_one_click_flow(monkeypatch, tmp_path):
    g = FakeGallery()
    monkeypatch.setattr(web, "DcClient", lambda **kw: _with_cancel(g, kw))
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path)
    c = web.app.test_client()
    assert c.get("/").status_code == 200

    r = c.post("/api/jobs", json={"gallery": "testgall", "start": "2026-09-29", "end": "2026-09-30", "mode": "both"})
    job_id = r.get_json()["id"]
    for _ in range(100):
        d = c.get(f"/api/jobs/{job_id}?sort=posts").get_json()
        if d["status"] != "running":
            break
        time.sleep(0.05)
    assert d["status"] == "done", d
    assert d["post_count"] == 96 and d["rows"][0]["rank"] == 1
    assert c.get(f"/api/jobs/{job_id}/ranking.csv").data.decode("utf-8-sig").startswith("순위")
    assert list(tmp_path.glob("*.json"))


def _with_cancel(g, kw):
    client = make_client(g)
    client.cancel_event = kw["cancel_event"]
    return client


def test_bad_input_rejected():
    c = web.app.test_client()
    assert c.post("/api/jobs", json={"gallery": "x y", "start": "2026-09-01", "end": "2026-09-02"}).status_code == 400
    r = c.post("/api/jobs", json={"gallery": "x", "start": "2026-09-05", "end": "2026-09-02"})
    assert r.status_code == 400
