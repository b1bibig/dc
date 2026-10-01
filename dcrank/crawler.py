"""날짜 범위 안의 글·댓글 수집."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Callable

from .client import BASE, CrawlError, DcClient, DisallowedError
from .parser import Comment, Gallery, ListPage, Post, parse_comments, parse_list_page

MODES = ("posts", "comments", "both")
LIST_NUM = 100  # 한 페이지당 글 수 (디시 목록 옵션 30/50/100 중 최대)
COMMENT_URL = f"{BASE}/board/comment/"

Progress = Callable[[dict], None]


@dataclass
class CrawlResult:
    gallery: Gallery
    start: date
    end: date
    mode: str
    posts: list[Post] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requests: int = 0
    complete: bool = True


class Crawler:
    def __init__(self, client: DcClient, progress: Progress | None = None, max_pages: int = 300):
        self.client = client
        self.progress = progress or (lambda _: None)
        self.max_pages = max_pages
        self._pages: dict[int, ListPage] = {}
        self.gallery: Gallery | None = None
        self.esno = ""

    def _report(self, phase: str, message: str, done: int = 0, total: int = 0) -> None:
        self.progress(
            {"phase": phase, "message": message, "done": done, "total": total, "requests": self.client.request_count}
        )

    # ---- 목록 ----
    def _list_url(self, page: int) -> str:
        url = self.gallery.list_url(page)
        return url.replace(f"&page={page}", f"&list_num={LIST_NUM}&page={page}")

    def _page(self, page: int) -> ListPage:
        if page not in self._pages:
            resp = self.client.get(self._list_url(page))
            parsed = parse_list_page(resp.text)
            if parsed.esno:
                self.esno = parsed.esno
            self._pages[page] = parsed
        return self._pages[page]

    def _resolve_gallery(self, gallery: Gallery) -> None:
        self.gallery = gallery if gallery.kind else Gallery(gallery.id, "G")
        first = self._page(1)
        if gallery.kind is None and first.redirect_kind:
            self.gallery = Gallery(gallery.id, first.redirect_kind)
            self._pages.clear()
            first = self._page(1)
        if not first.posts:
            raise CrawlError(f"'{gallery.id}' 갤러리 목록을 읽지 못했습니다. 갤러리 ID나 주소를 확인하세요.")

    def _find_first_page(self, end_dt: datetime) -> int:
        """end_dt 이전 글이 처음 나오는 페이지를 지수+이분 탐색으로 찾는다."""

        def reaches(p: int) -> bool:  # p쪽 마지막 글이 end_dt 이하인가 (또는 빈 페이지)
            posts = self._page(p).posts
            return not posts or posts[-1].date <= end_dt

        if reaches(1):
            return 1
        lo, hi = 1, 2
        while not reaches(hi):
            self._report("seek", f"시작 페이지 찾는 중… ({hi}쪽 확인)")
            lo, hi = hi, hi * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            self._report("seek", f"시작 페이지 찾는 중… ({mid}쪽 확인)")
            if reaches(mid):
                hi = mid
            else:
                lo = mid
        return hi

    def _collect_posts(self, result: CrawlResult, start_dt: datetime, end_dt: datetime) -> None:
        page = self._find_first_page(end_dt)
        seen: set[int] = set()
        pages_read = 0
        last_no = None
        while True:
            posts = self._page(page).posts
            # 마지막 쪽을 넘기면 빈 목록이나 같은 쪽이 다시 올 수 있다
            if not posts or posts[-1].no == last_no:
                break
            last_no = posts[-1].no
            for p in posts:
                if start_dt <= p.date <= end_dt and p.no not in seen:
                    seen.add(p.no)
                    result.posts.append(p)
            pages_read += 1
            self._report("posts", f"글 목록 {page}쪽 · 범위 내 글 {len(result.posts)}개", pages_read, 0)
            if posts[-1].date < start_dt:
                break
            if pages_read >= self.max_pages:
                result.complete = False
                result.warnings.append(
                    f"페이지 상한({self.max_pages}쪽)에 걸려 {posts[-1].date:%Y-%m-%d %H:%M} 이후 글까지만 수집했습니다."
                )
                break
            self._pages.pop(page, None)
            page += 1

    # ---- 댓글 ----
    def _fetch_comments(self, post: Post) -> list[Comment]:
        g = self.gallery
        out: dict[int, Comment] = {}
        retried = False
        page = 1
        while page <= 50:
            resp = self.client.post(
                COMMENT_URL,
                data={
                    "id": g.id,
                    "no": post.no,
                    "cmt_id": g.id,
                    "cmt_no": post.no,
                    "focus_cno": "",
                    "focus_pno": "",
                    "e_s_n_o": self.esno,
                    "comment_page": page,
                    "sort": "",
                    "prevCnt": "",
                    "board_type": "",
                    "_GALLTYPE_": g.kind,
                },
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": g.view_url(post.no)},
            )
            text = resp.text.strip()
            if not text:
                break
            try:
                data = json.loads(text)
            except ValueError:
                if retried:
                    raise CrawlError(f"{post.no}번 글 댓글 응답을 해석하지 못했습니다.")
                # e_s_n_o 토큰이 만료됐을 수 있어 목록을 새로 읽고 한 번만 재시도
                retried = True
                self._pages.pop(1, None)
                self._page(1)
                continue
            comments, total = parse_comments(data, post)
            before = len(out)
            for c in comments:
                out[c.no] = c
            if len(out) == before or len(out) >= total or not data.get("comments"):
                break
            page += 1
        return list(out.values())

    def _collect_comments(self, result: CrawlResult, start_dt: datetime, end_dt: datetime) -> None:
        targets = [p for p in result.posts if p.comment_count > 0]
        skipped = 0
        for i, post in enumerate(targets, 1):
            g = self.gallery
            if not (self.client.allowed(g.view_url(post.no)) and self.client.allowed(g.comment_view_url(post.no))):
                skipped += 1
                continue
            try:
                comments = self._fetch_comments(post)
            except DisallowedError:
                skipped += 1
                continue
            result.comments.extend(c for c in comments if start_dt <= c.date <= end_dt)
            self._report("comments", f"댓글 수집 {i}/{len(targets)} · 범위 내 댓글 {len(result.comments)}개", i, len(targets))
        if skipped:
            result.warnings.append(f"robots.txt가 막은 글 {skipped}개는 댓글을 수집하지 않았습니다.")

    # ---- 진입점 ----
    def run(self, gallery: Gallery, start: date, end: date, mode: str = "both") -> CrawlResult:
        if mode not in MODES:
            raise ValueError(f"mode는 {MODES} 중 하나여야 합니다.")
        if start > end:
            raise ValueError("시작일이 종료일보다 늦습니다.")
        start_dt = datetime.combine(start, time.min)
        end_dt = datetime.combine(end, time.max)
        self._report("init", "robots.txt 확인 · 갤러리 확인 중…")
        self._resolve_gallery(gallery)
        result = CrawlResult(self.gallery, start, end, mode)
        try:
            self._collect_posts(result, start_dt, end_dt)
            if mode in ("comments", "both"):
                if not self.esno:
                    result.warnings.append("댓글 토큰(e_s_n_o)을 찾지 못해 댓글은 수집하지 못했을 수 있습니다.")
                self._collect_comments(result, start_dt, end_dt)
                result.warnings.append(
                    "댓글은 기간 내 작성된 글에 달린 것만 셉니다 (기간 이전 글에 기간 중 달린 댓글은 제외)."
                )
        except CrawlError as exc:  # 중단·차단 시 그때까지 모은 것은 살린다
            result.complete = False
            result.warnings.append(f"수집이 중간에 멈췄습니다: {exc}")
        finally:
            result.requests = self.client.request_count
        self._report("done", f"완료 · 글 {len(result.posts)}개, 댓글 {len(result.comments)}개")
        return result
