from dcrank.robots import RobotsPolicy
from tests.fakes import ROBOTS

UA = "dcrank/0.1 (personal)"
B = "https://gall.dcinside.com"


def test_longest_match_beats_allow_all():
    r = RobotsPolicy(ROBOTS)
    assert r.can_fetch(UA, f"{B}/board/lists/?id=programming&list_num=100&page=1")
    assert not r.can_fetch(UA, f"{B}/board/lists/?id=47&list_num=100&page=3")
    assert not r.can_fetch(UA, f"{B}/board/view/?id=47&no=5")
    assert not r.can_fetch(UA, f"{B}/board/view/?id=testgall&no=1989")
    assert r.can_fetch(UA, f"{B}/board/view/?id=testgall&no=1990")
    assert not r.can_fetch(UA, f"{B}/kcaptcha/image_v3/x.png")


def test_specific_agent_group():
    r = RobotsPolicy(ROBOTS)
    assert not r.can_fetch("ClaudeBot/1.0", f"{B}/board/lists/?id=programming")


def test_wildcards_and_empty():
    r = RobotsPolicy("User-agent: *\nDisallow: /*.gif$\nDisallow:\n")
    assert not r.can_fetch(UA, f"{B}/a/b.gif")
    assert r.can_fetch(UA, f"{B}/a/b.gif?x=1")
    assert RobotsPolicy("").can_fetch(UA, f"{B}/anything")
