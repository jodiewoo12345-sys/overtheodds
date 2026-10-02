"""
sweep.py — build the board.   [version 11: model and config stamped separately]

Fetches UK bookmaker prices from The Odds API, works out what the market as a
whole thinks each selection's chance is, and writes the bets where one
bookmaker is out of line into feed.json.

Also records every pick in picks.json and keeps updating its closing price, so
settle.py can report closing line value later.

Run:  ODDS_API_KEY=xxx python sweep.py
"""
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://api.the-odds-api.com/v4"
OUT = Path(os.getenv("OUT_DIR", "."))
KEY = os.getenv("ODDS_API_KEY", "")

MIN_EDGE = float(os.getenv("MIN_EDGE", 2.0))       # percent
MIN_BOOKS = int(os.getenv("MIN_BOOKS", 5))          # ignore thinly priced markets
MIN_HOURS = float(os.getenv("MIN_HOURS", 2.0))      # skip anything starting sooner
MAX_PER_SPORT = int(os.getenv("MAX_PER_SPORT", 15))

# Which markets to scan. Each one costs a credit per sport per run, so h2h + totals
# is 2 credits. Add "spreads" when you can afford 3.
MARKETS = tuple(k.strip() for k in os.getenv("MARKETS", "h2h,totals").split(",") if k.strip())


def scanner_config():
    """A short, self-describing label for the rules that produced a recommendation."""
    return os.getenv("SCANNER_CONFIG") or (
        f"edge{MIN_EDGE:g}-books{MIN_BOOKS}-odds{MAX_ODDS:g}-" + "+".join(MARKETS))
MARKET_NAMES = {"h2h": "Match odds", "totals": "Totals", "spreads": "Handicap"}

# Two separate stamps, because they change for different reasons.
#
# MODEL_VERSION moves only when the maths changes: how margin is removed, how the
# consensus is taken, how lines are grouped. Bump it when a number would come out
# differently from the same prices.
#
# SCANNER_CONFIG records the selection rules in force: thresholds, book minimums,
# competitions, markets. It regenerates itself from the settings below, so it can
# never drift out of step with what actually ran.
MODEL_VERSION = os.getenv("MODEL_VERSION", "1.0")
MAX_ODDS = float(os.getenv("MAX_ODDS", 6.0))       # long shots carry huge margin: consensus is meaningless
MAX_EDGE = float(os.getenv("MAX_EDGE", 12.0))       # anything above this is a data artefact, not value

# Exchanges carry no margin and charge commission on winnings, so their prices are
# keener than any bookmaker's and would fill the board every day. They stay in the
# consensus, where their sharpness is useful, but are never listed as a bet.
EXCHANGES = tuple(k.strip() for k in os.getenv(
    "EXCHANGES", "betfair_ex_uk,betfair_ex_au,betfair_ex_eu,matchbook,smarkets,betdaq"
).split(",") if k.strip())
SPORT_PREFIXES = tuple(os.getenv(
    # Four heavily priced competitions. The consensus is most trustworthy where the
    # most firms are watching, and this fits inside the free API tier.
    "SPORT_PREFIXES",
    "soccer_epl,soccer_uefa_champs_league,tennis_atp,basketball_nba"
).split(","))


def active_sports():
    """The free /sports endpoint tells us what's in season. Costs no credits."""
    r = requests.get(f"{API}/sports", params={"apiKey": KEY}, timeout=20)
    r.raise_for_status()
    return [s["key"] for s in r.json()
            if s.get("active") and not s.get("has_outrights")
            and s["key"].startswith(SPORT_PREFIXES)]


def fetch_odds(sport):
    r = requests.get(
        f"{API}/sports/{sport}/odds",
        params={"apiKey": KEY, "regions": "uk", "markets": "h2h",
                "oddsFormat": "decimal", "includeLinks": "true"},
        timeout=25,
    )
    r.raise_for_status()
    print(f"  {sport}: {r.headers.get('x-requests-remaining')} credits left")
    return r.json()


def devig(prices):
    """Strip a bookmaker's margin out of its own book to get its true chances."""
    implied = {name: 1 / price for name, price in prices.items()}
    total = sum(implied.values())
    return {name: p / total for name, p in implied.items()}


def label_for(market_key, outcome):
    """'Over 2.5', 'Leeds United -1.5', or just the team name for match odds."""
    point = outcome.get("point")
    if point is None:
        return outcome["name"]
    if market_key == "spreads":
        return f"{outcome['name']} {point:+g}"
    return f"{outcome['name']} {point:g}"


def consensus(event):
    """
    For each market and selection, collect every bookmaker's margin-free view.
    Returns {(market_key, selection): {book: probability}}.

    Totals and handicaps are grouped by their line, not by the market as a whole.
    Over 2.5 at one firm and over 3.5 at another are different bets, so each line
    is priced against the firms offering that same line. Without this, a market
    where bookmakers disagree about the main line produces no comparable prices
    at all, which is what was quietly happening to every totals market.
    """
    groups = {}          # (market_key, line) -> [(book, {selection: price})]
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] not in MARKETS:
                continue
            lines = {}
            for o in market.get("outcomes", []):
                if not o.get("price", 0) > 1:
                    continue
                point = o.get("point")
                # handicaps mirror: -1.5 and +1.5 are the same line
                line = None if point is None else abs(float(point))
                lines.setdefault(line, {})[label_for(market["key"], o)] = o["price"]
            for line, prices in lines.items():
                if len(prices) >= 2:
                    groups.setdefault((market["key"], line), []).append((book["key"], prices))

    views = {}
    for (market_key, _line), books in groups.items():
        shapes = {}
        for _, prices in books:
            shape = tuple(sorted(prices))
            shapes[shape] = shapes.get(shape, 0) + 1
        standard = max(shapes, key=shapes.get)
        for book_key, prices in books:
            if tuple(sorted(prices)) != standard:
                continue
            for name, prob in devig(prices).items():
                views.setdefault((market_key, name), {})[book_key] = prob
    return views


def title_of(book_key, event):
    for b in event.get("bookmakers", []):
        if b["key"] == book_key:
            return b.get("title", book_key)
    return book_key


def link_for(event, book_key, market_key, selection):
    """Deepest link the API gives us: the bet, then the market, then the bookmaker."""
    for b in event.get("bookmakers", []):
        if b["key"] != book_key:
            continue
        for m in b.get("markets", []):
            if m["key"] != market_key:
                continue
            for o in m.get("outcomes", []):
                if label_for(market_key, o) == selection and o.get("link"):
                    return o["link"]
            if m.get("link"):
                return m["link"]
        return b.get("link", "")
    return ""


scanned = {"events": 0, "selections": 0, "sports": 0, "by_market": {}}


def find_value(events, sport_label, sport_key):
    """Returns (bets worth listing, the market's current true price for every selection)."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=MIN_HOURS)
    found = []
    market_prices = {}
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start < cutoff:
            continue
        views = consensus(ev)
        name = f"{ev.get('home_team','')} v {ev.get('away_team','')}".strip(" v")
        for (market_key, selection), by_book in views.items():
            if len(by_book) < MIN_BOOKS:
                continue
            scanned["selections"] += 1
            scanned["by_market"][market_key] = scanned["by_market"].get(market_key, 0) + 1
            # the whole market's view, used later as the closing price
            whole = statistics.median(list(by_book.values()))
            if 0 < whole < 1:
                market_prices[f"{ev['id']}|{market_key}|{selection}"] = round(1 / whole, 2)
            for book_key, _ in by_book.items():
                if book_key in EXCHANGES:
                    continue
                # the market's verdict, excluding the bookmaker being judged
                others = [p for k, p in by_book.items() if k != book_key]
                if len(others) < MIN_BOOKS - 1:
                    continue
                fair_prob = statistics.median(others)
                if not 0 < fair_prob < 1:
                    continue
                price = price_at(ev, book_key, market_key, selection)
                if not price or price > MAX_ODDS:
                    continue
                fair = 1 / fair_prob
                edge = (price / fair - 1) * 100
                if edge < MIN_EDGE or edge > MAX_EDGE:
                    continue
                found.append({
                    "id": f"{ev['id']}|{book_key}|{market_key}|{selection}",
                    "sport_key": sport_key,
                    "sport": sport_label,
                    "market_key": market_key,
                    "market": MARKET_NAMES.get(market_key, market_key),
                    "event": name,
                    "start": ev["commence_time"],
                    "selection": selection,
                    "book": title_of(book_key, ev),
                    "odds": round(price, 2),
                    "fair": round(fair, 2),
                    "books": len(by_book),
                    "link": link_for(ev, book_key, market_key, selection),
                })
    scanned["events"] += len(events)
    found = merge_duplicates(found)
    found.sort(key=lambda b: b["odds"] / b["fair"], reverse=True)
    return found[:MAX_PER_SPORT], market_prices


def merge_duplicates(bets):
    """
    The same bet offered by several firms is one opportunity, not several. Keep the
    best price and list the others, so the board stays readable and you can see
    where else to get on if an account is restricted.
    """
    best = {}
    for b in bets:
        key = (b["event"], b["market_key"], b["selection"])
        current = best.get(key)
        if current is None or b["odds"] > current["odds"]:
            if current:
                b.setdefault("also", []).extend(
                    current.get("also", []) + [{"book": current["book"], "odds": current["odds"]}])
            best[key] = b
        else:
            current.setdefault("also", []).append({"book": b["book"], "odds": b["odds"]})
    out = []
    for b in best.values():
        if b.get("also"):
            seen, rows = set(), []
            for a in sorted(b["also"], key=lambda x: -x["odds"]):
                if a["book"] in seen:
                    continue
                seen.add(a["book"])
                rows.append(a)
            b["also"] = rows[:4]
        out.append(b)
    return out


def price_at(event, book_key, market_key, selection):
    for b in event.get("bookmakers", []):
        if b["key"] != book_key:
            continue
        for m in b.get("markets", []):
            if m["key"] != market_key:
                continue
            for o in m.get("outcomes", []):
                if label_for(market_key, o) == selection:
                    return o.get("price")
    return None


def load_json(path, default):
    try:
        return json.loads((OUT / path).read_text())
    except Exception:
        return default


def main():
    if not KEY:
        sys.exit("Set ODDS_API_KEY first.")
    bets = []
    market_prices = {}
    for sport in active_sports():
        try:
            events = fetch_odds(sport)
        except Exception as exc:
            print(f"  {sport}: skipped ({exc})")
            continue
        label = events[0].get("sport_title", sport) if events else sport
        scanned["sports"] += 1
        sport_bets, sport_prices = find_value(events, label, sport)
        bets.extend(sport_bets)
        market_prices.update(sport_prices)

    # keep a record of every pick
    picks = load_json("picks.json", {})
    for b in bets:
        picks.setdefault(b["id"], {
            "sport": b["sport"], "sport_key": b["sport_key"],
            "market_key": b["market_key"], "market": b["market"],
            "event": b["event"], "start": b["start"],
            "selection": b["selection"], "book": b["book"],
            "odds": b["odds"],                      # the price we published
            "fair": b["fair"],                      # our consensus at that moment
            "edge": round((b["odds"] / b["fair"] - 1) * 100, 2),
            "books": b.get("books", 0),             # how many firms the consensus rested on
            "detected": datetime.now(timezone.utc).isoformat(),
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "scanner_config": scanner_config(),
            "min_edge_at_detection": MIN_EDGE,
            "config": {"min_edge": MIN_EDGE, "min_books": MIN_BOOKS,
                       "max_odds": MAX_ODDS, "markets": list(MARKETS)},
            "close_status": "pending",
        })

    # Keep a running market price on every pick still waiting. This is useful for
    # tracking drift, but it is NOT the closing line: close.py freezes that near
    # the off and marks it close_final, which is the only version the record uses.
    now_iso = datetime.now(timezone.utc).isoformat()
    refreshed = 0
    for pid, pick in picks.items():
        event_id = pid.split("|")[0]
        key = f"{event_id}|{pick.get('market_key','h2h')}|{pick['selection']}"
        if key in market_prices and not pick.get("close_final"):
            pick["latest_fair"] = market_prices[key]
            pick["latest_seen"] = now_iso
            refreshed += 1

    feed = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "scanned": scanned,
        "bets": [{k: v for k, v in b.items() if k not in ("id", "sport_key", "market_key", "books")}
                 for b in bets],
        "results": load_json("results.json", []),
    }
    (OUT / "feed.json").write_text(json.dumps(feed, indent=1))
    (OUT / "picks.json").write_text(json.dumps(picks, indent=1))
    per_market = ", ".join(f"{k}: {v}" for k, v in sorted(scanned["by_market"].items()))
    found_per_market = {}
    for b in bets:
        found_per_market[b["market_key"]] = found_per_market.get(b["market_key"], 0) + 1
    print(f"{len(bets)} bets written, {len(picks)} picks tracked, "
          f"{refreshed} prices refreshed")
    print(f"  priced: {per_market}")
    print(f"  qualifying: {found_per_market or 'none'}")
    print(f"  model {MODEL_VERSION}, config {scanner_config()}")


if __name__ == "__main__":
    main()
