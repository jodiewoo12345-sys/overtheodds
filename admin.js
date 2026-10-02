/*
 * admin.js — development tools, kept out of the public page.
 *
 * Loaded only when the page is opened with ?admin=1 or the masthead is tapped
 * five times. None of this appears in the source of index.html, so the site
 * never ships invented selections alongside real ones.
 *
 * If this file isn't deployed, the unlock simply does nothing.
 */
(function () {
  "use strict";
  var api = window.__otoAdmin;
  if (!api) return;

  var el = api.el, esc = api.esc, SPORTS = api.SPORTS, prefs = api.prefs;

  function panel() {
    if (el("admin")) return;
    el("adminSlot").innerHTML =
      '<section id="admin"><h2>Feed<span>local only</span></h2><div class="panel">' +
      '<p>Paste a scanner feed to preview it in this browser. Nothing here touches ' +
      'the live site, and reloading returns to live prices.</p>' +
      '<textarea id="feedBox" spellcheck="false"></textarea>' +
      '<div class="btnrow">' +
        '<button class="btn" id="loadBtn" type="button">Load feed</button>' +
        '<button class="btn line" id="demoBtn" type="button">Demo data</button>' +
        '<button class="btn line" id="copyBtn" type="button">Show current feed</button>' +
      '</div>' +
      '<p style="margin-top:10px">Free sport <select id="freePick"></select></p>' +
      '<p class="msg" id="msg"></p></div></section>';
    wire();
  }

  function wire() {
    el("loadBtn").onclick = function () {
      try {
        var incoming = JSON.parse(el("feedBox").value);
        if (!incoming || !Array.isArray(incoming.bets)) throw new Error("no bets");
        var checked = api.validate(incoming.bets);
        api.setFeed({
          updated: incoming.updated || new Date().toISOString(),
          bets: checked.bets,
          results: Array.isArray(incoming.results) ? incoming.results : [],
          scanned: incoming.scanned || null
        });
        api.refresh();
        el("msg").textContent = checked.bets.length + " bets and " +
          (incoming.results ? incoming.results.length : 0) + " results loaded" +
          (checked.dropped ? ", " + checked.dropped + " malformed records ignored" : "") + ".";
      } catch (err) {
        el("msg").textContent = "That didn't parse. Send the whole object with a bets array.";
      }
    };

    el("demoBtn").onclick = function () {
      var demo = sample();
      api.setFeed(demo);
      api.refresh();
      el("msg").textContent = "Demo data loaded into this browser only. Reload for live prices.";
    };

    el("copyBtn").onclick = function () {
      el("feedBox").value = JSON.stringify(api.current(), null, 1);
      el("feedBox").select();
      el("msg").textContent = "Current feed shown above.";
    };

    el("freePick").innerHTML = ["All"].concat(SPORTS).map(function (x) {
      return '<option' + (x === prefs.free ? ' selected' : '') + '>' + esc(x) + '</option>';
    }).join("");
    el("freePick").onchange = function () {
      prefs.free = this.value;
      api.save();
      api.refresh();
    };
  }

  /* Demo data for checking layout changes. Never reaches a visitor. */
  function sample() {
    var h = 3600000, now = Date.now();
    function b(sport, event, hrs, market, selection, book, odds, fair) {
      return { sport: sport, event: event, market: market,
               start: new Date(now + hrs * h).toISOString(),
               selection: selection, book: book, odds: odds, fair: fair,
               link: "https://example.com/" };
    }
    var bets = [
      b("Football", "Brentford v Crystal Palace", 5, "Match odds", "Crystal Palace", "Sky Bet", 4.20, 3.98),
      b("Football", "Real Sociedad v Girona", 27, "Match odds", "Draw", "Paddy Power", 3.90, 3.78),
      b("Football", "Bologna v Lazio", 52, "Totals", "Over 2.5", "Unibet", 2.15, 2.04),
      b("Tennis", "Draper v Fritz", 9, "Match odds", "Draper", "Sky Bet", 1.95, 1.88),
      b("Basketball", "Celtics v Knicks", 23, "Handicap", "Knicks +4.5", "BetVictor", 1.98, 1.90)
    ];
    var pool = ["Football", "Tennis", "Basketball"];
    var marketPool = ["Match odds", "Totals"];
    var bookPool = ["Sky Bet", "Paddy Power", "William Hill", "BetVictor", "Unibet", "Betfred"];
    var res = [], seed = 7;
    function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
    for (var i = 0; i < 120; i++) {
      var odds = 1.6 + rnd() * 4.4;
      var edge = 1.5 + rnd() * 6;
      var close = odds / (1 + edge / 100 * (0.2 + rnd() * 0.9));
      var failed = rnd() < 0.12;
      var d = new Date(now - (i * 2 + Math.floor(rnd() * 2)) * 864e5);
      res.push({
        sport: pool[Math.floor(rnd() * pool.length)],
        market: marketPool[Math.floor(rnd() * marketPool.length)],
        date: d.toISOString().slice(0, 10),
        selection: "Selection " + (i + 1),
        book: bookPool[Math.floor(rnd() * bookPool.length)],
        odds: Math.round(odds * 100) / 100,
        fair: Math.round(odds / (1 + edge / 100) * 100) / 100,
        edge: Math.round(edge * 100) / 100,
        close: failed ? 0 : Math.round(close * 100) / 100,
        close_status: failed ? "insufficient_books" : "final",
        close_books: failed ? 0 : 5 + Math.floor(rnd() * 7),
        close_minutes: failed ? null : Math.round((5 + rnd() * 7) * 10) / 10,
        model_version: "v10",
        won: rnd() < 1 / odds
      });
    }
    return { updated: new Date(now).toISOString(), bets: bets, results: res };
  }

  panel();
  api.show("yours");
  var box = el("admin");
  if (box) box.scrollIntoView({ behavior: "smooth" });
})();
