"""
Sports Betting Odds Tracker & Analyzer
=======================================
A local web app that tracks live sports betting odds,
detects arbitrage opportunities, calculates expected value,
tracks line movement, and shows team efficiency stats.

Data Structures used:
- Hash Map (dict): O(1) game/odds lookup by ID
- Min-Heap (heapq): priority queue for best odds ranking
- Sorted List: line movement history per game
- Stack: recent alert history

Run with: python3 app.py
Then open: http://localhost:5000
"""

from flask import Flask, render_template, jsonify, request
import requests
import time
import heapq
from datetime import datetime, timezone
from collections import defaultdict
import os
import random

app = Flask(__name__)

API_KEY  = os.environ.get("ODDS_API_KEY", "demo")
BASE_URL = "https://api.the-odds-api.com/v4"

SPORTS = {
    "americanfootball_nfl": "🏈 NFL",
    "basketball_nba":       "🏀 NBA",
    "baseball_mlb":         "⚾ MLB",
    "icehockey_nhl":        "🏒 NHL",
    "soccer_epl":           "⚽ EPL",
    "golf_pga_tour_winner": "⛳ PGA Tour",
}

BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "pointsbet", "barstool"]

# ─────────────────────────────────────────────────────────────
#  IN-MEMORY DATA STRUCTURES
# ─────────────────────────────────────────────────────────────
games_db      = {}
line_history  = defaultdict(list)
alerts_stack  = []
last_fetched  = {}
cache_ttl     = 60

# ─────────────────────────────────────────────────────────────
#  TEAM STATS DATABASE
# ─────────────────────────────────────────────────────────────
TEAM_STATS = {
    "Boston Celtics":       {"off_rtg": 122.4, "def_rtg": 108.2, "form": ["W","W","W","L","W"], "pace": 99.1,  "h2h": {"Miami Heat":            {"wins": 8, "losses": 3}}},
    "Miami Heat":           {"off_rtg": 111.3, "def_rtg": 112.8, "form": ["L","W","L","L","W"], "pace": 96.4,  "h2h": {"Boston Celtics":         {"wins": 3, "losses": 8}}},
    "Kansas City Chiefs":   {"off_rtg": 28.4,  "def_rtg": 18.2,  "form": ["W","W","L","W","W"], "pace": None,  "h2h": {"San Francisco 49ers":    {"wins": 4, "losses": 2}}},
    "San Francisco 49ers":  {"off_rtg": 26.1,  "def_rtg": 19.8,  "form": ["W","L","W","W","L"], "pace": None,  "h2h": {"Kansas City Chiefs":     {"wins": 2, "losses": 4}}},
    "New York Yankees":     {"off_rtg": 5.12,  "def_rtg": 3.88,  "form": ["W","W","W","L","W"], "pace": None,  "h2h": {"Chicago Cubs":           {"wins": 12,"losses": 8}}},
    "Chicago Cubs":         {"off_rtg": 4.55,  "def_rtg": 4.21,  "form": ["L","L","W","L","W"], "pace": None,  "h2h": {"New York Yankees":       {"wins": 8, "losses": 12}}},
    "Colorado Avalanche":   {"off_rtg": 3.42,  "def_rtg": 2.98,  "form": ["W","L","W","W","L"], "pace": None,  "h2h": {"Vegas Golden Knights":   {"wins": 5, "losses": 7}}},
    "Vegas Golden Knights": {"off_rtg": 3.61,  "def_rtg": 2.74,  "form": ["W","W","W","L","W"], "pace": None,  "h2h": {"Colorado Avalanche":     {"wins": 7, "losses": 5}}},
    "Scottie Scheffler":    {"off_rtg": 71.2,  "def_rtg": None,  "form": ["W","T","W","W","L"], "pace": None,  "h2h": {}},
    "Rory McIlroy":         {"off_rtg": 69.8,  "def_rtg": None,  "form": ["L","W","W","L","W"], "pace": None,  "h2h": {}},
    "Xander Schauffele":    {"off_rtg": 70.1,  "def_rtg": None,  "form": ["W","L","L","W","W"], "pace": None,  "h2h": {}},
}

def get_team_stats(team_name: str) -> dict:
    if team_name in TEAM_STATS:
        return TEAM_STATS[team_name]
    seed = sum(ord(c) for c in team_name)
    rng  = random.Random(seed)
    return {
        "off_rtg": round(rng.uniform(105, 120), 1),
        "def_rtg": round(rng.uniform(105, 118), 1),
        "form":    [rng.choice(["W","W","L"]) for _ in range(5)],
        "pace":    round(rng.uniform(95, 102), 1),
        "h2h":     {}
    }

def form_score(form: list) -> float:
    weights = [0.35, 0.25, 0.20, 0.12, 0.08]
    score   = 0.0
    for i, result in enumerate(form[:5]):
        w = weights[i] if i < len(weights) else 0.05
        if result == "W":   score += w
        elif result == "T": score += w * 0.5
    return round(score, 3)

def efficiency_edge(s1: dict, s2: dict) -> float:
    try:
        off_edge = (s1["off_rtg"] - s2["off_rtg"]) / max(s1["off_rtg"], 1)
        def_edge = (s2["def_rtg"] - s1["def_rtg"]) / max(s2["def_rtg"], 1) if s1.get("def_rtg") and s2.get("def_rtg") else 0
        return round((off_edge + def_edge) / 2, 4)
    except Exception:
        return 0.0

def h2h_edge(team1: str, team2: str, stats: dict) -> float:
    h2h    = stats.get("h2h", {}).get(team2, {})
    wins   = h2h.get("wins", 0)
    losses = h2h.get("losses", 0)
    total  = wins + losses
    return round(wins / total, 3) if total > 0 else 0.5

def smart_win_probability(team1: str, team2: str, odds_prob: float) -> dict:
    s1 = get_team_stats(team1)
    s2 = get_team_stats(team2)

    eff       = efficiency_edge(s1, s2)
    form1     = form_score(s1.get("form", []))
    form2     = form_score(s2.get("form", []))
    h2h       = h2h_edge(team1, team2, s1)
    eff_prob  = (eff + 1) / 2
    form_prob = form1 / (form1 + form2) if (form1 + form2) > 0 else 0.5

    smart = (odds_prob * 0.50 + eff_prob * 0.25 + form_prob * 0.15 + h2h * 0.10)
    smart = max(0.05, min(0.95, smart))

    h2h_rec = s1.get("h2h", {}).get(team2, {})
    return {
        "smart_prob":  round(smart    * 100, 1),
        "odds_prob":   round(odds_prob* 100, 1),
        "eff_prob":    round(eff_prob * 100, 1),
        "form_prob":   round(form_prob* 100, 1),
        "h2h_prob":    round(h2h      * 100, 1),
        "form":        s1.get("form", []),
        "off_rtg":     s1.get("off_rtg"),
        "def_rtg":     s1.get("def_rtg"),
        "h2h_wins":    h2h_rec.get("wins", 0),
        "h2h_losses":  h2h_rec.get("losses", 0),
    }

# ─────────────────────────────────────────────────────────────
#  ODDS MATH
# ─────────────────────────────────────────────────────────────
def american_to_decimal(american: int) -> float:
    if american > 0: return (american / 100) + 1
    return (100 / abs(american)) + 1

def decimal_to_implied_prob(decimal: float) -> float:
    return 1 / decimal if decimal > 0 else 0

def calculate_ev(odds: int, true_prob: float) -> float:
    decimal = american_to_decimal(odds)
    ev = (true_prob * (decimal - 1)) - ((1 - true_prob) * 1.0)
    return round(ev * 100, 2)

def check_arbitrage(outcomes: list):
    if len(outcomes) < 2: return None
    total = sum(decimal_to_implied_prob(american_to_decimal(o["price"])) for o in outcomes)
    if total < 1.0:
        return {"is_arb": True, "total_implied": round(total*100,2),
                "profit_pct": round((1-total)*100,2), "outcomes": outcomes}
    return None

def get_best_odds_per_team(bookmakers: list) -> dict:
    team_heaps = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    dec = american_to_decimal(outcome["price"])
                    heapq.heappush(team_heaps[outcome["name"]], (-dec, outcome["price"], book["title"]))
    best = {}
    for team, heap in team_heaps.items():
        if heap:
            neg_dec, price, book = heap[0]
            best[team] = {"price": price, "book": book,
                          "decimal": round(-neg_dec,3),
                          "implied_prob": round(decimal_to_implied_prob(-neg_dec)*100,1)}
    return best

def process_game(raw: dict) -> dict:
    gid        = raw["id"]
    bookmakers = raw.get("bookmakers", [])
    best_odds  = get_best_odds_per_team(bookmakers)
    teams      = list(best_odds.keys())[:2]

    arb = check_arbitrage([{"name":t,"price":best_odds[t]["price"],"book":best_odds[t]["book"]} for t in teams]) if len(teams)==2 else None

    team_probs = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    team_probs[outcome["name"]].append(decimal_to_implied_prob(american_to_decimal(outcome["price"])))

    raw_probs = {t: sum(ps)/len(ps) for t,ps in team_probs.items() if ps}
    total_raw = sum(raw_probs.values())
    consensus = {t: p/total_raw for t,p in raw_probs.items()} if total_raw>0 else {}

    ev_data     = {t: calculate_ev(best_odds[t]["price"], consensus.get(t,0.5)) for t in best_odds}
    smart_probs = {}
    if len(teams) >= 2:
        t1,t2 = teams[0], teams[1]
        smart_probs[t1] = smart_win_probability(t1, t2, consensus.get(t1, 0.5))
        smart_probs[t2] = smart_win_probability(t2, t1, consensus.get(t2, 0.5))

    books_table = []
    for book in bookmakers:
        row = {"book": book["title"]}
        for market in book.get("markets",[]):
            if market["key"]=="h2h":
                for o in market["outcomes"]: row[o["name"]] = o["price"]
        books_table.append(row)

    favorite = None
    if len(teams) >= 2:
        favorite = teams[0] if consensus.get(teams[0],0) >= consensus.get(teams[1],0) else teams[1]

    return {
        "id": gid, "sport": raw.get("sport_title",""), "sport_key": raw.get("sport_key",""),
        "home": raw["home_team"], "away": raw["away_team"],
        "commence_time": raw.get("commence_time",""),
        "best_odds": best_odds, "arb": arb, "ev": ev_data,
        "smart_probs": smart_probs, "books_table": books_table,
        "num_books": len(bookmakers), "favorite": favorite,
        "consensus": {t: round(p*100,1) for t,p in consensus.items()},
    }

# ─────────────────────────────────────────────────────────────
#  DEMO DATA
# ─────────────────────────────────────────────────────────────
def get_demo_data():
    return [
        {"id":"demo_001","sport_key":"basketball_nba","sport_title":"NBA","commence_time":"2026-05-02T00:00:00Z","home_team":"Boston Celtics","away_team":"Miami Heat","bookmakers":[
            {"key":"draftkings","title":"DraftKings","markets":[{"key":"h2h","outcomes":[{"name":"Boston Celtics","price":-165},{"name":"Miami Heat","price":140}]}]},
            {"key":"fanduel","title":"FanDuel","markets":[{"key":"h2h","outcomes":[{"name":"Boston Celtics","price":-170},{"name":"Miami Heat","price":145}]}]},
            {"key":"betmgm","title":"BetMGM","markets":[{"key":"h2h","outcomes":[{"name":"Boston Celtics","price":-160},{"name":"Miami Heat","price":135}]}]},
            {"key":"caesars","title":"Caesars","markets":[{"key":"h2h","outcomes":[{"name":"Boston Celtics","price":-168},{"name":"Miami Heat","price":142}]}]},
        ]},
        {"id":"demo_002","sport_key":"baseball_mlb","sport_title":"MLB","commence_time":"2026-05-01T23:10:00Z","home_team":"New York Yankees","away_team":"Chicago Cubs","bookmakers":[
            {"key":"draftkings","title":"DraftKings","markets":[{"key":"h2h","outcomes":[{"name":"New York Yankees","price":-130},{"name":"Chicago Cubs","price":110}]}]},
            {"key":"fanduel","title":"FanDuel","markets":[{"key":"h2h","outcomes":[{"name":"New York Yankees","price":-125},{"name":"Chicago Cubs","price":105}]}]},
            {"key":"betmgm","title":"BetMGM","markets":[{"key":"h2h","outcomes":[{"name":"New York Yankees","price":-135},{"name":"Chicago Cubs","price":115}]}]},
            {"key":"caesars","title":"Caesars","markets":[{"key":"h2h","outcomes":[{"name":"New York Yankees","price":-128},{"name":"Chicago Cubs","price":108}]}]},
        ]},
        {"id":"demo_003","sport_key":"americanfootball_nfl","sport_title":"NFL","commence_time":"2026-05-03T18:00:00Z","home_team":"Kansas City Chiefs","away_team":"San Francisco 49ers","bookmakers":[
            {"key":"draftkings","title":"DraftKings","markets":[{"key":"h2h","outcomes":[{"name":"Kansas City Chiefs","price":-115},{"name":"San Francisco 49ers","price":-105}]}]},
            {"key":"fanduel","title":"FanDuel","markets":[{"key":"h2h","outcomes":[{"name":"Kansas City Chiefs","price":-110},{"name":"San Francisco 49ers","price":-110}]}]},
            {"key":"betmgm","title":"BetMGM","markets":[{"key":"h2h","outcomes":[{"name":"Kansas City Chiefs","price":-118},{"name":"San Francisco 49ers","price":100}]}]},
            {"key":"caesars","title":"Caesars","markets":[{"key":"h2h","outcomes":[{"name":"Kansas City Chiefs","price":-112},{"name":"San Francisco 49ers","price":-108}]}]},
        ]},
        {"id":"demo_004","sport_key":"golf_pga_tour_winner","sport_title":"PGA Tour","commence_time":"2026-05-07T12:00:00Z","home_team":"Scottie Scheffler","away_team":"Field","bookmakers":[
            {"key":"draftkings","title":"DraftKings","markets":[{"key":"h2h","outcomes":[{"name":"Scottie Scheffler","price":450},{"name":"Rory McIlroy","price":650},{"name":"Xander Schauffele","price":900}]}]},
            {"key":"fanduel","title":"FanDuel","markets":[{"key":"h2h","outcomes":[{"name":"Scottie Scheffler","price":480},{"name":"Rory McIlroy","price":700},{"name":"Xander Schauffele","price":850}]}]},
        ]},
        {"id":"demo_005","sport_key":"icehockey_nhl","sport_title":"NHL","commence_time":"2026-05-01T23:00:00Z","home_team":"Colorado Avalanche","away_team":"Vegas Golden Knights","bookmakers":[
            {"key":"draftkings","title":"DraftKings","markets":[{"key":"h2h","outcomes":[{"name":"Colorado Avalanche","price":122},{"name":"Vegas Golden Knights","price":-145}]}]},
            {"key":"fanduel","title":"FanDuel","markets":[{"key":"h2h","outcomes":[{"name":"Colorado Avalanche","price":118},{"name":"Vegas Golden Knights","price":-140}]}]},
            {"key":"betmgm","title":"BetMGM","markets":[{"key":"h2h","outcomes":[{"name":"Colorado Avalanche","price":125},{"name":"Vegas Golden Knights","price":-138}]}]},
        ]},
    ]

def fetch_odds(sport_key: str) -> list:
    now = time.time()
    if sport_key in last_fetched and now - last_fetched[sport_key] < cache_ttl:
        cached = [g for g in games_db.values() if g["sport_key"] == sport_key]
        if cached: return cached

    raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key] if API_KEY == "demo" else []
    if not raw_games and API_KEY != "demo":
        try:
            resp = requests.get(f"{BASE_URL}/sports/{sport_key}/odds",
                params={"apiKey":API_KEY,"regions":"us","markets":"h2h","oddsFormat":"american","bookmakers":",".join(BOOKS)}, timeout=10)
            raw_games = resp.json() if resp.status_code == 200 else []
        except: raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key]

    processed = []
    for raw in raw_games:
        game = process_game(raw)
        games_db[game["id"]] = game
        line_history[game["id"]].append({"timestamp": datetime.now().isoformat(), "best_odds": game["best_odds"]})
        if len(line_history[game["id"]]) > 20: line_history[game["id"]].pop(0)
        if game["arb"]:
            alerts_stack.append({"type":"ARB","game":f"{game['away']} @ {game['home']}","profit":game["arb"]["profit_pct"],"time":datetime.now().strftime("%H:%M:%S")})
            if len(alerts_stack) > 50: alerts_stack.pop(0)
        processed.append(game)

    last_fetched[sport_key] = now
    return processed

def fetch_all_sports():
    all_games = []
    for sk in SPORTS: all_games.extend(fetch_odds(sk))
    return all_games

# ─────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", sports=SPORTS)

@app.route("/api/games")
def api_games():
    sport     = request.args.get("sport", "all")
    sort_by   = request.args.get("sort", "default")
    filter_by = request.args.get("filter", "all")

    games = fetch_all_sports() if sport == "all" else fetch_odds(sport)

    if filter_by == "arb":    games = [g for g in games if g.get("arb")]
    elif filter_by == "posev": games = [g for g in games if any(v > 0 for v in g.get("ev",{}).values())]
    elif filter_by == "favorite": games = [g for g in games if g.get("favorite")]
    elif filter_by == "underdog": games = [g for g in games if any(v > 0 for t,v in g.get("ev",{}).items() if t != g.get("favorite"))]

    if sort_by == "ev":    games.sort(key=lambda g: max(g.get("ev",{}).values() or [0]), reverse=True)
    elif sort_by == "arb": games.sort(key=lambda g: g["arb"]["profit_pct"] if g.get("arb") else 0, reverse=True)
    elif sort_by == "prob":
        games.sort(key=lambda g: max((v.get("smart_prob",0) for v in g.get("smart_probs",{}).values()), default=0), reverse=True)

    return jsonify({"games": games, "count": len(games), "demo": API_KEY == "demo"})

@app.route("/api/game/<game_id>")
def api_game(game_id):
    game = games_db.get(game_id)
    if not game: return jsonify({"error": "not found"}), 404
    return jsonify({"game": game, "history": line_history.get(game_id, [])})

@app.route("/api/alerts")
def api_alerts():
    return jsonify({"alerts": list(reversed(alerts_stack[-10:]))})

@app.route("/api/best_ev")
def api_best_ev():
    fetch_all_sports()
    ev_bets = []
    for game in games_db.values():
        for team, ev in game.get("ev",{}).items():
            if ev > 0:
                best = game["best_odds"].get(team,{})
                sp   = game.get("smart_probs",{}).get(team,{})
                ev_bets.append({"game":f"{game['away']} @ {game['home']}","team":team,"ev":ev,
                                "odds":best.get("price",0),"book":best.get("book",""),
                                "sport":game["sport"],"smart_prob":sp.get("smart_prob",0),"form":sp.get("form",[])})
    heap = [(-b["ev"],i,b) for i,b in enumerate(ev_bets)]
    heapq.heapify(heap)
    top = []
    while heap and len(top)<10: _, _, bet = heapq.heappop(heap); top.append(bet)
    return jsonify({"top_ev": top})

@app.route("/api/arbitrage")
def api_arbitrage():
    fetch_all_sports()
    return jsonify({"arbs": [g for g in games_db.values() if g.get("arb")]})

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  🎰  Sports Betting Odds Tracker")
    print("="*50)
    print(f"  Mode: {'DEMO' if API_KEY=='demo' else 'LIVE'}")
    print("  http://localhost:5000")
    print("  Ctrl+C to stop\n")
    app.run(debug=False, port=5000)
