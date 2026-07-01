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

# Model can be overridden via the DIGEST_MODEL env var / repo variable.
MODEL = os.environ.get("DIGEST_MODEL", "claude-sonnet-5")
SITE_DIR = Path(__file__).parent / "docs"
ARCHIVE_DIR = SITE_DIR / "archive"
DATA_DIR = SITE_DIR / "data"

SYSTEM_PROMPT = """\
You are a markets research analyst producing a daily intelligence briefing for a \
senior salesperson / coverage banker on the markets desk of a large international \
investment bank. The reader covers Türkiye. Their clients are Turkish financial \
institutions (including the Central Bank / CBRT), Turkish banks, and large Turkish \
and multinational corporates that operate or invest in Türkiye. The reader sells \
financing, hedging, and risk-management solutions across FX, Rates, Commodities, \
Credit and Equities, plus solutions tied to M&A and capital-expenditure activity.

Your job is to surface news from roughly the last 24 hours that helps the reader:
  (a) source business / revenue leads,
  (b) stay ahead of what their clients are doing,
  (c) track competitor (other banks / dealers) activity and mandates, and
  (d) catch regulatory or rule changes that affect clients or the bank's market \
positions in Türkiye.

Prioritise, in roughly this order:
  1. Lead & client signals — capital raising, refinancing, bond or syndicated-loan \
issuance (DCM), IPOs / blocks / rights issues (ECM), M&A, large capex or investment \
programmes, privatisations, and any FX / rates / commodity exposure that implies a \
hedging or financing need at a Turkish FI or corporate, or at a multinational active \
in Türkiye.
  2. CBRT & policy — rate decisions, reserves, FX interventions and swaps, funding / \
liquidity operations, KKM wind-down, macroprudential steps.
  3. Regulation & rule changes — CBRT, BDDK (banking regulator), SPK/CMB (capital \
markets board), Treasury, tax, and capital / derivatives rules, and their impact on \
clients or on dealing and positions.
  4. Market moves & flows — FX (USDTRY, EURTRY, TRY vol, forwards / swaps), Rates \
(policy rate, TLREF, local curve, Eurobonds), Credit (sovereign CDS, rating actions \
from Moody's / S&P / Fitch, corporate credit), Commodities (energy import bill, gold, \
corporate commodity exposure), and Equities (BIST 100, sectors).
  5. Competitor activity — mandates won or lost by other investment banks or dealers, \
league-table moves, notable deal roles.

Rules:
  - Use the web_search tool to find genuinely recent, real items (roughly the last \
24-48 hours). Only include stories with a working source URL from the results.
  - ALSO check these primary sources directly with the web_fetch tool on these exact \
URLs, then follow through to the individual disclosures / releases you find:
      * KAP - Public Disclosure Platform: https://kap.org.tr/en - material \
disclosures by BIST-listed companies (bond / loan issuance, capital increases, \
buybacks, M&A, board decisions, investment / capex announcements).
      * CBRT press releases: https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Main+Menu/Announcements/Press+Releases
      * Ministry of Treasury and Finance (public finance): https://en.hmb.gov.tr/kategori/public-finance/sayfa/1
  - Prefer primary and reputable sources: the three above, plus BDDK / SPK releases, \
Reuters, Bloomberg, IFR, GlobalCapital, Debtwire, AA, Daily Sabah, Financial Times.
  - Aggregate PUBLIC information only. Do not speculate about material non-public \
information. Be factual and neutral; this is market intelligence, not investment advice.
  - Keep each item's summary to ONE short, factual sentence (aim for under ~25 words).
  - Record the publication date and time of each item as reported by the source \
(include the timezone if given, e.g. "2026-07-01 09:15 TRT"; use the date alone if \
no time is available).
"""

USER_PROMPT_TEMPLATE = """\
Today is {date}. Research the news most relevant to a sell-side markets banker \
covering Türkiye from roughly the last 24 hours, and build today's intelligence \
briefing. Cast a wide net across FX, Rates, Commodities, Credit, Equities, DCM/ECM, \
M&A, CBRT / regulatory actions, named client institutions and corporates, and \
competitor deal activity. Also review KAP (listed-company public disclosures), the \
CBRT press-release page, and the Ministry of Treasury & Finance page for fresh \
official items.

After you have finished researching, respond with ONE JSON object and nothing else \
(no prose before or after, no markdown code fences). Use exactly this shape:

{{
  "date": "{date}",
  "headline": "one punchy sentence capturing the day's most business-relevant theme",
  "summary": "3-5 sentence market-focused overview: TRY / rates / credit tone, key \
CBRT or regulatory items, and the standout lead / client / competitor stories",
  "items": [
    {{
      "rank": 1,
      "title": "headline of the story",
      "source": "publication name",
      "url": "https://direct-link-to-the-article",
      "published": "publication date and time as reported, e.g. 2026-07-01 09:15 TRT (date alone if no time)",
      "summary": "ONE short factual sentence on what happened (under ~25 words)",
      "tag": "one of: lead, client, competitor, regulation, cbrt, market",
      "category": "one of: fx, rates, credit, commodities, equities, m&a, dcm, ecm, ratings, regulation, macro, other"
    }}
  ]
}}

Include up to 12 items, most business-relevant first. If it was a quiet news day, \
include fewer real items rather than padding with filler.
"""


def _request_kwargs(model: str) -> dict:
    """Model-specific tools and reasoning settings (kept lean to control cost)."""
    if model.startswith("claude-haiku"):
        # Haiku 4.5 doesn't support the newer search/fetch tools, adaptive
        # thinking, or the effort parameter — use the basic web search only.
        return {
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
        }
    # Opus 4.8 / Sonnet 5: dynamic-filtering search + fetch, light reasoning.
    return {
        "tools": [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 10},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
        ],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }


def run_research(client: anthropic.Anthropic, date_str: str, model: str = MODEL) -> str:
    """Run the web-search research loop and return the model's final text."""
    extra = _request_kwargs(model)
    messages = [{"role": "user", "content": USER_PROMPT_TEMPLATE.format(date=date_str)}]

    # Server-side tools run a loop that can pause; re-send until it finishes.
    for _ in range(12):
        response = client.messages.create(
            model=model,
            max_tokens=12000,
            system=SYSTEM_PROMPT,
            messages=messages,
            **extra,
        )
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break
    else:
        raise RuntimeError("Research did not converge after 12 continuations.")

    if response.stop_reason == "refusal":
        raise RuntimeError("The model refused to produce the digest.")

    usage = response.usage
    print(
        f"[{model}] tokens - input: {usage.input_tokens}, output: {usage.output_tokens}",
        file=sys.stderr,
    )

    return "".join(block.text for block in response.content if block.type == "text")


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str) -> str:
    """Strip stray HTML / citation markup (e.g. <cite index="...">) from model text."""
    return _TAG_RE.sub("", str(value)).strip()


def parse_digest(text: str, date_str: str) -> dict:
    """Extract and validate the JSON digest from the model's text output."""
    # Be tolerant of stray prose or code fences: grab the outermost {...}.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output:\n{text[:1000]}")

    data = json.loads(match.group(0))
    data.setdefault("date", date_str)
    data["headline"] = _clean_text(data.get("headline", "Turkey finance briefing"))
    data["summary"] = _clean_text(data.get("summary", ""))
    items = data.get("items") or []

    cleaned = []
    for i, item in enumerate(items[:12], start=1):
        cleaned.append(
            {
                "rank": item.get("rank", i),
                "title": _clean_text(item.get("title", "Untitled")),
                "source": str(item.get("source", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "published": str(item.get("published", "")).strip(),
                "summary": _clean_text(item.get("summary", "")),
                "tag": str(item.get("tag", "")).strip().lower(),
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
  .tag {{ display: inline-block; font-size: .66rem; font-weight: 700; letter-spacing: .06em;
          padding: .12rem .45rem; border-radius: 4px; color: #fff; margin-right: .5rem; vertical-align: middle; }}
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
    <div class="meta">{tag_badge}{meta_text}</div>
    <p>{summary}</p>
  </li>"""

# Colour-coded badges so each item's relevance is scannable at a glance.
TAG_COLORS = {
    "lead": "#0a7d2c",        # green — business opportunity
    "client": "#1558d6",      # blue  — client development
    "competitor": "#c25e00",  # orange— competitor activity
    "regulation": "#6f42c1",  # purple— rule / regulatory change
    "cbrt": "#e30a17",        # red   — central bank / policy
    "market": "#555555",      # grey  — market move / flow
}


def render_items(items: list) -> str:
    rows = []
    for item in items:
        title = html.escape(item["title"])
        url = item["url"]
        if url:
            title_html = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{title}</a>'
        else:
            title_html = f"<span>{title}</span>"

        tag = item.get("tag", "")
        if tag:
            color = TAG_COLORS.get(tag, "#555555")
            tag_badge = f'<span class="tag" style="background:{color}">{html.escape(tag.upper())}</span> '
        else:
            tag_badge = ""

        meta_bits = [item.get("source") or "Unknown source"]
        if item.get("published"):
            meta_bits.append(item["published"])
        if item.get("category"):
            meta_bits.append(item["category"])
        meta_text = html.escape(" · ".join(meta_bits))

        rows.append(
            ITEM_TEMPLATE.format(
                title_html=title_html,
                tag_badge=tag_badge,
                meta_text=meta_text,
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
        tag = f"[{item['tag'].upper()}] " if item.get("tag") else ""
        lines.append(f"{item['rank']}. {tag}{item['title']} ({item['source']})")
        meta = " · ".join(b for b in (item.get("published", ""), item.get("category", "")) if b)
        if meta:
            lines.append(f"   {meta}")
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
