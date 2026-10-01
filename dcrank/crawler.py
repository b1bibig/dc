"""날짜 범위 안의 글·댓글 수집."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time
from time import monotonic as clock
from typing import Callable, Iterable

from .cache import CommentCache
from .client import BASE, CrawlError, DcClient, DisallowedError
from .parser import Comment, Gallery, ListPage, Post, parse_comments, parse_list_page

MODES = ("posts", "comments", "both")
LIST_NUM = 100  # 한 페이지당 글 수 (디시 목록 옵션 30/50/100 중 최대)
COMMENT_URL = f"{BASE}/board/comment/"
MAX_PAGE_PROBE = 1 << 20
PROBES = 4  # 시작 페이지 탐색 때 한 번에 찔러볼 페이지 수

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
    cached_posts: int = 0
    complete: bool = True


class Crawler:
    def __init__(
        self,
        client: DcClient,
        progress: Progress | None = None,
        max_pages: int = 300,
        cache: CommentCache | None = None,
    ):
        self.client = client
        self.workers = client.workers
        self.cache = cache
        self.result: CrawlResult | None = None  # 수집 중에도 지금까지 모인 결과를 볼 수 있게
        self.progress = progress or (lambda _: None)
        self.max_pages = max_pages
        self._pages: dict[int, ListPage] = {}
        self.gallery: Gallery | None = None
        self.esno = ""
        self._esno_lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None

    def _report(self, phase: str, message: str, done: int = 0, total: int = 0) -> None:
        self.progress(
            {"phase": phase, "message": message, "done": done, "total": total, "requests": self.client.request_count}
        )

    def _parallel(self, fn, items: Iterable):
        """items를 작업자 수만큼 동시에 처리해 (item, 결과)를 끝나는 순서대로 낸다.
        하나라도 예외가 나면 아직 시작 안 한 작업은 취소하고 그 예외를 올린다."""
        items = list(items)
        if self.workers == 1 or len(items) <= 1:
            for item in items:
                yield item, fn(item)
            return
        futures = {self._pool.submit(fn, item): item for item in items}
        try:
            for fut in as_completed(futures):
                yield futures[fut], fut.result()
        except BaseException:
            self.client.cancel_event.set()  # 403 등 치명적 오류면 돌고 있는 작업자도 바로 멈춘다
            raise
        finally:
            for fut in futures:
                fut.cancel()

    # ---- 목록 ----
    def _list_url(self, page: int) -> str:
        url = self.gallery.list_url(page)
        return url.replace(f"&page={page}", f"&list_num={LIST_NUM}&page={page}")

    def _fetch_page(self, page: int) -> ListPage:
        parsed = parse_list_page(self.client.get(self._list_url(page)).text)
        if parsed.esno:
            with self._esno_lock:
                self.esno = parsed.esno
        return parsed

    def _load(self, pages: Iterable[int]) -> None:
        missing = sorted({p for p in pages if p not in self._pages})
        for page, parsed in self._parallel(self._fetch_page, missing):
            self._pages[page] = parsed

    def _page(self, page: int) -> ListPage:
        self._load([page])
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
        """end_dt 이전 글이 처음 나오는 페이지를 찾는다.
        여러 페이지를 한꺼번에 찔러보는 지수 탐색 + k분 탐색. 헛요청이 늘지 않게 한 번에 최대 PROBES개."""

        def reaches(p: int) -> bool:  # p쪽 마지막 글이 end_dt 이하인가 (또는 빈 페이지)
            posts = self._pages[p].posts
            return not posts or posts[-1].date <= end_dt

        self._load([1])
        if reaches(1):
            return 1
        width = min(self.workers, PROBES)
        lo, hi, exp = 1, None, 1
        while hi is None:
            probes = [1 << e for e in range(exp, exp + width) if (1 << e) <= MAX_PAGE_PROBE]
            if not probes:
                raise CrawlError("시작 페이지를 찾지 못했습니다.")
            self._report("seek", f"시작 페이지 찾는 중… ({probes[0]}~{probes[-1]}쪽 확인)")
            self._load(probes)
            for p in probes:
                if reaches(p):
                    hi = p
                    break
                lo = p
            exp += width
        while hi - lo > 1:
            k = min(width, hi - lo - 1)
            probes = sorted({lo + (hi - lo) * j // (k + 1) for j in range(1, k + 1)} - {lo, hi})
            self._report("seek", f"시작 페이지 찾는 중… ({lo}~{hi}쪽 사이)")
            self._load(probes)
            for p in probes:
                if reaches(p):
                    hi = p
                    break
                lo = p
        return hi

    def _collect_posts(self, result: CrawlResult, start_dt: datetime, end_dt: datetime) -> None:
        page = self._find_first_page(end_dt)
        seen: set[int] = set()
        pages_read = 0
        last_no = None
        ahead = 1
        while True:
            # 다음 쪽들을 미리 받아두고 순서대로 처리. 범위가 짧으면 헛요청이 없게 1, 2, 4…쪽씩 늘린다
            self._load(range(page, page + max(1, min(ahead, self.max_pages - pages_read))))
            ahead = min(ahead * 2, self.workers)
            posts = self._pages.pop(page).posts
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
            page += 1
        self._pages.clear()

    # ---- 댓글 ----
    def _refresh_esno(self, stale: str) -> None:
        with self._esno_lock:
            if self.esno != stale:  # 다른 작업자가 이미 갱신함
                return
        self._fetch_page(1)

    def _fetch_comments(self, post: Post) -> list[Comment]:
        g = self.gallery
        out: dict[int, Comment] = {}
        raw_seen = 0  # 댓글돌이·삭제 댓글 포함 개수. total_cnt와는 이걸로 비교해야 헛요청이 없다
        retried = False
        page = 1
        while page <= 50:
            esno = self.esno
            resp = self.client.post(
                COMMENT_URL,
                data={
                    "id": g.id,
                    "no": post.no,
                    "cmt_id": g.id,
                    "cmt_no": post.no,
                    "focus_cno": "",
                    "focus_pno": "",
                    "e_s_n_o": esno,
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
                self._refresh_esno(esno)
                continue
            comments, total = parse_comments(data, post)
            raw = data.get("comments") or []
            raw_seen += len(raw)
            before = len(out)
            for c in comments:
                out[c.no] = c
            if not raw or raw_seen >= total or (len(out) == before and page > 1):
                break
            page += 1
        return list(out.values())

    def _comments_for(self, post: Post) -> tuple[str, list[Comment]]:
        """('skipped' | 'cached' | 'fetched', 댓글들)"""
        g = self.gallery
        if not (self.client.allowed(g.view_url(post.no)) and self.client.allowed(g.comment_view_url(post.no))):
            return "skipped", []
        comments = self.cache.get(g.id, post.no, post.comment_count) if self.cache else None
        if comments is not None:
            return "cached", comments
        try:
            comments = self._fetch_comments(post)
        except DisallowedError:
            return "skipped", []
        if self.cache:
            self.cache.put(g.id, post.no, post.comment_count, comments)
        return "fetched", comments

    def _collect_comments(self, result: CrawlResult, start_dt: datetime, end_dt: datetime) -> None:
        targets = [p for p in result.posts if p.comment_count > 0]
        counts = {"skipped": 0, "cached": 0, "fetched": 0}
        started = clock()
        for i, (_, (status, comments)) in enumerate(self._parallel(self._comments_for, targets), 1):
            counts[status] += 1
            result.comments.extend(c for c in comments if start_dt <= c.date <= end_dt)
            eta = ""
            if counts["fetched"]:
                per_post = (clock() - started) / counts["fetched"]
                eta = f" · 남은 시간 약 {max(1, round(per_post * (len(targets) - i) / 60))}분"
            self._report(
                "comments",
                f"댓글 {i}/{len(targets)}글 · 범위 내 댓글 {len(result.comments)}개 · 캐시 {counts['cached']}{eta}",
                i,
                len(targets),
            )
        result.cached_posts = counts["cached"]
        if counts["skipped"]:
            result.warnings.append(f"robots.txt가 막은 글 {counts['skipped']}개는 댓글을 수집하지 않았습니다.")

    # ---- 진입점 ----
    def run(self, gallery: Gallery, start: date, end: date, mode: str = "both") -> CrawlResult:
        if mode not in MODES:
            raise ValueError(f"mode는 {MODES} 중 하나여야 합니다.")
        if start > end:
            raise ValueError("시작일이 종료일보다 늦습니다.")
        start_dt = datetime.combine(start, time.min)
        end_dt = datetime.combine(end, time.max)
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="dcrank") as pool:
            self._pool = pool
            self._report("init", "robots.txt 확인 · 갤러리 확인 중…")
            self._resolve_gallery(gallery)
            result = self.result = CrawlResult(self.gallery, start, end, mode)
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
                if self.client.throttled:
                    result.warnings.append(
                        f"디시가 속도 제한(429/5xx)을 {self.client.throttled}번 걸어 자동으로 속도를 낮췄습니다. "
                        "동시 요청 수를 줄이는 게 좋습니다."
                    )
        self._report("done", f"완료 · 글 {len(result.posts)}개, 댓글 {len(result.comments)}개")
        return result
