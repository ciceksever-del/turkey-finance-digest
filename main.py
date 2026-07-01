"""
Daily Turkey finance news digest.

Runs once a day (via GitHub Actions). It:
  1. Uses Claude + the built-in web_search tool to research the last ~24h of
     news about the Republic of Turkey, focused on finance/economy.
  2. Produces a structured JSON digest (headline, summary, top-10 items).
  3. Renders an HTML page into docs/ (served by GitHub Pages) plus a dated archive.
  4. Emails the digest to you (optional — only if Gmail secrets are set).

Environment variables (all provided via GitHub Actions secrets):
  ANTHROPIC_API_KEY   - required. Your Anthropic API key.
  GMAIL_ADDRESS       - optional. Gmail address that sends the email.
  GMAIL_APP_PASSWORD  - optional. Gmail *app password* (not your real password).
  DIGEST_RECIPIENT    - optional. Where to send the digest (defaults to GMAIL_ADDRESS).
"""

import datetime
import html
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic

MODEL = "claude-opus-4-8"
SITE_DIR = Path(__file__).parent / "docs"
ARCHIVE_DIR = SITE_DIR / "archive"
DATA_DIR = SITE_DIR / "data"

SYSTEM_PROMPT = """\
You are a financial news analyst who writes a concise daily briefing about the \
Republic of Turkey (Türkiye), with a strong focus on finance and the economy.

Prioritise, in roughly this order of importance:
  - The Turkish lira (TRY), FX moves, and the Central Bank of the Republic of \
Türkiye (CBRT / TCMB): rate decisions, reserves, policy signals.
  - Inflation, CPI/PPI prints, and cost-of-living data.
  - Markets: Borsa Istanbul (BIST 100), bonds, CDS, major listed companies.
  - Banking and financial sector news.
  - Fiscal/economic policy, the budget, and the Treasury & Finance Ministry.
  - Major Turkish companies, deals, trade, and foreign investment.
  - Broader political or geopolitical events ONLY when they materially move \
markets or the economy.

Rules:
  - Use the web_search tool to find genuinely recent news (roughly the last 24-48 hours).
  - Only include real, verifiable stories with a working source URL from the search results.
  - Prefer reputable outlets (Reuters, Bloomberg, AA, Daily Sabah, Hurriyet Daily \
News, Financial Times, etc.). Note if a claim is uncertain.
  - Be factual and neutral. No investment advice.
"""

USER_PROMPT_TEMPLATE = """\
Today is {date}. Research the most important news about the Republic of Turkey \
from roughly the last 24 hours, focused on finance and the economy, and build \
today's briefing.

After you have finished researching, respond with ONE JSON object and nothing \
else (no prose before or after, no markdown code fences). Use exactly this shape:

{{
  "date": "{date}",
  "headline": "a single punchy sentence capturing the day",
  "summary": "2-4 sentence narrative overview of the day's Turkey finance news",
  "items": [
    {{
      "rank": 1,
      "title": "headline of the story",
      "source": "publication name",
      "url": "https://direct-link-to-the-article",
      "summary": "1-2 sentence explanation of what happened and why it matters",
      "category": "one of: markets, policy, banking, inflation, companies, trade, macro, other"
    }}
  ]
}}

Include up to 10 items, most important first. If it was a quiet news day, include \
fewer real items rather than padding with filler.
"""


def run_research(client: anthropic.Anthropic, date_str: str) -> str:
    """Run the web-search research loop and return the model's final text."""
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    messages = [{"role": "user", "content": USER_PROMPT_TEMPLATE.format(date=date_str)}]

    # Server-side tools run a loop that can pause; re-send until it finishes.
    for _ in range(10):
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break
    else:
        raise RuntimeError("Research did not converge after 10 continuations.")

    if response.stop_reason == "refusal":
        raise RuntimeError("The model refused to produce the digest.")

    usage = response.usage
    print(
        f"Tokens - input: {usage.input_tokens}, output: {usage.output_tokens}",
        file=sys.stderr,
    )

    return "".join(block.text for block in response.content if block.type == "text")


def parse_digest(text: str, date_str: str) -> dict:
    """Extract and validate the JSON digest from the model's text output."""
    # Be tolerant of stray prose or code fences: grab the outermost {...}.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output:\n{text[:1000]}")

    data = json.loads(match.group(0))
    data.setdefault("date", date_str)
    data.setdefault("headline", "Turkey finance briefing")
    data.setdefault("summary", "")
    items = data.get("items") or []

    cleaned = []
    for i, item in enumerate(items[:10], start=1):
        cleaned.append(
            {
                "rank": item.get("rank", i),
                "title": str(item.get("title", "Untitled")).strip(),
                "source": str(item.get("source", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "summary": str(item.get("summary", "")).strip(),
                "category": str(item.get("category", "other")).strip().lower(),
            }
        )
    data["items"] = cleaned
    return data


PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Türkiye Finance Digest — {date}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         max-width: 760px; margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.55;
         color: #1a1a1a; background: #fafafa; }}
  @media (prefers-color-scheme: dark) {{ body {{ color: #e8e8e8; background: #16181c; }} }}
  header {{ border-bottom: 3px solid #e30a17; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .25rem; }}
  .date {{ color: #888; font-size: .95rem; }}
  .headline {{ font-size: 1.2rem; font-weight: 600; margin: 1.25rem 0 .5rem; }}
  .summary {{ font-size: 1.02rem; opacity: .92; }}
  ol {{ padding-left: 0; list-style: none; counter-reset: item; }}
  li.item {{ counter-increment: item; position: relative; padding: 1rem 0 1rem 2.6rem;
             border-top: 1px solid rgba(128,128,128,.25); }}
  li.item::before {{ content: counter(item); position: absolute; left: 0; top: 1rem;
             width: 1.8rem; height: 1.8rem; background: #e30a17; color: #fff; border-radius: 50%;
             display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .9rem; }}
  .item a {{ color: inherit; text-decoration: none; font-weight: 600; font-size: 1.05rem; }}
  .item a:hover {{ text-decoration: underline; }}
  .meta {{ font-size: .82rem; color: #999; margin: .25rem 0 .35rem; text-transform: uppercase; letter-spacing: .03em; }}
  .item p {{ margin: .35rem 0 0; opacity: .9; }}
  footer {{ margin-top: 3rem; font-size: .85rem; color: #999; border-top: 1px solid rgba(128,128,128,.25); padding-top: 1rem; }}
  footer a {{ color: #e30a17; }}
  .archive a {{ display: inline-block; margin: 0 .5rem .35rem 0; font-size: .85rem; }}
</style>
</head>
<body>
<header>
  <h1>🇹🇷 Türkiye Finance Digest</h1>
  <div class="date">{date_long}</div>
</header>
<p class="headline">{headline}</p>
<p class="summary">{summary}</p>
<ol>
{items}
</ol>
<footer>
  <div class="archive"><strong>Past briefings:</strong><br>{archive}</div>
  <p>Generated automatically with Claude + web search. Not investment advice.</p>
</footer>
</body>
</html>
"""

ITEM_TEMPLATE = """\
  <li class="item">
    {title_html}
    <div class="meta">{source} · {category}</div>
    <p>{summary}</p>
  </li>"""


def render_items(items: list) -> str:
    rows = []
    for item in items:
        title = html.escape(item["title"])
        url = item["url"]
        if url:
            title_html = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{title}</a>'
        else:
            title_html = f"<span>{title}</span>"
        rows.append(
            ITEM_TEMPLATE.format(
                title_html=title_html,
                source=html.escape(item["source"]) or "Unknown source",
                category=html.escape(item["category"]),
                summary=html.escape(item["summary"]),
            )
        )
    return "\n".join(rows) if rows else "  <li class='item'>No notable stories today.</li>"


def render_archive_links(current_date: str) -> str:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dates = sorted(
        (p.stem for p in ARCHIVE_DIR.glob("*.html")),
        reverse=True,
    )
    if current_date not in dates:
        dates = sorted(set(dates) | {current_date}, reverse=True)
    links = [f'<a href="archive/{d}.html">{d}</a>' for d in dates[:30]]
    return " ".join(links) if links else "(none yet)"


def render_page(data: dict, *, in_archive: bool = False) -> str:
    date_str = data["date"]
    try:
        date_long = datetime.date.fromisoformat(date_str).strftime("%A, %d %B %Y")
    except ValueError:
        date_long = date_str
    archive = render_archive_links(date_str)
    if in_archive:
        # Fix relative links when the page lives inside docs/archive/.
        archive = archive.replace('href="archive/', 'href="')
    return PAGE_TEMPLATE.format(
        date=html.escape(date_str),
        date_long=html.escape(date_long),
        headline=html.escape(data["headline"]),
        summary=html.escape(data["summary"]),
        items=render_items(data["items"]),
        archive=archive,
    )


def write_site(data: dict) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    date_str = data["date"]
    # Dated archive copy first, so the homepage's archive list includes today.
    (ARCHIVE_DIR / f"{date_str}.html").write_text(
        render_page(data, in_archive=True), encoding="utf-8"
    )
    (SITE_DIR / "index.html").write_text(render_page(data), encoding="utf-8")
    (DATA_DIR / f"{date_str}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Tell GitHub Pages not to run Jekyll on these files.
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote site for {date_str}", file=sys.stderr)


def send_email(data: dict) -> None:
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print("Email secrets not set — skipping email.", file=sys.stderr)
        return
    recipient = os.environ.get("DIGEST_RECIPIENT") or sender

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🇹🇷 Türkiye Finance Digest — {data['date']}"
    msg["From"] = sender
    msg["To"] = recipient

    lines = [f"{data['headline']}\n", f"{data['summary']}\n"]
    for item in data["items"]:
        lines.append(f"{item['rank']}. {item['title']} ({item['source']})")
        if item["summary"]:
            lines.append(f"   {item['summary']}")
        if item["url"]:
            lines.append(f"   {item['url']}")
        lines.append("")
    text_body = "\n".join(lines)

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(render_page(data), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    print(f"Emailed digest to {recipient}", file=sys.stderr)


def main() -> None:
    date_str = datetime.date.today().isoformat()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    raw = run_research(client, date_str)
    data = parse_digest(raw, date_str)
    write_site(data)
    send_email(data)
    print(f"Done. {len(data['items'])} items for {date_str}.")


if __name__ == "__main__":
    main()
