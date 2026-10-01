"""수집 결과 → 갤창랭킹 집계 및 CSV/JSON 출력."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass

from .crawler import CrawlResult


@dataclass
class RankRow:
    rank: int
    key: str
    nick: str
    ident: str  # 식별코드(아이디) 또는 IP 앞자리
    kind: str
    posts: int
    comments: int

    @property
    def total(self) -> int:
        return self.posts + self.comments


SORT_KEYS = {"total": lambda r: r.total, "posts": lambda r: r.posts, "comments": lambda r: r.comments}


def build_ranking(result: CrawlResult, sort: str = "total", merge_ip: bool = False) -> list[RankRow]:
    """작성자별 글/댓글 수 집계.

    merge_ip=False(기본)면 유동은 `닉(IP앞자리)` 단위로 따로 센다.
    True면 유동을 전부 하나("유동 전체")로 합친다.
    """
    rows: dict[str, RankRow] = {}

    def bump(writer, field_name: str) -> None:
        key = writer.key
        if merge_ip and not writer.uid:
            key, nick, ident = "ip:*", "유동 전체", ""
        else:
            nick, ident = writer.nick, writer.uid or writer.ip
        row = rows.get(key)
        if row is None:
            row = rows[key] = RankRow(0, key, nick, ident, writer.kind, 0, 0)
        elif writer.uid and writer.kind == "고닉":
            row.kind, row.nick = "고닉", writer.nick  # 고닉 표기가 더 정확한 정보
        setattr(row, field_name, getattr(row, field_name) + 1)

    if result.mode in ("posts", "both"):
        for p in result.posts:
            bump(p.writer, "posts")
    if result.mode in ("comments", "both"):
        for c in result.comments:
            bump(c.writer, "comments")

    keyfn = SORT_KEYS[sort]
    ordered = sorted(rows.values(), key=lambda r: (-keyfn(r), -r.total, r.nick))
    prev, rank = None, 0
    for i, row in enumerate(ordered, 1):  # 공동 순위 처리
        if keyfn(row) != prev:
            rank, prev = i, keyfn(row)
        row.rank = rank
    return ordered


def ranking_csv(rows: list[RankRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["순위", "닉네임", "식별코드/IP", "구분", "글", "댓글", "합계"])
    for r in rows:
        w.writerow([r.rank, r.nick, r.ident, r.kind, r.posts, r.comments, r.total])
    return "﻿" + buf.getvalue()  # 엑셀 한글 깨짐 방지 BOM


def result_json(result: CrawlResult) -> str:
    def writer(w):
        return {"nick": w.nick, "uid": w.uid, "ip": w.ip, "kind": w.kind}

    return json.dumps(
        {
            "gallery": {"id": result.gallery.id, "kind": result.gallery.kind},
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "mode": result.mode,
            "complete": result.complete,
            "warnings": result.warnings,
            "requests": result.requests,
            "posts": [
                {"no": p.no, "title": p.title, "date": p.date.isoformat(sep=" "), "comment_count": p.comment_count,
                 "writer": writer(p.writer)}
                for p in result.posts
            ],
            "comments": [
                {"post_no": c.post_no, "no": c.no, "date": c.date.isoformat(sep=" "), "depth": c.depth,
                 "writer": writer(c.writer)}
                for c in result.comments
            ],
        },
        ensure_ascii=False,
        indent=1,
    )
