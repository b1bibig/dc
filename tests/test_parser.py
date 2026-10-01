from datetime import datetime

import pytest

from dcrank.parser import Post, Writer, parse_comment_date, parse_comments, parse_gallery, parse_list_page
from tests.fakes import list_html, row


def test_parse_gallery_inputs():
    assert parse_gallery("programming") == parse_gallery("programming")
    assert parse_gallery("programming").kind is None
    assert parse_gallery("https://gall.dcinside.com/board/lists/?id=programming").kind == "G"
    g = parse_gallery("https://gall.dcinside.com/mgallery/board/lists/?id=github&page=3")
    assert (g.id, g.kind) == ("github", "M")
    g = parse_gallery("gall.dcinside.com/mini/board/view/?id=abc&no=1")
    assert (g.id, g.kind) == ("abc", "MI")
    assert parse_gallery("https://m.dcinside.com/board/programming/123").id == "programming"
    with pytest.raises(ValueError):
        parse_gallery("not a gallery!")


def test_gallery_urls_put_id_first():
    g = parse_gallery("https://gall.dcinside.com/mgallery/board/lists/?id=x")
    assert g.list_url(2) == "https://gall.dcinside.com/mgallery/board/lists/?id=x&page=2"
    assert g.view_url(5) == "https://gall.dcinside.com/mgallery/board/view/?id=x&no=5"


def test_parse_list_skips_notice_and_ad():
    d = datetime(2026, 9, 30, 12, 0, 0)
    html = list_html([
        row(10, d, "고닉", uid="fix1", replies=4),
        row(9, d, "반고닉", uid="half", fixed=False),
        row(8, d, "ㅇㅇ", ip="1.2"),
    ])
    page = parse_list_page(html)
    assert page.esno == "ESNO123"
    assert [p.no for p in page.posts] == [10, 9, 8]
    assert [p.writer.kind for p in page.posts] == ["고닉", "반고닉", "유동"]
    assert page.posts[0].comment_count == 4
    assert page.posts[2].writer.key == "ip:ㅇㅇ(1.2)"
    assert page.posts[0].date == d


def test_redirect_detection():
    page = parse_list_page('<script>location.replace("https://gall.dcinside.com/mgallery/board/lists?id=x")</script>')
    assert page.posts == [] and page.redirect_kind == "M"


def test_comment_date_year_inference():
    post = datetime(2025, 12, 31, 23, 50)
    assert parse_comment_date("12.31 23:55:00", post) == datetime(2025, 12, 31, 23, 55)
    assert parse_comment_date("01.01 00:05:00", post) == datetime(2026, 1, 1, 0, 5)
    assert parse_comment_date("2024.03.01 10:00:00", post) == datetime(2024, 3, 1, 10, 0)
    assert parse_comment_date("garbage", post) is None


def test_parse_comments_filters_bot_and_deleted():
    post = Post(1, "t", datetime(2026, 9, 1, 10, 0), Writer("x"))
    data = {"total_cnt": "3", "comments": [
        {"no": "11", "user_id": "a", "name": "A", "ip": "", "reg_date": "09.01 10:05:00", "gallog_icon": "fix_nik"},
        {"no": "12", "user_id": "", "name": "ㅇㅇ", "ip": "1.1", "reg_date": "09.01 10:06:00", "del_yn": "Y"},
        {"no": "0", "name": "댓글돌이", "nicktype": "COMMENT_BOY", "reg_date": "09.01 10:00:00"},
        {"no": "13", "user_id": "", "name": "ㅇㅇ", "ip": "1.1", "reg_date": "09.02 00:00:00", "depth": 1},
    ]}
    comments, total = parse_comments(data, post)
    assert total == 3
    assert [c.no for c in comments] == [11, 13]
    assert comments[0].writer.kind == "고닉"
    assert comments[1].writer.kind == "유동" and comments[1].depth == 1
