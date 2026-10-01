"""요청 간격·robots.txt·차단 응답을 지키는 HTTP 클라이언트.

원칙
- 작업자(workers) 하나당 요청 간격 최소 1초. 전체 속도는 workers / delay 회/초를 넘지 않는다.
- robots.txt가 막은 URL은 요청하지 않는다.
- 403은 즉시 중단. 429/5xx가 오면 모든 작업자가 Retry-After(없으면 지수 백오프)만큼 같이 쉬고
  전체 속도를 절반으로 낮춘다. 계속되면 중단한다. 프록시 교체·캡차 우회 같은 회피는 하지 않는다.
"""

from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from . import __version__
from .robots import RobotsPolicy

BASE = "https://gall.dcinside.com"
DEFAULT_USER_AGENT = f"dcrank/{__version__} (personal gallery stats)"
MAX_WORKERS = 20


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
        delay: float = 1.0,
        jitter: float = 0.3,
        workers: int = 1,
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
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"동시 요청 수는 1~{MAX_WORKERS} 사이여야 합니다.")
        self.user_agent = user_agent
        self.delay = delay
        self.jitter = jitter
        self.workers = workers
        self.max_retries = max_retries
        self.timeout = timeout
        if session is None:
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=max(workers, 10))
            session.mount("https://", adapter)
        self.session = session
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "ko-KR,ko;q=0.9"})
        self._robots = robots
        self.cancel_event = cancel_event or threading.Event()
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next_slot: float | None = None
        self._pause_until = 0.0
        self._slowdown = 1.0  # 429를 받을 때마다 2배 (최대 workers배 = 1개 작업자 속도)
        self.request_count = 0
        self.throttled = 0  # 429/5xx로 속도를 낮춘 횟수

    # ---- robots.txt ----
    @property
    def robots(self) -> RobotsPolicy:
        with self._lock:
            need = self._robots is None
        if need:
            resp = self._send("GET", f"{BASE}/robots.txt", check_robots=False)
            with self._lock:
                self._robots = self._robots or RobotsPolicy(resp.text if resp.status_code == 200 else "")
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
        """전체 요청을 delay/workers 간격의 슬롯에 하나씩 배정한다."""
        if self.cancel_event.is_set():
            raise Cancelled("사용자가 중단했습니다.")
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot or now, self._pause_until)
            interval = (self.delay + random.uniform(0, self.jitter)) / self.workers
            self._next_slot = slot + min(interval * self._slowdown, self.delay + self.jitter)
            self.request_count += 1
        if slot > now:
            self._wait(slot - now)

    def _back_off(self, seconds: float) -> None:
        with self._lock:
            self._pause_until = max(self._pause_until, self._clock() + seconds)
            self._slowdown = min(self._slowdown * 2, float(self.workers))
            self.throttled += 1
        self._wait(seconds)

    def _send(self, method: str, url: str, check_robots: bool = True, **kwargs) -> requests.Response:
        if check_robots and not self.allowed(url):
            raise DisallowedError(f"robots.txt가 막은 URL이라 건너뜁니다: {urlsplit(url).path}?{urlsplit(url).query}")
        kwargs.setdefault("timeout", self.timeout)
        backoff = 5.0
        for attempt in range(self.max_retries + 1):
            self._throttle()
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
                self._back_off(float(retry_after) if retry_after.isdigit() else backoff)
                backoff *= 2
                continue
            return resp
        raise CrawlError("unreachable")
