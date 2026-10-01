"""디시 응답을 흉내 내는 가짜 세션 (실제 사이트에 요청하지 않음)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from dcrank.client import DcClient
from dcrank.robots import RobotsPolicy

ROBOTS = """
User-agent: ClaudeBot
Disallow: /
User-agent: *
Allow: /
Disallow: /kcaptcha/image_v3/
Disallow: /board/lists/?id=47
Disallow: /board/view/?id=47
Disallow: /board/comment_view/?id=47
Disallow: /board/view/?id=testgall&no=1989
"""


def writer_td(nick, uid="", ip="", fixed=True):
    icon = ""
    if uid:
        icon = f'<a class="writer_nikcon"><img src="https://nstatic.dcinside.com/dc/w/images/{"fix_nik" if fixed else "nik"}.gif"></a>'
    return (f'<td class="gall_writer ub-writer" data-nick="{nick}" data-uid="{uid}" data-ip="{ip}" data-loc="list">'
            f'<span class="nickname"><em>{nick}</em></span>{icon}</td>')


def row(no, date, nick, uid="", ip="", replies=0, fixed=True):
    reply = f'<a class="reply_numbox"><span class="reply_num">[{replies}]</span></a>' if replies else ""
    return (f'<tr class="ub-content us-post" data-no="{no}" data-type="icon_txt">'
            f'<td class="gall_num">{no}</td>'
            f'<td class="gall_tit ub-word"><a href="/board/view/?id=testgall&no={no}">글 {no}</a>{reply}</td>'
            f'{writer_td(nick, uid, ip, fixed)}'
            f'<td class="gall_date" title="{date:%Y-%m-%d %H:%M:%S}">{date:%H:%M}</td>'
            f'<td class="gall_count">1</td><td class="gall_recommend">0</td></tr>')


NOTICE = ('<tr class="ub-content" data-no="1" data-type="icon_notice"><td class="gall_num">공지</td>'
          '<td class="gall_tit"><a>공지</a></td>' + writer_td("매니저", "mgr") +
          '<td class="gall_date" title="2020-01-01 00:00:00">20.01.01</td></tr>')
AD = ('<tr class="ub-content"><td class="gall_num">AD</td><td class="gall_tit"><a>광고</a></td>'
      '<td class="gall_writer"></td><td class="gall_date"></td></tr>')


def list_html(rows, esno="ESNO123"):
    return (f'<html><body><input type="hidden" name="e_s_n_o" id="e_s_n_o" value="{esno}">'
            f'<table class="gall_list"><tbody>{NOTICE}{AD}{"".join(rows)}</tbody></table></body></html>')


class Resp:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, headers or {}


class FakeGallery:
    """1000개 글, 30분 간격, 최신 글이 2026-09-30 23:30. no % 3 == 0 인 글에 댓글 3개(댓글돌이 포함)."""

    def __init__(self, gall_id="testgall", kind="G", n=1000):
        self.id, self.kind = gall_id, kind
        self.latest = datetime(2026, 9, 30, 23, 30)
        self.posts = []
        for i in range(n):
            no = 1000 + n - i
            d = self.latest - timedelta(minutes=30 * i)
            if no % 2:
                w = dict(nick="고닉러", uid="gonick1")
            elif no % 5 == 0:
                w = dict(nick="반고닉", uid="half1", fixed=False)
            else:
                w = dict(nick="ㅇㅇ", ip=f"1.{no % 4}")
            self.posts.append((no, d, w, 3 if no % 3 == 0 else 0))
        self.calls = []

    def comments_for(self, no):
        _, d, _, cnt = next(p for p in self.posts if p[0] == no)
        if not cnt:
            return []
        t = lambda m: (d + timedelta(minutes=m)).strftime("%m.%d %H:%M:%S")
        return [
            {"no": str(no * 10 + 1), "user_id": "gonick1", "name": "고닉러", "ip": "", "reg_date": t(10), "depth": 0,
             "del_yn": "N", "nicktype": "20", "gallog_icon": '<img src="/fix_nik.gif">'},
            {"no": str(no * 10 + 2), "user_id": "", "name": "ㅇㅇ", "ip": "9.9", "reg_date": t(20), "depth": 1,
             "del_yn": "N", "nicktype": "00", "gallog_icon": ""},
            {"no": "0", "user_id": "", "name": "댓글돌이", "ip": "", "reg_date": t(1), "nicktype": "COMMENT_BOY"},
        ]


class FakeSession:
    def __init__(self, gallery: FakeGallery, minor_redirect=False):
        self.g = gallery
        self.minor_redirect = minor_redirect
        self.headers = {}
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url))
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path == "/board/comment/" and method == "POST":
            assert data["e_s_n_o"] == "ESNO123"
            assert data["_GALLTYPE_"] == self.g.kind
            comments = self.g.comments_for(int(data["no"]))
            # 실제처럼 total_cnt는 댓글돌이까지 센 값
            return Resp(json.dumps({"total_cnt": len(comments), "comments": comments}) if comments else "")
        if parts.path.endswith("/board/lists/"):
            prefix = {"G": "/board", "M": "/mgallery/board", "MI": "/mini/board"}[self.g.kind]
            if not parts.path.startswith(prefix):
                if self.minor_redirect:
                    return Resp('<script>location.replace("https://gall.dcinside.com/mgallery/board/lists?id=x")</script>')
                return Resp("<html>없는 갤</html>")
            page, per = int(q.get("page", 1)), int(q.get("list_num", 50))
            chunk = self.g.posts[(page - 1) * per: page * per]
            rows = [row(no, d, replies=c, **w) for no, d, w, c in chunk]
            return Resp(list_html(rows))
        return Resp("not found", 404)


def make_client(gallery: FakeGallery, workers: int = 1, **kw) -> DcClient:
    now = [0.0]

    def sleep(s):
        now[0] += s

    session = FakeSession(gallery, **kw)
    return DcClient(session=session, robots=RobotsPolicy(ROBOTS), delay=1.0, jitter=0, workers=workers,
                    sleep=sleep, clock=lambda: now[0])
