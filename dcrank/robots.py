"""robots.txt 파서 (RFC 9309).

표준 라이브러리 urllib.robotparser는 "먼저 나온 규칙"을 적용하는데,
디시 robots.txt는 `Allow: /` 뒤에 개별 `Disallow`를 나열하는 구조라서
그 방식이면 전부 허용으로 잘못 판정된다. 그래서 RFC 9309대로
"가장 길게 일치하는 규칙"을 적용하는 파서를 직접 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern = field(repr=False)


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)


def _compile(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    body = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.compile(body + ("$" if anchored else ""))


class RobotsPolicy:
    def __init__(self, text: str):
        self.groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    self.groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if current is None or not value:
                    continue
                current.rules.append(_Rule(key == "allow", value, _compile(value)))
            else:
                last_was_agent = False

    def _rules_for(self, user_agent: str) -> list[_Rule]:
        token = user_agent.split("/", 1)[0].strip().lower()
        matched = [g for g in self.groups if token in g.agents]
        if not matched:
            matched = [g for g in self.groups if "*" in g.agents]
        return [rule for g in matched for rule in g.rules]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: _Rule | None = None
        for rule in self._rules_for(user_agent):
            if not rule.regex.match(target):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return best is None or best.allow
