"""Інтеграційний тест на локальному мок-сервері: пагінація, 429, інкрементність, ідемпотентність."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import collector.client as client_mod
from collector.collect import run
from collector.config import Config
from collector.db import Store

STATE = {"max": 300, "calls": 0, "rl_done": False}  # статуси з id 1..max


def make(i):
    return {"id": str(i), "url": f"https://mock.example/@u{i % 7}/{i}", "uri": f"https://mock.example/{i}",
            "created_at": f"2026-10-01T{i // 60 % 24:02d}:{i % 60:02d}:00.000Z",
            "content": f"<p>Post <b>{i}</b> #python</p>", "account": {"acct": f"u{i % 7}"},
            "media_attachments": [], "reblog": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        STATE["calls"] += 1
        if STATE["calls"] == 3 and not STATE["rl_done"]:
            STATE["rl_done"] = True
            self.send_response(429)
            self.send_header("Retry-After", "2")
            self.end_headers()
            return
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        limit = int(q.get("limit", 20))
        ids = list(range(STATE["max"], 0, -1))
        if "max_id" in q:
            ids = [i for i in ids if i < int(q["max_id"])][:limit]
        elif "min_id" in q:
            ids = sorted([i for i in ids if i > int(q["min_id"])])[:limit][::-1]
        else:
            ids = ids[:limit]
        base = f"http://127.0.0.1:{self.server.server_port}{urlparse(self.path).path}"
        links = []
        if ids:
            links.append(f'<{base}?limit={limit}&max_id={min(ids)}>; rel="next"')
            links.append(f'<{base}?limit={limit}&min_id={max(ids)}>; rel="prev"')
        body = json.dumps([make(i) for i in ids]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Link", ", ".join(links))
        self.send_header("X-RateLimit-Limit", "300")
        self.send_header("X-RateLimit-Remaining", "250")
        self.end_headers()
        self.wfile.write(body)


def test_end_to_end(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    slept = []
    orig = client_mod.MastodonClient.__init__

    def patched(self, cfg, sleep=None):
        orig(self, cfg, sleep=slept.append)  # не чекаємо насправді
    client_mod.MastodonClient.__init__ = patched

    cfg = Config(instance=f"http://127.0.0.1:{srv.server_port}", tokens=[], hashtags=["python"],
                 pages_per_run=3, page_limit=40, delay=0.1, max_retries=4,
                 db_path=str(tmp_path / "t.db"), proxy=None)
    try:
        r1 = run(cfg)
        assert r1["inserted"] == 120 and r1["rate_limited"] == 1      # 3 сторінки * 40, один 429 пережито
        assert any(s >= 2 for s in slept)                             # поважали Retry-After
        r2 = run(cfg)                                                  # новинок нема — курсор min_id
        assert r2["inserted"] == 0
        STATE["max"] = 330                                             # з'явилось 30 нових
        r3 = run(cfg)
        assert r3["inserted"] == 30                                    # підтягнули лише нове
        r4 = run(cfg, backfill=True)                                   # добір старих
        assert r4["inserted"] == 120
        s = Store(cfg.db_path)
        assert s.count() == 270
        n_before = s.count()
        s.insert_many([])                                              # ідемпотентність
        assert s.export_jsonl(str(tmp_path / "o.jsonl")) == n_before
        assert len({json.loads(l)["url"] for l in open(tmp_path / "o.jsonl")}) == n_before
    finally:
        client_mod.MastodonClient.__init__ = orig
        srv.shutdown()
