"""Статичний HTML-дашборд зі статистикою збору (без залежностей, один файл)."""
from html import escape
from pathlib import Path

from .db import Store

CSS = """
:root { color-scheme: light; --bg:#fcfcfb; --card:#ffffff; --line:#e6e5e1; --ink:#0b0b0b;
  --ink2:#52514e; --muted:#7d7b75; --bar:#2a78d6; --bar-hover:#1f5fae; --bad:#e34948; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) {
  color-scheme: dark; --bg:#1a1a19; --card:#222221; --line:#363634; --ink:#f0efec;
  --ink2:#c3c2b7; --muted:#8e8c85; --bar:#3987e5; --bar-hover:#6aa5ee; --bad:#e66767; } }
:root[data-theme="dark"] { color-scheme: dark; --bg:#1a1a19; --card:#222221; --line:#363634;
  --ink:#f0efec; --ink2:#c3c2b7; --muted:#8e8c85; --bar:#3987e5; --bar-hover:#6aa5ee; --bad:#e66767; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width:960px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:22px; margin:0 0 4px; } .sub { color:var(--ink2); margin:0 0 20px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:12px; margin-bottom:16px; }
.tile, .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.tile .v { font-size:26px; font-weight:600; font-variant-numeric:tabular-nums; }
.tile .l { color:var(--ink2); font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr)); gap:12px; }
.grid > * { min-width:0; }
@media (max-width:640px){ .grid { grid-template-columns:minmax(0,1fr); } }
.card h2 { font-size:15px; margin:0 0 10px; }
.row { display:grid; grid-template-columns:minmax(80px,38%) 1fr 48px; align-items:center; gap:8px;
  padding:3px 0; border-radius:4px; }
.row:hover { background:color-mix(in srgb, var(--bar) 10%, transparent); }
.row .n { color:var(--ink2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:14px; }
.row .t { height:12px; }
.row .b { height:12px; background:var(--bar); border-radius:0 4px 4px 0; min-width:2px; }
.row:hover .b { background:var(--bar-hover); }
.row .c { text-align:right; font-variant-numeric:tabular-nums; font-size:14px; }
.cols { display:flex; align-items:flex-end; gap:3px; height:140px; border-bottom:1px solid var(--line); }
.col { flex:1; background:var(--bar); border-radius:4px 4px 0 0; min-height:2px; }
.col:hover { background:var(--bar-hover); }
.axis { display:flex; justify-content:space-between; color:var(--muted); font-size:12px; margin-top:4px; }
table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:right; padding:5px 8px; border-bottom:1px solid var(--line); }
th:first-child, td:first-child { text-align:left; } th { color:var(--ink2); font-weight:500; }
.wide { grid-column:1 / -1; } .err { color:var(--bad); }
"""


def _ts(s) -> str:
    """'2026-10-20T17:47:06+00:00' -> '2026-10-20 17:47'."""
    return str(s)[:16].replace("T", " ")


def _bars(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return '<p class="sub">Немає даних</p>'
    mx = max(n for _, n in rows) or 1
    out = []
    for name, n in rows:
        w = max(1.0, n / mx * 100)
        out.append(
            f'<div class="row" title="{escape(name)}: {n}"><span class="n">{escape(name)}</span>'
            f'<span class="t"><div class="b" style="width:{w:.1f}%"></div></span>'
            f'<span class="c">{n}</span></div>')
    return "".join(out)


def _columns(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return '<p class="sub">Немає даних</p>'
    mx = max(n for _, n in rows) or 1
    cols = "".join(
        f'<div class="col" title="{escape(k)}: {n}" style="height:{max(1.0, n / mx * 100):.1f}%"></div>'
        for k, n in rows)
    return (f'<div class="cols">{cols}</div>'
            f'<div class="axis"><span>{escape(rows[0][0])}</span><span>{escape(rows[-1][0])}</span></div>')


def build(store: Store, out: str, source_label: str = "Mastodon") -> str:
    d = store.dashboard_data()
    tiles = [
        ("Постів", d["total"]), ("Унікальних авторів", d["authors"]),
        ("З медіа", f'{d["media_pct"]:.0f}%'), ("Прогонів", d["runs_n"]),
        ("Запитів 429", d["rate_limited"]), ("Помилок", d["errors"]),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="v">{escape(str(v))}</div>'
                         f'<div class="l">{escape(l)}</div></div>' for l, v in tiles)
    runs_rows = "".join(
        f"<tr><td>{escape(_ts(r[0]))}</td><td>{r[1]}</td><td>{r[2]}</td>"
        f'<td class="{"err" if r[3] else ""}">{r[3]}</td><td>{r[4]}</td></tr>' for r in d["runs"])
    lo, hi = d["date_range"]
    html = f"""<!doctype html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Статистика збору</title><style>{CSS}</style></head><body><main>
<h1>Статистика збору: {escape(source_label)}</h1>
<p class="sub">Діапазон дат постів: {escape(_ts(lo))} — {escape(_ts(hi))} UTC</p>
<div class="tiles">{tiles_html}</div>
<div class="grid">
<section class="card"><h2>Пости за хештегом</h2>{_bars(d["by_tag"])}</section>
<section class="card"><h2>Топ-10 авторів</h2>{_bars(d["top_authors"])}</section>
<section class="card wide"><h2>Пости за {d["bucket_label"]} (дата публікації, UTC)</h2>{_columns(d["timeline"])}</section>
<section class="card wide"><h2>Останні прогони</h2>
<table><thead><tr><th>Початок (UTC)</th><th>Отримано</th><th>Нових</th><th>Помилок</th><th>429</th></tr></thead>
<tbody>{runs_rows}</tbody></table></section>
</div></main></body></html>"""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html, encoding="utf-8")
    return out
