# 🇹🇷 Türkiye Finance Digest

A daily news briefing about the Republic of Turkey, focused on finance and the
economy. Every morning a GitHub Actions job asks Claude to research the last ~24
hours of news (using Claude's built-in web search), writes a top-10 digest to a
web page, and emails it to you.

- **No server to run.** Everything happens inside GitHub Actions (free).
- **No database.** Each day's digest is a file in `docs/` — the git history *is*
  the archive.
- **Web page:** served by GitHub Pages from the `docs/` folder.
- **Cost:** ~a few cents per day of Anthropic API usage; infrastructure is free.

## How it works

```
GitHub Actions (daily cron, 08:00 Istanbul)
        │
        ▼
   python main.py
        ├── Claude (claude-opus-4-8) + web_search  → researches the news
        ├── writes docs/index.html + docs/archive/<date>.html + docs/data/<date>.json
        └── emails the digest (Gmail SMTP, optional)
        │
        ▼
   commits docs/ back to the repo  → GitHub Pages publishes it
```

## Setup

See the step-by-step guide you were given, but in short:

1. Create the repo on GitHub and push this code.
2. Add repository **secrets** (Settings → Secrets and variables → Actions):
   - `ANTHROPIC_API_KEY` — required.
   - `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `DIGEST_RECIPIENT` — optional (email).
3. Enable **GitHub Pages**: Settings → Pages → Source = *Deploy from a branch*,
   Branch = `main`, Folder = `/docs`.
4. Run it once manually: Actions → *Daily Türkiye Finance Digest* → *Run workflow*.

## Run it locally (optional)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
open docs/index.html
```

## Configuration

Edit the constants and prompts at the top of `main.py` to change the focus,
model, or number of items. Change the `cron:` line in
`.github/workflows/daily.yml` to change the time (it's in UTC).
