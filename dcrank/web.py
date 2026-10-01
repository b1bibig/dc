"""원클릭 웹 UI (로컬 전용, 127.0.0.1)."""

from __future__ import annotations

import threading
import uuid
import webbrowser
from datetime import date
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

from .client import CrawlError, DcClient
from .crawler import MODES, Crawler, CrawlResult
from .parser import parse_gallery
from .ranking import SORT_KEYS, build_ranking, ranking_csv, result_json

OUTPUT_DIR = Path("output")

app = Flask(__name__)


class Job:
    def __init__(self, params: dict):
        self.id = uuid.uuid4().hex[:8]
        self.params = params
        self.status = "running"  # running / done / error
        self.progress: dict = {"message": "준비 중…", "done": 0, "total": 0, "requests": 0}
        self.log: list[str] = []
        self.result: CrawlResult | None = None
        self.error = ""
        self.saved_path = ""
        self.cancel = threading.Event()

    def on_progress(self, p: dict) -> None:
        self.progress = p
        if not self.log or self.log[-1] != p["message"]:
            self.log.append(p["message"])
            del self.log[:-200]


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _running() -> Job | None:
    return next((j for j in _jobs.values() if j.status == "running"), None)


def _run(job: Job) -> None:
    p = job.params
    try:
        client = DcClient(delay=p["delay"], cancel_event=job.cancel)
        crawler = Crawler(client, progress=job.on_progress, max_pages=p["max_pages"])
        job.result = crawler.run(p["gallery"], p["start"], p["end"], p["mode"])
        OUTPUT_DIR.mkdir(exist_ok=True)
        r = job.result
        path = OUTPUT_DIR / f"{r.gallery.id}_{r.start}_{r.end}_{r.mode}.json"
        path.write_text(result_json(r), encoding="utf-8")
        job.saved_path = str(path)
        job.status = "done"
    except (CrawlError, ValueError) as exc:
        job.error, job.status = str(exc), "error"
    except Exception as exc:  # 예상 못한 오류도 화면에 띄운다
        job.error, job.status = f"{type(exc).__name__}: {exc}", "error"


@app.get("/")
def index():
    return render_template("index.html", today=date.today().isoformat())


@app.post("/api/jobs")
def create_job():
    body = request.get_json(force=True)
    try:
        params = {
            "gallery": parse_gallery(body.get("gallery", "")),
            "start": date.fromisoformat(body["start"]),
            "end": date.fromisoformat(body["end"]),
            "mode": body.get("mode", "both"),
            "delay": max(1.0, float(body.get("delay", 1.5))),
            "max_pages": max(1, min(int(body.get("max_pages", 300)), 5000)),
        }
        if params["mode"] not in MODES:
            raise ValueError("수집 대상이 올바르지 않습니다.")
        if params["start"] > params["end"]:
            raise ValueError("시작일이 종료일보다 늦습니다.")
    except (KeyError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    with _lock:
        if _running():
            return jsonify(error="이미 수집 중인 작업이 있습니다. 동시에 하나만 돌립니다."), 409
        job = Job(params)
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return jsonify(id=job.id)


def _job_or_404(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        abort(404)
    return job


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = _job_or_404(job_id)
    out = {"status": job.status, "progress": job.progress, "log": job.log[-30:], "error": job.error}
    if job.result is not None:
        r = job.result
        sort = request.args.get("sort", "total")
        rows = build_ranking(r, sort=sort if sort in SORT_KEYS else "total", merge_ip=request.args.get("merge_ip") == "1")
        out.update(
            gallery=f"{r.gallery.id} ({ {'G': '일반', 'M': '마이너', 'MI': '미니'}[r.gallery.kind] })",
            mode=r.mode,
            complete=r.complete,
            warnings=r.warnings,
            post_count=len(r.posts),
            comment_count=len(r.comments),
            requests=r.requests,
            saved_path=job.saved_path,
            rows=[
                {"rank": x.rank, "nick": x.nick, "ident": x.ident, "kind": x.kind,
                 "posts": x.posts, "comments": x.comments, "total": x.total}
                for x in rows
            ],
        )
    return jsonify(out)


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    _job_or_404(job_id).cancel.set()
    return jsonify(ok=True)


@app.get("/api/jobs/<job_id>/ranking.csv")
def download_csv(job_id: str):
    job = _job_or_404(job_id)
    if job.result is None:
        return jsonify(error="결과가 아직 없습니다."), 404
    sort = request.args.get("sort", "total")
    rows = build_ranking(job.result, sort=sort if sort in SORT_KEYS else "total",
                         merge_ip=request.args.get("merge_ip") == "1")
    r = job.result
    name = f"{r.gallery.id}_{r.start}_{r.end}_ranking.csv"
    return Response(ranking_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/jobs/<job_id>/raw.json")
def download_json(job_id: str):
    job = _job_or_404(job_id)
    if job.result is None:
        return jsonify(error="결과가 아직 없습니다."), 404
    r = job.result
    name = f"{r.gallery.id}_{r.start}_{r.end}_{r.mode}.json"
    return Response(result_json(r), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def serve(port: int = 5000, open_browser: bool = True) -> None:
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"갤창랭킹 UI: {url}  (종료: Ctrl+C)")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
