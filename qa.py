"""
qa.py — the weekly health check on the validation run.

Reads results.json and picks.json and answers the questions that decide whether
the evidence is trustworthy, before anyone starts drawing conclusions from it.
Costs nothing: no API calls, no credits.

Run:  python qa.py           (from the folder holding the JSON files)
"""
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(".")


def load(name, default):
    try:
        return json.loads((OUT / name).read_text())
    except Exception:
        return default


def median(values):
    return statistics.median(values) if values else None


def pct(part, whole):
    return f"{100*part/whole:.0f}%" if whole else "n/a"


def clv_of(r):
    return (r["odds"] / r["close"] - 1) * 100 if r.get("close", 0) > 0 else None


def band_of(edge):
    if edge < 2.5: return "1.5-2.5%"
    if edge < 4: return "2.5-4%"
    if edge < 6: return "4-6%"
    return "6%+"


def heading(text):
    print("\n" + text)
    print("-" * len(text))


def main():
    results = load("results.json", [])
    picks = load("picks.json", {})

    print(f"Settled selections: {len(results)}")
    print(f"Still open: {len(picks)}")
    if not results:
        print("\nNothing settled yet. Come back once events have finished.")
        return

    # ---- closing snapshot reliability -------------------------------------
    heading("Closing snapshots")
    statuses = Counter(r.get("close_status", "unknown") for r in results)
    final = statuses.get("final", 0)
    print(f"Verified: {final} of {len(results)} ({pct(final, len(results))})")
    for status, n in statuses.most_common():
        if status != "final":
            print(f"  {status.replace('_',' ')}: {n}")

    mins = [r["close_minutes"] for r in results if r.get("close_minutes") is not None]
    books = [r["close_books"] for r in results if r.get("close_books")]
    if mins:
        print(f"Minutes before the off: median {median(mins):.1f}, "
              f"range {min(mins):.0f} to {max(mins):.0f}")
        drift = [m for m in mins if m > 12]
        if drift:
            print(f"  {len(drift)} landed before T-12, so the cron may be drifting late")
    if books:
        print(f"Bookmakers in the closing consensus: median {median(books):.0f}, "
              f"lowest {min(books)}")

    # ---- duplicates --------------------------------------------------------
    heading("Duplicates")
    seen = Counter((r["date"], r["selection"], r.get("market",""), r["book"]) for r in results)
    dupes = {k: n for k, n in seen.items() if n > 1}
    if dupes:
        print(f"{len(dupes)} repeated selections, which should not happen:")
        for (d, sel, mkt, book), n in list(dupes.items())[:8]:
            print(f"  {d} {sel} ({mkt}) at {book} x{n}")
    else:
        print("None. Every settled selection is unique.")

    # ---- settlement joins --------------------------------------------------
    heading("Settlement integrity")
    missing = [r for r in results if not r.get("book") or not r.get("odds")]
    noedge = [r for r in results if r.get("edge") in (None, 0)]
    print(f"Records missing price or bookmaker: {len(missing)}")
    print(f"Records missing the original edge: {len(noedge)}"
          + ("  (expect a few from before version 10)" if noedge else ""))
    odd = [r for r in results if r.get("close", 0) and
           (r["close"] < 1.01 or r["odds"] < 1.01 or abs(clv_of(r)) > 60)]
    print(f"Implausible prices or CLV beyond ±60%: {len(odd)}")
    for r in odd[:5]:
        print(f"  {r['date']} {r['selection']}: took {r['odds']}, closed {r['close']}")

    # ---- the numbers that matter ------------------------------------------
    with_close = [r for r in results if r.get("close", 0) > 0]
    if not with_close:
        print("\nNo verified closing prices yet, so CLV cannot be measured.")
        return

    heading("Closing line value")
    clvs = [clv_of(r) for r in with_close]
    beat = sum(1 for c in clvs if c > 0)
    print(f"Average CLV: {statistics.mean(clvs):+.2f}%   median {statistics.median(clvs):+.2f}%")
    print(f"Beat the close: {beat} of {len(clvs)} ({pct(beat, len(clvs))})")
    extremes = sorted(clvs, reverse=True)[:3]
    print(f"Three largest: {', '.join(f'{c:+.1f}%' for c in extremes)}")
    trimmed = sorted(clvs)[1:-1] if len(clvs) > 6 else clvs
    print(f"Average excluding the best and worst: {statistics.mean(trimmed):+.2f}%")

    # ---- does claimed edge predict CLV? -----------------------------------
    heading("Does claimed edge predict CLV?")
    bands = defaultdict(list)
    for r in with_close:
        if r.get("edge"):
            bands[band_of(r["edge"])].append(clv_of(r))
    if bands:
        for band in ["1.5-2.5%", "2.5-4%", "4-6%", "6%+"]:
            if band in bands:
                vals = bands[band]
                print(f"  {band:>9}: {len(vals):3d} bets, average CLV {statistics.mean(vals):+.2f}%")
        print("\nIf CLV rises with claimed edge, the pricing is calibrated.")
        print("If every band lands near the same number, the edge figure is noise.")
    else:
        print("No edge figures recorded yet.")

    # ---- cuts for later ----------------------------------------------------
    for label, key in (("By market", "market"), ("By league", "league"),
                       ("By bookmaker", "book"), ("By model version", "model_version")):
        groups = defaultdict(list)
        for r in with_close:
            if r.get(key):
                groups[r[key]].append(clv_of(r))
        if len(groups) > 1:
            heading(label)
            for name, vals in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]:
                print(f"  {name[:28]:<28} {len(vals):3d} bets  CLV {statistics.mean(vals):+.2f}%")

    heading("Level stakes")
    stake = 10
    profit = sum(stake * (r["odds"] - 1) if r["won"] else -stake for r in results)
    print(f"£{stake} per bet: {len(results)} bets, "
          f"{'+' if profit >= 0 else '-'}£{abs(profit):.2f}, "
          f"ROI {100*profit/(stake*len(results)):+.1f}%")
    print("Treat this as secondary. CLV tells you the same thing far sooner.")


if __name__ == "__main__":
    main()
