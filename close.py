"""
close.py — freeze the closing price for selections we've already published.

The daily sweep finds bets. This runs every quarter of an hour and does one small
job: for any recommendation whose event is about to start, it fetches that sport's
prices once more, works out the market's margin-free consensus, and freezes it as
the closing line.

That one number is what turns the record from decoration into evidence. Comparing
the price we published against where the market actually settled tells us whether
the engine has an edge months before profit could.

Deliberately frugal: it only looks at sports with a selection starting inside the
window, never fetches the same sport twice in a window, and stops entirely once
it has spent its daily credit budget.

Run:  ODDS_API_KEY=xxx python close.py
"""
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import sweep          # reuse the exact pricing method the recommendation was made with

API = "https://api.the-odds-api.com/v4"
OUT = Path(os.getenv("OUT_DIR", "."))
KEY = os.getenv("ODDS_API_KEY", "")

# T-10 is the primary snapshot, T-5 the backup. The window covers both without
# firing at kick-off itself, when bookmakers are suspending markets.
WINDOW_FROM = float(os.getenv("CLOSE_FROM_MIN", 4))     # minutes before the off
WINDOW_TO = float(os.getenv("CLOSE_TO_MIN", 16))
MIN_BOOKS = int(os.getenv("CLOSE_MIN_BOOKS", 4))
DAILY_BUDGET = int(os.getenv("CLOSE_DAILY_CREDITS", 60))


def load(path, default):
    try:
        return json.loads((OUT / path).read_text())
    except Exception:
        return default


def spent_today(log, today):
    return sum(n for day, n in log.items() if day == today)


def fetch(sport):
    r = requests.get(
        f"{API}/sports/{sport}/odds",
        params={"apiKey": KEY, "regions": "uk", "markets": ",".join(sweep.MARKETS),
                "oddsFormat": "decimal"},
        timeout=25,
    )
    r.raise_for_status()
    print(f"  {sport}: {r.headers.get('x-requests-remaining')} credits left")
    return r.json()


MODEL_VERSION = os.getenv("MODEL_VERSION", "v10")


def closing_prices(event):
    """
    The market's margin-free view of every selection, with how many bookmakers
    it rests on. Same method as the sweep, so the two numbers are comparable.
    """
    out = {}
    for (market_key, selection), by_book in sweep.consensus(event).items():
        if len(by_book) < MIN_BOOKS:
            continue
        prob = statistics.median(list(by_book.values()))
        if 0 < prob < 1:
            out[f"{market_key}|{selection}"] = (round(1 / prob, 2), len(by_book))
    return out


def main():
    if not KEY:
        sys.exit("Set ODDS_API_KEY first.")
    picks = load("picks.json", {})
    if not picks:
        print("No picks waiting.")
        return

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    log = load("close_log.json", {})
    budget_left = DAILY_BUDGET - spent_today(log, today)

    # which sports have something starting inside the window and no closing snapshot yet
    due = {}
    for pid, pick in picks.items():
        if pick.get("close_final"):
            continue
        try:
            start = datetime.fromisoformat(pick["start"].replace("Z", "+00:00"))
        except Exception:
            continue
        minutes = (start - now).total_seconds() / 60
        if WINDOW_FROM <= minutes <= WINDOW_TO:
            due.setdefault(pick.get("sport_key", ""), []).append((pid, pick, minutes))

    if not due:
        print("Nothing in the closing window.")
        return
    if budget_left <= 0:
        print(f"Daily closing budget of {DAILY_BUDGET} credits already spent; skipping.")
        return

    frozen = 0
    for sport, items in due.items():
        if not sport or budget_left <= 0:
            continue
        try:
            events = fetch(sport)
        except Exception as exc:
            print(f"  {sport}: skipped ({exc})")
            continue
        cost = len(sweep.MARKETS)
        budget_left -= cost
        log[today] = log.get(today, 0) + cost

        by_event = {ev["id"]: closing_prices(ev) for ev in events}
        seen_events = {ev["id"] for ev in events}
        for pid, pick, minutes in items:
            event_id = pid.split("|")[0]
            prices = by_event.get(event_id, {})
            key = f"{pick.get('market_key','h2h')}|{pick['selection']}"
            pick["close_attempts"] = pick.get("close_attempts", 0) + 1
            if key not in prices:
                # the event may have gone, or too few firms were still pricing it
                pick["close_status"] = ("insufficient_books" if event_id in seen_events
                                        else "unavailable")
                pick["close_checked_at"] = now.isoformat()
                continue
            fair, books = prices[key]
            pick["close"] = fair
            pick["close_books"] = books
            pick["close_at"] = now.isoformat()
            pick["close_minutes_before"] = round(minutes, 1)
            pick["close_final"] = True          # don't overwrite a good snapshot
            pick["close_status"] = "final"
            frozen += 1
            print(f"  froze {pick['selection']} at {fair} "
                  f"({books} books, T-{minutes:.0f}m)")

    # keep a fortnight of spending history, no more
    cutoff = (now - timedelta(days=14)).date().isoformat()
    log = {d: n for d, n in log.items() if d >= cutoff}

    (OUT / "picks.json").write_text(json.dumps(picks, indent=1))
    (OUT / "close_log.json").write_text(json.dumps(log, indent=1))
    print(f"{frozen} closing prices frozen, {budget_left} credits left in today's budget")


if __name__ == "__main__":
    main()
