"""Render the captured three-model comparison into docs/compare.html.

Reads the real outputs saved under compare_data/ and produces a single page
(reusing the digest's item styling) so the three models can be read side by side
in a browser. No API calls — pure rendering.
"""

import json
from pathlib import Path

from main import render_items

MODELS = [
    ("haiku.json", "Haiku 4.5", "Fastest & cheapest — ~$0.15/run, ~30s. Widest coverage this run."),
    ("opus.json", "Opus 4.8", "Deepest per-item nuance — but ~$1.00/run and ~25 minutes."),
    ("sonnet.json", "Sonnet 5", "Returned nothing this run — the tool budget was set too low (since fixed); not a fair test."),
]

STYLE = """
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         max-width: 820px; margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.55;
         color: #1a1a1a; background: #fafafa; }
  @media (prefers-color-scheme: dark) { body { color: #e8e8e8; background: #16181c; } }
  h1 { border-bottom: 3px solid #e30a17; padding-bottom: .6rem; }
  .intro { color: #888; font-size: .95rem; }
  section.model { margin: 2.5rem 0; padding: 1.2rem 1.3rem; border: 1px solid rgba(128,128,128,.3);
                  border-radius: 10px; background: rgba(128,128,128,.05); }
  section.model h2 { margin: 0 0 .2rem; }
  .note { font-weight: 400; font-size: .85rem; color: #999; display: block; margin-top: .2rem; }
  .headline { font-size: 1.12rem; font-weight: 600; margin: 1rem 0 .4rem; }
  .summary { font-size: .98rem; opacity: .92; }
  ol { padding-left: 0; list-style: none; counter-reset: item; }
  li.item { counter-increment: item; position: relative; padding: .8rem 0 .8rem 2.4rem;
            border-top: 1px solid rgba(128,128,128,.25); }
  li.item::before { content: counter(item); position: absolute; left: 0; top: .8rem;
            width: 1.7rem; height: 1.7rem; background: #e30a17; color: #fff; border-radius: 50%;
            display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .85rem; }
  .item a { color: inherit; text-decoration: none; font-weight: 600; font-size: 1.02rem; }
  .item a:hover { text-decoration: underline; }
  .meta { font-size: .8rem; color: #999; margin: .2rem 0 .3rem; text-transform: uppercase; letter-spacing: .03em; }
  .item p { margin: .3rem 0 0; opacity: .9; font-size: .95rem; }
  .tag { display: inline-block; font-size: .66rem; font-weight: 700; letter-spacing: .06em;
         padding: .12rem .45rem; border-radius: 4px; color: #fff; margin-right: .5rem; vertical-align: middle; }
  .empty { color: #b00; font-style: italic; }
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model comparison — Türkiye Finance Digest</title>
<style>{style}</style>
</head>
<body>
<h1>🔬 Model comparison</h1>
<p class="intro">The same daily brief run through three models on 2026-07-01, so you can
compare coverage, tone and depth. This is a one-off decision aid, not the live digest.</p>
{sections}
<p class="intro" style="margin-top:3rem">Live digest: <a href="./">index</a></p>
</body>
</html>
"""

SECTION = """<section class="model">
  <h2>{name}<span class="note">{note}</span></h2>
  <p class="headline">{headline}</p>
  <p class="summary">{summary}</p>
  <ol>
{items}
  </ol>
</section>"""


def main() -> None:
    import html

    sections = []
    for filename, name, note in MODELS:
        data = json.loads((Path("compare_data") / filename).read_text(encoding="utf-8"))
        items = data.get("items") or []
        items_html = (
            render_items(items)
            if items
            else "  <li class='item empty'>No items produced.</li>"
        )
        sections.append(
            SECTION.format(
                name=html.escape(name),
                note=html.escape(note),
                headline=html.escape(data.get("headline", "")),
                summary=html.escape(data.get("summary", "")),
                items=items_html,
            )
        )

    out = Path("docs") / "compare.html"
    out.write_text(
        PAGE.format(style=STYLE, sections="\n".join(sections)), encoding="utf-8"
    )
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
