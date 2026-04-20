"""
Nightly reflection job — runs once a day, reads yesterday's actions,
asks Claude to find patterns, saves insights to the patterns table.
Run via systemd timer (sunday-reflect.timer).
"""
import json
import sys
from datetime import datetime, timedelta

import anthropic

import memory
from config import Config

REFLECT_PROMPT = """\
You are analyzing the action log of Sunday, a voice assistant used by Karthik Veenam.
Below is a list of actions Sunday took over the past 24 hours, with timestamps.

Your job: extract clear, specific, useful patterns and insights about Karthik's routines,
preferences, and behaviors. Focus on:
- Time-based habits (e.g. "turns on fan around 11 PM before sleeping")
- Device usage patterns (e.g. "always turns on cove light + dashboard together")
- Sequences (e.g. "AC is turned on shortly before geyser — likely getting ready to sleep")
- Preferences (e.g. "prefers fan on medium, not high")
- Anything surprising or noteworthy

IMPORTANT: The following insights are already known. Do NOT repeat, rephrase, or add minor variations of these.
Only output insights that are genuinely new or add meaningfully new detail not covered below.

Already known insights:
{existing_insights}

Output a JSON array of insight objects. Each object must have:
  "insight": a single clear sentence describing the pattern
  "category": one of: routine, preference, sequence, device, general

If there is nothing new to add, return an empty array.
If the data is too sparse or does not clearly support a pattern, do not invent or assume insights — return an empty array instead.

Output ONLY valid JSON. No explanation, no markdown. Example:
[
  {{"insight": "Karthik turns on the fan every night between 10 PM and midnight.", "category": "routine"}},
  {{"insight": "Cove light and dashboard lights are almost always turned on together.", "category": "sequence"}}
]

Action log:
"""


def main() -> None:
    config = Config.load()
    memory.init()
    mem = memory.get()

    since = (datetime.now() - timedelta(hours=24)).isoformat()
    actions = mem.actions_since(since)

    if not actions:
        print("[Reflect] No actions in the last 24 hours. Nothing to analyse.")
        return

    log_text = "\n".join(
        f"[{a['timestamp']}] {a['tool_name']} | input: {a['inputs']} | result: {a['result']} | user said: {a['user_text']!r}"
        for a in actions
    )

    existing = mem.get_insights(limit=200)
    existing_text = "\n".join(f"- {i['insight']}" for i in existing) or "None yet."

    print(f"[Reflect] Analysing {len(actions)} actions... ({len(existing)} existing insights)")

    prompt = REFLECT_PROMPT.format(existing_insights=existing_text) + log_text

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    raw = raw.strip()

    try:
        insights = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[Reflect] Failed to parse response:\n{raw}")
        sys.exit(1)

    if not insights:
        print("[Reflect] No patterns found yet — need more data.")
        sys.exit(0)

    for item in insights:
        insight = item.get("insight", "").strip()
        category = item.get("category", "general")
        if insight:
            mem.save_insight(insight, category)
            print(f"[Reflect] [{category}] {insight}")

    print(f"[Reflect] Saved {len(insights)} insights.")


if __name__ == "__main__":
    main()
