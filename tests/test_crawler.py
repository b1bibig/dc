from datetime import date

from dcrank.crawler import Crawler
from dcrank.parser import Gallery
from dcrank.ranking import build_ranking, ranking_csv
from tests.fakes import FakeGallery, make_client


def posts_on(g, start, end):
    return [p for p in g.posts if start <= p[1].date() <= end]


def test_posts_in_range_with_page_seek():
    g = FakeGallery()
    client = make_client(g)
    # 9/20~9/21 범위 → 최신에서 약 9일(432개) 뒤, 앞 페이지는 이분 탐색으로 건너뜀
    res = Crawler(client).run(Gallery("testgall"), date(2026, 9, 20), date(2026, 9, 21), "posts")
    expected = posts_on(g, date(2026, 9, 20), date(2026, 9, 21))
    assert sorted(p.no for p in res.posts) == sorted(p[0] for p in expected)
    assert res.complete and res.comments == []
    list_calls = [u for m, u in client.session.calls if "/lists/" in u]
    assert all("list_num=100" in u and "?id=testgall&" in u for u in list_calls)
    assert len(list_calls) < 10  # 10쪽 전부를 읽지는 않음


def test_both_collects_comments_and_skips_robots_blocked_post():
    g = FakeGallery()
    client = make_client(g)
    res = Crawler(client).run(Gallery("testgall"), date(2026, 9, 10), date(2026, 9, 10), "both")
    in_range = posts_on(g, date(2026, 9, 10), date(2026, 9, 10))
    with_comments = [p for p in in_range if p[3]]
    # 글마다 댓글 2개(댓글돌이 제외). 9/10 23:xx 글의 댓글은 9/11로 넘어가 범위 밖일 수 있음
    assert 0 < len(res.comments) <= 2 * len(with_comments)
    assert all(c.date.date() == date(2026, 9, 10) for c in res.comments)
    assert not any(c.writer.nick == "댓글돌이" for c in res.comments)
    comment_calls = [u for m, u in client.session.calls if m == "POST"]
    assert len(comment_calls) == len(with_comments)

    rows = build_ranking(res)
    assert rows[0].nick == "고닉러" and rows[0].rank == 1
    assert rows[0].posts == sum(1 for p in in_range if p[2].get("uid") == "gonick1")
    assert "순위" in ranking_csv(rows)


def test_robots_blocked_post_comments_skipped():
    g = FakeGallery()
    client = make_client(g)
    blocked = next(p for p in g.posts if p[0] == 1989)  # fakes.ROBOTS가 view/?id=testgall&no=1989를 막음
    assert blocked[3] > 0
    day = blocked[1].date()
    res = Crawler(client).run(Gallery("testgall"), day, day, "comments")
    assert res.posts and res.comments
    assert 1989 not in {c.post_no for c in res.comments}
    assert any("robots.txt" in w for w in res.warnings)


def test_minor_gallery_autodetect():
    g = FakeGallery(kind="M")
    client = make_client(g, minor_redirect=True)
    res = Crawler(client).run(Gallery("testgall"), date(2026, 9, 30), date(2026, 9, 30), "both")
    assert res.gallery.kind == "M"
    assert len(res.posts) == 48


def test_max_pages_marks_incomplete():
    g = FakeGallery()
    res = Crawler(make_client(g), max_pages=1).run(Gallery("testgall"), date(2026, 9, 1), date(2026, 9, 30), "posts")
    assert not res.complete
    assert len(res.posts) == 100


def test_merge_ip_and_sort():
    g = FakeGallery()
    res = Crawler(make_client(g)).run(Gallery("testgall"), date(2026, 9, 29), date(2026, 9, 30), "both")
    merged = build_ranking(res, merge_ip=True)
    assert sum(1 for r in merged if r.kind == "유동") == 1
    by_comments = build_ranking(res, sort="comments")
    assert by_comments == sorted(by_comments, key=lambda r: -r.comments)


def test_range_older_than_gallery_stops_at_last_page():
    g = FakeGallery(n=250)
    res = Crawler(make_client(g)).run(Gallery("testgall"), date(2020, 1, 1), date(2026, 9, 30), "posts")
    assert len(res.posts) == 250 and res.complete
