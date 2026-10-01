import pytest

from dcrank.client import BlockedError, DcClient, DisallowedError
from dcrank.robots import RobotsPolicy
from tests.fakes import ROBOTS, Resp


class Seq:
    def __init__(self, responses):
        self.responses, self.headers, self.n = list(responses), {}, 0

    def request(self, *a, **kw):
        self.n += 1
        return self.responses.pop(0)


def client(responses):
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    c = DcClient(session=Seq(responses), robots=RobotsPolicy(ROBOTS), delay=1.0, jitter=0,
                 sleep=sleep, clock=lambda: now[0])
    return c, now


def test_delay_enforced_between_requests():
    c, now = client([Resp("a"), Resp("b"), Resp("c")])
    for _ in range(3):
        c.get("https://gall.dcinside.com/board/lists/?id=x")
    assert now[0] >= 2.0


def test_403_stops_immediately():
    c, _ = client([Resp(status=403)])
    with pytest.raises(BlockedError):
        c.get("https://gall.dcinside.com/board/lists/?id=x")
    assert c.session.n == 1


def test_429_backs_off_then_gives_up():
    c, now = client([Resp(status=429, headers={"Retry-After": "30"})] * 4)
    with pytest.raises(BlockedError):
        c.get("https://gall.dcinside.com/board/lists/?id=x")
    assert now[0] >= 90


def test_robots_blocked_url_never_requested():
    c, _ = client([])
    with pytest.raises(DisallowedError):
        c.get("https://gall.dcinside.com/board/lists/?id=47&page=1")
    assert c.session.n == 0


def test_delay_minimum():
    with pytest.raises(ValueError):
        DcClient(delay=0.2, robots=RobotsPolicy(""))


def test_workers_share_rate_limit():
    now = [0.0]

    def sleep(s):
        now[0] += s

    c = DcClient(session=Seq([Resp("x")] * 8), robots=RobotsPolicy(ROBOTS), delay=1.0, jitter=0, workers=4,
                 sleep=sleep, clock=lambda: now[0])
    for _ in range(8):
        c.get("https://gall.dcinside.com/board/lists/?id=x")
    assert now[0] == pytest.approx(7 * 0.25)  # 4개 작업자 = 초당 4회


def test_429_slows_everyone_down():
    now = [0.0]

    def sleep(s):
        now[0] += s

    c = DcClient(session=Seq([Resp(status=429, headers={"Retry-After": "10"}), Resp("ok")]), robots=RobotsPolicy(ROBOTS),
                 delay=1.0, jitter=0, workers=8, sleep=sleep, clock=lambda: now[0])
    assert c.get("https://gall.dcinside.com/board/lists/?id=x").text == "ok"
    assert c.throttled == 1 and c._slowdown == 2.0 and now[0] >= 10


def test_workers_bounds():
    with pytest.raises(ValueError):
        DcClient(workers=0, robots=RobotsPolicy(""))
    with pytest.raises(ValueError):
        DcClient(workers=21, robots=RobotsPolicy(""))
