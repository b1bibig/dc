"""글별 댓글 캐시 (SQLite).

목록에 보이는 댓글 수가 지난번과 같으면 댓글을 다시 받지 않는다.
"최근 3일"처럼 겹치는 기간을 반복해서 돌릴 때 요청이 크게 줄어든다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .parser import Comment, Writer


class CommentCache:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS comments ("
            " gallery TEXT, post_no INTEGER, comment_count INTEGER, data TEXT,"
            " PRIMARY KEY (gallery, post_no))"
        )

    def get(self, gallery: str, post_no: int, comment_count: int) -> list[Comment] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT comment_count, data FROM comments WHERE gallery = ? AND post_no = ?", (gallery, post_no)
            ).fetchone()
        if row is None or row[0] != comment_count:
            return None
        return [
            Comment(
                post_no=post_no,
                no=c["no"],
                date=datetime.fromisoformat(c["date"]),
                writer=Writer(c["nick"], c["uid"], c["ip"], c["kind"]),
                depth=c["depth"],
            )
            for c in json.loads(row[1])
        ]

    def put(self, gallery: str, post_no: int, comment_count: int, comments: list[Comment]) -> None:
        data = json.dumps(
            [
                {"no": c.no, "date": c.date.isoformat(), "nick": c.writer.nick, "uid": c.writer.uid,
                 "ip": c.writer.ip, "kind": c.writer.kind, "depth": c.depth}
                for c in comments
            ],
            ensure_ascii=False,
        )
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO comments VALUES (?, ?, ?, ?)", (gallery, post_no, comment_count, data)
            )
