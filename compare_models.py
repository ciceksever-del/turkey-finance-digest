"""One-off: run today's digest through three models and print each result.

Used to compare Sonnet 5 vs Opus 4.8 vs Haiku 4.5 before committing to one.
Prints each digest JSON to stdout between clear markers so it can be read from
the GitHub Actions logs. Does not write to docs/ or send email.
"""

import datetime
import json
import sys

import anthropic

from main import parse_digest, run_research

MODELS = ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]


def main() -> None:
    client = anthropic.Anthropic()
    date_str = datetime.date.today().isoformat()
    for model in MODELS:
        print(f"\n========== BEGIN {model} ==========", flush=True)
        try:
            raw = run_research(client, date_str, model=model)
            data = parse_digest(raw, date_str)
            print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
        except Exception as exc:  # noqa: BLE001 - want to see any failure per model
            print(f"ERROR for {model}: {exc}", file=sys.stderr, flush=True)
            print(f"ERROR for {model}: {exc}", flush=True)
        print(f"========== END {model} ==========\n", flush=True)


if __name__ == "__main__":
    main()
