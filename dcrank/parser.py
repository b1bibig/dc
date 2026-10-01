"""갤러리 주소 해석, 목록 HTML / 댓글 JSON 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .client import BASE

# 갤러리 종류: G=일반(메이저), M=마이너, MI=미니
_PREFIX = {"G": "", "M": "/mgallery", "MI": "/mini"}
_GALL_ID = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Gallery:
    id: str
    kind: str | None = None  # None이면 자동 판별 전

    def list_url(self, page: int) -> str:
        # robots.txt 규칙이 `/board/lists/?id=xxx` 형태라 id를 맨 앞에 둔다.
        return f"{BASE}{_PREFIX[self.kind or 'G']}/board/lists/?id={self.id}&page={page}"

    def view_url(self, no: int) -> str:
        return f"{BASE}{_PREFIX[self.kind or 'G']}/board/view/?id={self.id}&no={no}"

    def comment_view_url(self, no: int) -> str:
        return f"{BASE}{_PREFIX[self.kind or 'G']}/board/comment_view/?id={self.id}&no={no}"


def parse_gallery(text: str) -> Gallery:
    """갤러리 ID(`programming`) 또는 주소를 받아 Gallery로 바꾼다."""
    text = text.strip()
    if "://" in text or text.startswith(("gall.", "m.dcinside")):
        parts = urlsplit(text if "://" in text else f"https://{text}")
        gall_id = (parse_qs(parts.query).get("id") or [""])[0]
        if not gall_id and parts.netloc.startswith("m."):
            # 모바일 주소: m.dcinside.com/board/<id>[/<no>]
            segs = [s for s in parts.path.split("/") if s]
            gall_id = segs[1] if len(segs) > 1 and segs[0] in ("board", "mini") else ""
            kind = "MI" if segs and segs[0] == "mini" else None
        else:
            path = parts.path
            kind = "M" if path.startswith("/mgallery/") else "MI" if path.startswith("/mini/") else "G"
        if not gall_id:
            raise ValueError(f"주소에서 갤러리 ID를 찾지 못했습니다: {text}")
        return Gallery(gall_id, kind)
    if not _GALL_ID.match(text):
        raise ValueError(f"갤러리 ID 형식이 아닙니다: {text}")
    return Gallery(text, None)


@dataclass(frozen=True)
class Writer:
    nick: str
    uid: str = ""
    ip: str = ""
    kind: str = "유동"  # 고닉 / 반고닉 / 유동

    @property
    def key(self) -> str:
        return f"uid:{self.uid}" if self.uid else f"ip:{self.nick}({self.ip})"

    @property
    def label(self) -> str:
        return f"{self.nick}({self.uid})" if self.uid else f"{self.nick}({self.ip})"


@dataclass
class Post:
    no: int
    title: str
    date: datetime
    writer: Writer
    comment_count: int = 0


@dataclass
class Comment:
    post_no: int
    no: int
    date: datetime
    writer: Writer
    depth: int = 0


@dataclass
class ListPage:
    posts: list[Post] = field(default_factory=list)
    esno: str = ""
    redirect_kind: str | None = None  # 일반 갤 주소로 마이너/미니 갤을 요청했을 때


def _writer_kind(uid: str, icon_html: str) -> str:
    if not uid:
        return "유동"
    return "고닉" if "fix_" in icon_html else "반고닉"


def _int(text: str | None, default: int = 0) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else default


def parse_list_page(html: str) -> ListPage:
    soup = BeautifulSoup(html, "html.parser")
    page = ListPage()
    esno = soup.select_one("input#e_s_n_o")
    if esno is not None:
        page.esno = esno.get("value", "")

    for row in soup.select("tr.ub-content"):
        num_td = row.select_one("td.gall_num")
        num_text = num_td.get_text(strip=True) if num_td else ""
        data_type = row.get("data-type", "")
        # 공지·설문·AD 행은 번호 칸이 숫자가 아니거나 data-type이 notice
        if not num_text.isdigit() or "notice" in data_type:
            continue
        date_td = row.select_one("td.gall_date")
        raw_date = (date_td.get("title") or "").strip() if date_td else ""
        try:
            date = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        w = row.select_one("td.gall_writer")
        if w is None:
            continue
        uid = (w.get("data-uid") or "").strip()
        writer = Writer(
            nick=(w.get("data-nick") or "").strip(),
            uid=uid,
            ip=(w.get("data-ip") or "").strip(),
            kind=_writer_kind(uid, str(w)),
        )
        link = row.select_one("td.gall_tit a")
        reply = row.select_one("td.gall_tit .reply_num")
        page.posts.append(
            Post(
                no=int(num_text),
                title=link.get_text(" ", strip=True) if link else "",
                date=date,
                writer=writer,
                comment_count=_int(reply.get_text() if reply else None),
            )
        )

    if not page.posts:
        if "/mgallery/board/lists" in html:
            page.redirect_kind = "M"
        elif "/mini/board/lists" in html:
            page.redirect_kind = "MI"
    return page


def parse_esno(html: str) -> str:
    tag = BeautifulSoup(html, "html.parser").select_one("input#e_s_n_o")
    return tag.get("value", "") if tag else ""


def parse_comment_date(raw: str, post_date: datetime) -> datetime | None:
    """댓글 시각. 올해 것은 `MM.DD HH:MM:SS`로 연도가 빠져 있어 글 작성 연도로 보정한다."""
    raw = (raw or "").strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        partial = datetime.strptime(f"{post_date.year}.{raw}", "%Y.%m.%d %H:%M:%S")
    except ValueError:
        return None
    # 12월 글에 1월 댓글이 달린 경우
    if partial < post_date.replace(second=0, microsecond=0) and partial.month < post_date.month:
        partial = partial.replace(year=partial.year + 1)
    return partial


def parse_comments(data: dict, post: Post) -> tuple[list[Comment], int]:
    """댓글 API 응답 → (댓글 목록, 전체 댓글 수). 댓글돌이·삭제 댓글은 뺀다."""
    total = _int(str(data.get("total_cnt", "")), 0)
    out: list[Comment] = []
    for c in data.get("comments") or []:
        if c.get("nicktype") == "COMMENT_BOY" or str(c.get("del_yn", "N")).upper() == "Y":
            continue
        name = (c.get("name") or "").strip()
        uid = (c.get("user_id") or "").strip()
        ip = (c.get("ip") or "").strip()
        if not uid and not ip and name == "댓글돌이":
            continue
        date = parse_comment_date(c.get("reg_date", ""), post.date)
        no = _int(str(c.get("no", "")), -1)
        if date is None or no < 0:
            continue
        out.append(
            Comment(
                post_no=post.no,
                no=no,
                date=date,
                writer=Writer(nick=name, uid=uid, ip=ip, kind=_writer_kind(uid, c.get("gallog_icon") or "")),
                depth=_int(str(c.get("depth", "0"))),
            )
        )
    return out, total
