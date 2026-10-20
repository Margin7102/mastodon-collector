"""Збір хештег-таймлайнів Mastodon в канонічну модель, інкрементно за курсорами."""
import argparse
import html
import logging
import re
import sys
from html.parser import HTMLParser

from .client import MastodonClient
from .config import Config
from .db import Post, Store, now_iso

log = logging.getLogger("collect")
SOURCE = "mastodon"


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    p.feed(s or "")
    return re.sub(r"\n{3,}", "\n\n", html.unescape("".join(p.parts))).strip()


def to_post(status: dict, tag: str) -> Post | None:
    """Мапінг статусу Mastodon -> канонічна модель. Реблоги пропускаємо (дублі чужих постів)."""
    if status.get("reblog"):
        return None
    url = status.get("url") or status.get("uri")
    if not url or not status.get("id"):
        return None
    acct = (status.get("account") or {}).get("acct", "unknown")
    return Post(
        source=SOURCE,
        source_id=str(status["id"]),
        author=acct,
        url=url,
        date=status.get("created_at", ""),
        text=html_to_text(status.get("content", "")),
        media=[m.get("url") for m in status.get("media_attachments", []) if m.get("url")],
        tag=tag,
    )


def _next_url(resp, rel: str) -> str | None:
    return (resp.links.get(rel) or {}).get("url")


def collect_tag(client: MastodonClient, store: Store, cfg: Config, tag: str, backfill: bool = False) -> tuple[int, int]:
    """Одна невелика партія для хештега. Курсор зберігаємо після КОЖНОЇ сторінки."""
    url = f"{cfg.instance}/api/v1/timelines/tag/{tag}"
    k_new, k_old = f"{tag}:newest", f"{tag}:oldest"
    newest, oldest = store.get_cursor(k_new), store.get_cursor(k_old)

    params: dict = {"limit": cfg.page_limit}
    direction = "next"  # next = далі в минуле, prev = далі в майбутнє (до свіжіших)
    if backfill:
        if not oldest:
            log.info("[%s] backfill: ще немає початкового збору — спершу запустіть звичайний прогін", tag)
            return 0, 0
        params["max_id"] = oldest
    elif newest:
        params["min_id"] = newest
        direction = "prev"
    # інакше — перший запуск: беремо найсвіжіше й ідемо назад

    fetched = inserted = 0
    for page in range(cfg.pages_per_run):
        resp = client.get(url, params)
        data = resp.json()
        if not data:
            log.info("[%s] порожня сторінка — кінець", tag)
            break
        posts = [p for p in (to_post(s, tag) for s in data) if p]
        fetched += len(data)
        inserted += store.insert_many(posts)

        ids = [int(s["id"]) for s in data]
        cur_new = store.get_cursor(k_new)
        cur_old = store.get_cursor(k_old)
        if not cur_new or max(ids) > int(cur_new):
            store.set_cursor(k_new, str(max(ids)))
        if not cur_old or min(ids) < int(cur_old):
            store.set_cursor(k_old, str(min(ids)))

        nxt = _next_url(resp, direction)
        if not nxt:
            break
        url, params = nxt, None  # Link-URL уже містить усі параметри
    log.info("[%s] fetched=%d new=%d", tag, fetched, inserted)
    return fetched, inserted


def run(cfg: Config, backfill: bool = False) -> dict:
    store = Store(cfg.db_path)
    client = MastodonClient(cfg)
    started = now_iso()
    total_f = total_i = 0
    for tag in cfg.hashtags:
        try:  # збій одного хештега не валить решту
            f, i = collect_tag(client, store, cfg, tag, backfill)
            total_f += f
            total_i += i
        except Exception as e:  # noqa: BLE001
            client.stats.errors += 1
            log.exception("[%s] збій, продовжуємо з наступним: %s", tag, e)
    store.log_run(started, total_f, total_i, client.stats.errors, client.stats.rate_limited)
    summary = {"fetched": total_f, "inserted": total_i, "errors": client.stats.errors,
               "rate_limited": client.stats.rate_limited, "db_total": store.count()}
    log.info("run done: %s", summary)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Інкрементний колектор Mastodon")
    ap.add_argument("--backfill", action="store_true", help="добрати старіші записи за курсором :oldest")
    ap.add_argument("--export", metavar="PATH", help="експорт у JSONL і вихід (напр. data/sample.jsonl)")
    ap.add_argument("--stats", action="store_true", help="показати статистику збору")
    ap.add_argument("--dashboard", metavar="PATH", help="згенерувати HTML-дашборд (напр. data/dashboard.html) і вийти")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if not args.verbose else logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("collector.log", encoding="utf-8")])
    cfg = Config()
    if args.export:
        n = Store(cfg.db_path).export_jsonl(args.export)
        print(f"exported {n} records -> {args.export}")
        return 0
    if args.dashboard:
        from .dashboard import build
        print(f"dashboard -> {build(Store(cfg.db_path), args.dashboard)}")
        return 0
    if args.stats:
        import json
        print(json.dumps(Store(cfg.db_path).stats(), ensure_ascii=False, indent=2, default=str))
        return 0
    run(cfg, backfill=args.backfill)
    return 0


if __name__ == "__main__":
    sys.exit(main())
