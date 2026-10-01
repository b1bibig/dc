"""실행: `python -m dcrank` (웹 UI) 또는 `python -m dcrank crawl <갤> <시작일> <종료일>`."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .client import CrawlError, DcClient
from .crawler import MODES, Crawler
from .parser import parse_gallery
from .ranking import SORT_KEYS, build_ranking, ranking_csv, result_json


def _crawl(args: argparse.Namespace) -> int:
    def progress(p: dict) -> None:
        print(f"\r[요청 {p['requests']:>4}] {p['message']:<60}", end="", file=sys.stderr, flush=True)

    client = DcClient(delay=args.delay)
    crawler = Crawler(client, progress=progress, max_pages=args.max_pages)
    try:
        result = crawler.run(parse_gallery(args.gallery), args.start, args.end, args.mode)
    except (CrawlError, ValueError) as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    for w in result.warnings:
        print(f"※ {w}", file=sys.stderr)

    rows = build_ranking(result, sort=args.sort, merge_ip=args.merge_ip)
    print(f"\n{result.gallery.id} · {result.start} ~ {result.end} · 글 {len(result.posts)} / 댓글 {len(result.comments)}\n")
    print(f"{'순위':>4}  {'글':>5}  {'댓글':>5}  {'합계':>5}  작성자")
    for r in rows[: args.top]:
        ident = f" ({r.ident})" if r.ident else ""
        print(f"{r.rank:>4}  {r.posts:>5}  {r.comments:>5}  {r.total:>5}  {r.nick}{ident} [{r.kind}]")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{result.gallery.id}_{result.start}_{result.end}_{result.mode}"
    (out / f"{stem}.json").write_text(result_json(result), encoding="utf-8")
    (out / f"{stem}_ranking.csv").write_text(ranking_csv(rows), encoding="utf-8")
    print(f"\n저장: {out / stem}.json, {out / stem}_ranking.csv")
    return 0 if result.complete else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dcrank", description="디시 갤창랭킹")
    sub = ap.add_subparsers(dest="cmd")

    web = sub.add_parser("web", help="웹 UI 실행 (기본)")
    web.add_argument("--port", type=int, default=5000)
    web.add_argument("--no-browser", action="store_true")

    c = sub.add_parser("crawl", help="명령줄에서 바로 수집")
    c.add_argument("gallery", help="갤러리 ID 또는 주소")
    c.add_argument("start", type=date.fromisoformat, help="시작일 YYYY-MM-DD")
    c.add_argument("end", type=date.fromisoformat, help="종료일 YYYY-MM-DD")
    c.add_argument("--mode", choices=MODES, default="both")
    c.add_argument("--sort", choices=tuple(SORT_KEYS), default="total")
    c.add_argument("--merge-ip", action="store_true", help="유동을 하나로 합쳐 집계")
    c.add_argument("--delay", type=float, default=1.5, help="요청 간격(초, 최소 1)")
    c.add_argument("--max-pages", type=int, default=300, help="글 목록 페이지 상한")
    c.add_argument("--top", type=int, default=50)
    c.add_argument("--out", default="output")

    args = ap.parse_args(argv)
    if args.cmd == "crawl":
        return _crawl(args)
    from .web import serve

    serve(port=getattr(args, "port", 5000), open_browser=not getattr(args, "no_browser", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
