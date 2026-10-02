"""
settle.py — turn finished picks into the public record.

Reads picks.json, fetches results from The Odds API scores endpoint, works out
which picks won, and appends them to results.json with the closing price that
sweep.py recorded. That file is what powers the Record page.

Nothing here edits an existing result. Once a pick is settled it stays settled,
which is the whole point of publishing a record.

Run:  ODDS_API_KEY=xxx python settle.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://api.the-odds-api.com/v4"
OUT = Path(os.getenv("OUT_DIR", "."))
KEY = os.getenv("ODDS_API_KEY", "")
DAYS_BACK = int(os.getenv("DAYS_BACK", 3))


def load(path, default):
    try:
        return json.loads((OUT / path).read_text())
    except Exception:
        return default


def scores_for(sport_key):
    r = requests.get(f"{API}/sports/{sport_key}/scores",
                     params={"apiKey": KEY, "daysFrom": DAYS_BACK},
                     timeout=25)
    r.raise_for_status()
    return r.json()


def tally_of(game):
    """{team: score}, or None if the game isn't finished."""
    if not game.get("completed") or not game.get("scores"):
        return None
    try:
        tally = {s["name"]: float(s["score"]) for s in game["scores"]}
    except (TypeError, ValueError):
        return None
    return tally if len(tally) >= 2 else None


def settle_pick(pick, tally):
    """
    True if the selection won, False if it lost, None if it's a push or we can't
    tell. A push (the line landing exactly) is dropped rather than counted, which
    is how a bookmaker would treat it.
    """
    market = pick.get("market_key", "h2h")
    selection = pick["selection"]

    if market == "h2h":
        best = max(tally.values())
        leaders = [name for name, score in tally.items() if score == best]
        result = "Draw" if len(leaders) > 1 else leaders[0]
        return result == selection

    if market == "totals":
        side, _, line = selection.rpartition(" ")
        try:
            line = float(line)
        except ValueError:
            return None
        total = sum(tally.values())
        if total == line:
            return None
        return total > line if side.lower() == "over" else total < line

    if market == "spreads":
        team, _, point = selection.rpartition(" ")
        try:
            point = float(point)
        except ValueError:
            return None
        if team not in tally:
            return None
        theirs = max(score for name, score in tally.items() if name != team)
        margin = tally[team] + point - theirs
        if margin == 0:
            return None
        return margin > 0

    return None


def main():
    if not KEY:
        sys.exit("Set ODDS_API_KEY first.")
    picks = load("picks.json", {})
    results = load("results.json", [])
    if not picks:
        print("No picks waiting.")
        return

    # event id is the first part of the pick id, so group the work by sport
    sports = sorted({p.get("sport_key", "") for p in picks.values() if p.get("sport_key")})
    games = {}
    for sport in sports:
        try:
            for g in scores_for(sport):
                games[g["id"]] = g
        except Exception as exc:
            print(f"  {sport}: skipped ({exc})")

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK + 4)
    settled, dropped = 0, 0
    for pid in list(picks):
        pick = picks[pid]
        event_id = pid.split("|")[0]
        game = games.get(event_id)
        tally = tally_of(game) if game else None
        result = settle_pick(pick, tally) if tally else None
        if result is None:
            # give up on anything too old to appear in the scores window
            # a push, or too old to appear in the scores window
            start = datetime.fromisoformat(pick["start"].replace("Z", "+00:00"))
            if tally is not None or start < cutoff:
                picks.pop(pid)
                dropped += 1
            continue
        # only a snapshot taken near the off counts as a closing price; a stale
        # one from the morning sweep would flatter the figures
        verified = bool(pick.get("close_final"))
        results.append({
            "sport": pick["sport"],
            "market": pick.get("market", "Match odds"),
            "date": pick["start"][:10],
            "selection": pick["selection"],
            "book": pick["book"],
            "odds": pick["odds"],
            "close": pick.get("close", 0) if verified else 0,
            "close_books": pick.get("close_books", 0) if verified else 0,
            "close_minutes": pick.get("close_minutes_before") if verified else None,
            "won": bool(result),
        })
        picks.pop(pid)
        settled += 1

    (OUT / "picks.json").write_text(json.dumps(picks, indent=1))
    (OUT / "results.json").write_text(json.dumps(results, indent=1))

    feed = load("feed.json", None)
    if feed:
        feed["results"] = results
        (OUT / "feed.json").write_text(json.dumps(feed, indent=1))

    print(f"{settled} settled, {dropped} expired, {len(results)} in the record")


if __name__ == "__main__":
    main()
