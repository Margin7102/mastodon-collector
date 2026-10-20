"""Конфіг з .env / змінних середовища. Секрети живуть лише тут, не в коді."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    instance: str = field(default_factory=lambda: os.getenv("MASTODON_INSTANCE", "https://mastodon.social").rstrip("/"))
    # Токени опційні (публічні таймлайни працюють і без них). Кілька через кому -> ротація.
    tokens: list[str] = field(default_factory=lambda: _list("MASTODON_TOKENS"))
    hashtags: list[str] = field(default_factory=lambda: _list("HASHTAGS", "python,opensource,linux"))
    pages_per_run: int = field(default_factory=lambda: int(os.getenv("PAGES_PER_RUN", "5")))
    page_limit: int = field(default_factory=lambda: int(os.getenv("PAGE_LIMIT", "40")))
    delay: float = field(default_factory=lambda: float(os.getenv("REQUEST_DELAY", "1.5")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "6")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/collector.db"))
    proxy: str | None = field(default_factory=lambda: os.getenv("HTTP_PROXY_URL") or None)
