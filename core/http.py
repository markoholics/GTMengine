"""Shared HTTP client for every external API: retries with exponential backoff and jitter, honours Retry-After,
and a per host token bucket so no integration can exceed its rate limit by accident.
"""
import random, threading, time
import requests
from core.log import log

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

class RateLimiter:
    def __init__(self, per_second, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep

    def wait(self):
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)

class HttpError(Exception):
    pass

class Client:
    def __init__(self, name, per_second=5.0, max_attempts=5, base_delay=1.0, max_delay=60.0,
                 timeout=30, session=None, sleep=time.sleep, rng=None):
        self.name, self.max_attempts = name, max_attempts
        self.base_delay, self.max_delay, self.timeout = base_delay, max_delay, timeout
        self.session = session or requests.Session()
        self.limiter = RateLimiter(per_second)
        self._sleep = sleep
        self._rng = rng or random.Random()

    def _delay(self, attempt, retry_after):
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        return min(self.max_delay, self.base_delay * 2 ** attempt) * (0.5 + self._rng.random() / 2)

    def request(self, method, url, **kw):
        """Retries transport errors and RETRY_STATUS. Other 4xx raise immediately: retrying a bad request is waste.
        Non idempotent calls (POST that spends credits) must pass an idempotency key or set max_attempts=1."""
        kw.setdefault("timeout", self.timeout)
        last = None
        for attempt in range(self.max_attempts):
            self.limiter.wait()
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                r = None
            if r is not None:
                if r.status_code not in RETRY_STATUS:
                    if r.status_code >= 400:
                        raise HttpError(f"{self.name} {method} {url} -> {r.status_code}: {r.text[:300]}")
                    return r
                last = f"HTTP {r.status_code}"
            if attempt == self.max_attempts - 1:
                break
            ra = None
            if r is not None and r.headers.get("Retry-After", "").isdigit():
                ra = int(r.headers["Retry-After"])
            d = self._delay(attempt, ra)
            log("http_retry", level="warn", api=self.name, attempt=attempt + 1, delay=round(d, 2), reason=last)
            self._sleep(d)
        raise HttpError(f"{self.name} {method} {url} failed after {self.max_attempts} attempts: {last}")

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, **kw): return self.request("POST", url, **kw)
