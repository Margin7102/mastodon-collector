"""Ротація токенів при 429/401, заголовок Authorization і дашборд."""
import collector.client as client_mod
from collector.client import MastodonClient
from collector.config import Config
from collector.dashboard import build
from collector.db import Post, Store


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers, self.url = status, headers or {}, "http://x/y"
        self.links = {}

    def json(self):
        return []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def make_client(tokens, responses):
    cfg = Config(instance="http://x", tokens=tokens, hashtags=[], pages_per_run=1, page_limit=1,
                 delay=0, max_retries=5, db_path=":memory:", proxy=None)
    slept = []
    c = MastodonClient(cfg, sleep=slept.append)
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append(headers.get("Authorization"))
        return responses.pop(0)

    c.session.get = fake_get
    return c, seen, slept


def test_rotates_token_on_429_and_respects_retry_after():
    c, seen, slept = make_client(["aaa", "bbb"], [FakeResp(429, {"Retry-After": "7"}), FakeResp(200)])
    c.get("http://x/y")
    assert seen == ["Bearer aaa", "Bearer bbb"]      # після 429 перейшли на другий токен
    assert any(7 <= s < 9 for s in slept)            # відчекали Retry-After (+jitter)
    assert c.stats.rate_limited == 1


def test_rotation_wraps_around():
    c, seen, _ = make_client(["a", "b"], [FakeResp(429), FakeResp(429), FakeResp(200)])
    c.get("http://x/y")
    assert seen == ["Bearer a", "Bearer b", "Bearer a"]


def test_no_tokens_means_no_auth_header_and_no_crash_on_429():
    c, seen, _ = make_client([], [FakeResp(429, {"Retry-After": "1"}), FakeResp(200)])
    c.get("http://x/y")
    assert seen == [None, None]


def test_rotates_on_401_then_raises():
    c, seen, _ = make_client(["a", "b"], [FakeResp(401)])
    try:
        c.get("http://x/y")
        assert False, "очікувався RuntimeError"
    except RuntimeError:
        pass
    assert c._tok_idx == 1                            # наступний запит піде вже з іншим токеном


def test_dashboard_builds(tmp_path):
    s = Store(str(tmp_path / "d.db"))
    s.insert_many([Post("mastodon", str(i), f"u{i % 3}", f"https://e/{i}", f"2026-10-0{1 + i % 2}T10:00:00Z",
                        f"t{i}", ["m"] if i % 2 else [], "python") for i in range(10)])
    s.log_run("2026-10-01T00:00:00+00:00", 10, 10, 0, 0)
    out = build(s, str(tmp_path / "dash.html"))
    html = open(out, encoding="utf-8").read()
    assert "<title>Статистика збору</title>" in html and "python" in html and ">10<" in html
