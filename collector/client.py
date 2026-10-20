"""HTTP-клієнт: пейсинг, 429/5xx backoff, повага до Retry-After і X-RateLimit-*, ротація токенів."""
import logging
import random
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("client")


class RateLimitStats:
    def __init__(self):
        self.rate_limited = 0
        self.errors = 0
        self.requests = 0


class MastodonClient:
    def __init__(self, cfg, sleep=time.sleep):
        self.cfg = cfg
        self.sleep = sleep
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "edu-incremental-collector/1.0 (homework project)"
        if cfg.proxy:
            self.session.proxies = {"http": cfg.proxy, "https": cfg.proxy}
        self.tokens = list(cfg.tokens) or [None]
        self._tok_idx = 0
        self.stats = RateLimitStats()

    def _rotate(self) -> None:
        if len(self.tokens) > 1:
            self._tok_idx = (self._tok_idx + 1) % len(self.tokens)
            log.info("rotated to token #%d", self._tok_idx)

    def _headers(self) -> dict:
        tok = self.tokens[self._tok_idx]
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    @staticmethod
    def _reset_wait(resp) -> float | None:
        """Секунди до скидання ліміту з X-RateLimit-Reset (ISO-час) або Retry-After."""
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except ValueError:
                pass
        return None

    def get(self, url: str, params: dict | None = None):
        """GET з ретраями. Повертає requests.Response або кидає RuntimeError після вичерпання спроб."""
        for attempt in range(1, self.cfg.max_retries + 1):
            self.sleep(self.cfg.delay)  # пейсинг перед КОЖНИМ запитом
            self.stats.requests += 1
            try:
                resp = self.session.get(url, params=params, headers=self._headers(), timeout=30)
            except requests.RequestException as e:
                self.stats.errors += 1
                wait = min(60, 2 ** attempt) + random.random()
                log.warning("network error %s (attempt %d), sleep %.1fs", e, attempt, wait)
                self.sleep(wait)
                continue

            h = resp.headers
            log.info("GET %s -> %s | limit=%s remaining=%s reset=%s retry-after=%s",
                     resp.url, resp.status_code, h.get("X-RateLimit-Limit"),
                     h.get("X-RateLimit-Remaining"), h.get("X-RateLimit-Reset"), h.get("Retry-After"))

            if resp.status_code == 429:
                self.stats.rate_limited += 1
                wait = self._reset_wait(resp)
                if wait is None:
                    wait = min(300, 2 ** attempt)
                wait = min(wait, 900) + random.random()
                self._rotate()
                log.warning("429 rate limited, sleeping %.1fs (attempt %d)", wait, attempt)
                self.sleep(wait)
                continue
            if resp.status_code >= 500:
                self.stats.errors += 1
                wait = min(60, 2 ** attempt) + random.random()
                log.warning("server error %s, sleeping %.1fs", resp.status_code, wait)
                self.sleep(wait)
                continue
            if resp.status_code in (401, 403):
                self._rotate()
                raise RuntimeError(f"auth/forbidden {resp.status_code} for {resp.url}")
            resp.raise_for_status()

            # Проактивно: якщо ліміт майже вичерпано — чекаємо до скидання, а не б'ємось у 429.
            try:
                remaining = int(h.get("X-RateLimit-Remaining", "999"))
            except ValueError:
                remaining = 999
            if remaining <= 1:
                wait = self._reset_wait(resp) or 30
                log.info("limit nearly exhausted, pre-emptive sleep %.1fs", min(wait, 900))
                self.sleep(min(wait, 900))
            return resp
        raise RuntimeError(f"gave up after {self.cfg.max_retries} attempts: {url}")
