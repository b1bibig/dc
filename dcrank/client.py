"""요청 간격·robots.txt·차단 응답을 지키는 HTTP 클라이언트.

원칙
- 요청은 한 번에 하나씩, 기본 1.5초(+무작위 0~0.5초) 간격.
- robots.txt가 막은 URL은 요청하지 않는다.
- 403은 즉시 중단, 429/5xx는 Retry-After(없으면 지수 백오프)만큼 쉬고 재시도,
  그래도 안 되면 중단한다. 프록시 교체·캡차 우회 같은 회피는 하지 않는다.
"""

from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlsplit

import requests

from . import __version__
from .robots import RobotsPolicy

BASE = "https://gall.dcinside.com"
DEFAULT_USER_AGENT = f"dcrank/{__version__} (personal gallery stats; low-rate)"


class CrawlError(RuntimeError):
    pass


class BlockedError(CrawlError):
    """사이트가 접근을 거부했거나 속도 제한이 풀리지 않음."""


class DisallowedError(CrawlError):
    """robots.txt가 막은 URL."""


class Cancelled(CrawlError):
    pass


class DcClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.5,
        jitter: float = 0.5,
        max_retries: int = 3,
        timeout: float = 15.0,
        session: requests.Session | None = None,
        robots: RobotsPolicy | None = None,
        cancel_event: threading.Event | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if delay < 1.0:
            raise ValueError("요청 간격(delay)은 1초 이상이어야 합니다.")
        self.user_agent = user_agent
        self.delay = delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "ko-KR,ko;q=0.9"})
        self._robots = robots
        self.cancel_event = cancel_event or threading.Event()
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.request_count = 0

    # ---- robots.txt ----
    @property
    def robots(self) -> RobotsPolicy:
        if self._robots is None:
            resp = self._send("GET", f"{BASE}/robots.txt", check_robots=False)
            self._robots = RobotsPolicy(resp.text if resp.status_code == 200 else "")
        return self._robots

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(self.user_agent, url)

    # ---- 요청 ----
    def get(self, url: str, **kwargs) -> requests.Response:
        return self._send("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._send("POST", url, **kwargs)

    def _wait(self, seconds: float) -> None:
        end = self._clock() + seconds
        while (left := end - self._clock()) > 0:
            if self.cancel_event.is_set():
                raise Cancelled("사용자가 중단했습니다.")
            self._sleep(min(left, 0.25))

    def _throttle(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("사용자가 중단했습니다.")
        target = self.delay + random.uniform(0, self.jitter)
        elapsed = self._clock() - (self._last_request or 0.0)
        if self._last_request is not None and elapsed < target:
            self._wait(target - elapsed)

    def _send(self, method: str, url: str, check_robots: bool = True, **kwargs) -> requests.Response:
        if check_robots and not self.allowed(url):
            raise DisallowedError(f"robots.txt가 막은 URL이라 건너뜁니다: {urlsplit(url).path}?{urlsplit(url).query}")
        kwargs.setdefault("timeout", self.timeout)
        backoff = 5.0
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self._last_request = self._clock()
            self.request_count += 1
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise CrawlError(f"네트워크 오류: {exc}") from exc
                self._wait(backoff)
                backoff *= 2
                continue
            if resp.status_code == 403:
                raise BlockedError("403 Forbidden: 사이트가 접근을 거부했습니다. 수집을 중단합니다.")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise BlockedError(f"HTTP {resp.status_code}가 계속되어 중단합니다. 나중에 다시 시도하세요.")
                retry_after = resp.headers.get("Retry-After", "")
                self._wait(float(retry_after) if retry_after.isdigit() else backoff)
                backoff *= 2
                continue
            return resp
        raise CrawlError("unreachable")
