"""
Sports Betting Odds Tracker & Analyzer
=======================================
A local web app that tracks live sports betting odds,
detects arbitrage opportunities, calculates expected value,
and tracks line movement over time.

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
import json
import time
import heapq
from datetime import datetime, timezone
from collections import defaultdict
import threading
import os

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
API_KEY = os.environ.get("ODDS_API_KEY", "demo")   # set your key as env var
BASE_URL = "https://api.the-odds-api.com/v4"

SPORTS = {
    "americanfootball_nfl": "🏈 NFL",
    "basketball_nba": "🏀 NBA",
    "baseball_mlb": "⚾ MLB",
    "icehockey_nhl": "🏒 NHL",
    "soccer_epl": "⚽ EPL",
    "golf_pga_tour_winner": "⛳ PGA Tour",
}

BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "pointsbet", "barstool"]

# ─────────────────────────────────────────────────────────────
#  IN-MEMORY DATA STRUCTURES
# ─────────────────────────────────────────────────────────────

# Hash map: game_id -> game data (O(1) lookup)
games_db = {}

# Hash map: game_id -> list of (timestamp, odds_snapshot) for line movement
line_history = defaultdict(list)

# Stack (list used as stack): last 50 alerts
alerts_stack = []

# Min-heap for best EV bets: (-ev, game_id, bet_info)
ev_heap = []

# Cache timestamp
last_fetched = {}
cache_ttl = 60  # seconds


# ─────────────────────────────────────────────────────────────
#  DEMO DATA (used when no API key is set)
# ─────────────────────────────────────────────────────────────
def get_demo_data():
    """Return realistic demo data so the app works without an API key."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "id": "demo_001",
            "sport_key": "basketball_nba",
            "sport_title": "NBA",
            "commence_time": "2026-05-02T00:00:00Z",
            "home_team": "Boston Celtics",
            "away_team": "Miami Heat",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -165},
                    {"name": "Miami Heat", "price": 140}
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -170},
                    {"name": "Miami Heat", "price": 145}
                ]}]},
                {"key": "betmgm", "title": "BetMGM", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -160},
                    {"name": "Miami Heat", "price": 135}
                ]}]},
                {"key": "caesars", "title": "Caesars", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -168},
                    {"name": "Miami Heat", "price": 142}
                ]}]},
            ]
        },
        {
            "id": "demo_002",
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "commence_time": "2026-05-01T23:10:00Z",
            "home_team": "New York Yankees",
            "away_team": "Chicago Cubs",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -130},
                    {"name": "Chicago Cubs", "price": 110}
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -125},
                    {"name": "Chicago Cubs", "price": 105}
                ]}]},
                {"key": "betmgm", "title": "BetMGM", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -135},
                    {"name": "Chicago Cubs", "price": 115}
                ]}]},
                {"key": "caesars", "title": "Caesars", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -128},
                    {"name": "Chicago Cubs", "price": 108}
                ]}]},
            ]
        },
        {
            "id": "demo_003",
            "sport_key": "americanfootball_nfl",
            "sport_title": "NFL",
            "commence_time": "2026-05-03T18:00:00Z",
            "home_team": "Kansas City Chiefs",
            "away_team": "San Francisco 49ers",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Kansas City Chiefs", "price": -115},
                    {"name": "San Francisco 49ers", "price": -105}
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Kansas City Chiefs", "price": -110},
                    {"name": "San Francisco 49ers", "price": -110}
                ]}]},
                {"key": "betmgm", "title": "BetMGM", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Kansas City Chiefs", "price": -118},
                    {"name": "San Francisco 49ers", "price": 100}
                ]}]},
                {"key": "caesars", "title": "Caesars", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Kansas City Chiefs", "price": -112},
                    {"name": "San Francisco 49ers", "price": -108}
                ]}]},
            ]
        },
        {
            "id": "demo_004",
            "sport_key": "golf_pga_tour_winner",
            "sport_title": "PGA Tour",
            "commence_time": "2026-05-07T12:00:00Z",
            "home_team": "Scottie Scheffler",
            "away_team": "Field",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Scottie Scheffler", "price": 450},
                    {"name": "Rory McIlroy", "price": 650},
                    {"name": "Xander Schauffele", "price": 900},
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Scottie Scheffler", "price": 480},
                    {"name": "Rory McIlroy", "price": 700},
                    {"name": "Xander Schauffele", "price": 850},
                ]}]},
            ]
        },
        {
            "id": "demo_005",
            "sport_key": "icehockey_nhl",
            "sport_title": "NHL",
            "commence_time": "2026-05-01T23:00:00Z",
            "home_team": "Colorado Avalanche",
            "away_team": "Vegas Golden Knights",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Colorado Avalanche", "price": 122},
                    {"name": "Vegas Golden Knights", "price": -145}
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Colorado Avalanche", "price": 118},
                    {"name": "Vegas Golden Knights", "price": -140}
                ]}]},
                {"key": "betmgm", "title": "BetMGM", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Colorado Avalanche", "price": 125},   # arb opportunity!
                    {"name": "Vegas Golden Knights", "price": -138}
                ]}]},
            ]
        },
    ]


# ─────────────────────────────────────────────────────────────
#  ODDS MATH
# ─────────────────────────────────────────────────────────────
def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal odds."""
    if american > 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1

def decimal_to_implied_prob(decimal: float) -> float:
    """Convert decimal odds to implied probability (0-1)."""
    return 1 / decimal if decimal > 0 else 0

def calculate_ev(odds: int, true_prob: float) -> float:
    """
    Calculate Expected Value given American odds and true win probability.
    EV > 0 means profitable bet.
    """
    decimal = american_to_decimal(odds)
    win_amount = decimal - 1   # profit per $1 bet
    lose_amount = 1.0
    ev = (true_prob * win_amount) - ((1 - true_prob) * lose_amount)
    return round(ev * 100, 2)   # as percentage

def check_arbitrage(outcomes: list) -> dict:
    """
    Check if a set of outcomes has an arbitrage opportunity.
    Arbitrage exists when sum of implied probabilities < 1.
    Returns arb info if found, else None.
    """
    if len(outcomes) < 2:
        return None
    total_implied = sum(decimal_to_implied_prob(american_to_decimal(o["price"])) for o in outcomes)
    if total_implied < 1.0:
        profit_pct = round((1 - total_implied) * 100, 2)
        return {
            "is_arb": True,
            "total_implied": round(total_implied * 100, 2),
            "profit_pct": profit_pct,
            "outcomes": outcomes
        }
    return None

def get_best_odds_per_team(bookmakers: list) -> dict:
    """
    Use a max-heap (via negation) to find best odds per team across all books.
    Returns dict: team_name -> {price, book}
    """
    team_heaps = defaultdict(list)   # team -> heap of (-decimal, price, book)

    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    decimal = american_to_decimal(outcome["price"])
                    heapq.heappush(
                        team_heaps[outcome["name"]],
                        (-decimal, outcome["price"], book["title"])
                    )

    best = {}
    for team, heap in team_heaps.items():
        if heap:
            neg_dec, price, book = heap[0]   # best is at top (most negative = highest decimal)
            best[team] = {
                "price": price,
                "book": book,
                "decimal": round(-neg_dec, 3),
                "implied_prob": round(decimal_to_implied_prob(-neg_dec) * 100, 1)
            }
    return best

def process_game(raw_game: dict) -> dict:
    """Process raw API/demo game data into enriched game object."""
    game_id   = raw_game["id"]
    home      = raw_game["home_team"]
    away      = raw_game["away_team"]
    bookmakers = raw_game.get("bookmakers", [])

    # Best odds per team using heap
    best_odds = get_best_odds_per_team(bookmakers)

    # Build best combo for arbitrage check
    best_combo = [
        {"name": team, "price": info["price"], "book": info["book"]}
        for team, info in best_odds.items()
    ][:2]  # h2h only needs 2

    arb = check_arbitrage(best_combo) if len(best_combo) == 2 else None

    # Consensus implied prob (average across books) for EV calc
    team_probs = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    dec = american_to_decimal(outcome["price"])
                    team_probs[outcome["name"]].append(decimal_to_implied_prob(dec))

    # Normalize consensus probs (remove vig)
    consensus = {}
    raw_probs = {t: sum(ps)/len(ps) for t, ps in team_probs.items() if ps}
    total_raw = sum(raw_probs.values())
    if total_raw > 0:
        for team, prob in raw_probs.items():
            consensus[team] = prob / total_raw

    # EV for best odds
    ev_data = {}
    for team, info in best_odds.items():
        true_prob = consensus.get(team, 0.5)
        ev = calculate_ev(info["price"], true_prob)
        ev_data[team] = ev

    # All books odds table
    books_table = []
    for book in bookmakers:
        row = {"book": book["title"]}
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    row[outcome["name"]] = outcome["price"]
        books_table.append(row)

    # Sport emoji
    sport_key = raw_game.get("sport_key", "")
    sport_emoji = SPORTS.get(sport_key, "🏆")

    return {
        "id": game_id,
        "sport": raw_game.get("sport_title", sport_emoji),
        "sport_key": sport_key,
        "sport_emoji": sport_emoji,
        "home": home,
        "away": away,
        "commence_time": raw_game.get("commence_time", ""),
        "best_odds": best_odds,
        "arb": arb,
        "ev": ev_data,
        "books_table": books_table,
        "num_books": len(bookmakers),
    }


# ─────────────────────────────────────────────────────────────
#  DATA FETCHING
# ─────────────────────────────────────────────────────────────
def fetch_odds(sport_key: str = "basketball_nba") -> list:
    """Fetch odds from API or return demo data."""
    cache_key = sport_key
    now = time.time()

    if cache_key in last_fetched and now - last_fetched[cache_key] < cache_ttl:
        cached = [g for g in games_db.values() if g["sport_key"] == sport_key]
        if cached:
            return cached

    # Use demo data if no API key
    if API_KEY == "demo":
        raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key]
    else:
        try:
            url = f"{BASE_URL}/sports/{sport_key}/odds"
            params = {
                "apiKey": API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
                "bookmakers": ",".join(BOOKS),
            }
            resp = requests.get(url, params=params, timeout=10)
            raw_games = resp.json() if resp.status_code == 200 else []
        except Exception:
            raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key]

    processed = []
    for raw in raw_games:
        game = process_game(raw)
        games_db[game["id"]] = game

        # Record line history (sorted list by timestamp)
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "best_odds": game["best_odds"]
        }
        line_history[game["id"]].append(snapshot)
        if len(line_history[game["id"]]) > 20:
            line_history[game["id"]].pop(0)

        # Push arb alert to stack
        if game["arb"]:
            alert = {
                "type": "ARB",
                "game": f"{game['away']} @ {game['home']}",
                "profit": game["arb"]["profit_pct"],
                "time": datetime.now().strftime("%H:%M:%S")
            }
            alerts_stack.append(alert)  # push
            if len(alerts_stack) > 50:
                alerts_stack.pop(0)     # keep stack bounded

        processed.append(game)

    last_fetched[cache_key] = now
    return processed


def fetch_all_sports():
    """Fetch odds for all sports."""
    all_games = []
    for sport_key in SPORTS:
        games = fetch_odds(sport_key)
        all_games.extend(games)
    return all_games


# ─────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", sports=SPORTS)

@app.route("/api/games")
def api_games():
    sport = request.args.get("sport", "all")
    if sport == "all":
        games = fetch_all_sports()
    else:
        games = fetch_odds(sport)
    return jsonify({"games": games, "count": len(games), "demo": API_KEY == "demo"})

@app.route("/api/game/<game_id>")
def api_game(game_id):
    game = games_db.get(game_id)
    if not game:
        return jsonify({"error": "Game not found"}), 404
    history = line_history.get(game_id, [])
    return jsonify({"game": game, "history": history})

@app.route("/api/alerts")
def api_alerts():
    # Pop from stack (LIFO) — show most recent first
    return jsonify({"alerts": list(reversed(alerts_stack[-10:]))})

@app.route("/api/best_ev")
def api_best_ev():
    """Return top EV bets across all games using heap."""
    fetch_all_sports()
    ev_bets = []
    for game in games_db.values():
        for team, ev in game.get("ev", {}).items():
            if ev > 0:
                best = game["best_odds"].get(team, {})
                ev_bets.append({
                    "game": f"{game['away']} @ {game['home']}",
                    "team": team,
                    "ev": ev,
                    "odds": best.get("price", 0),
                    "book": best.get("book", ""),
                    "sport": game["sport"],
                })
    # Sort by EV descending using heap
    heap = [(-b["ev"], i, b) for i, b in enumerate(ev_bets)]
    heapq.heapify(heap)
    top = []
    while heap and len(top) < 10:
        _, _, bet = heapq.heappop(heap)
        top.append(bet)
    return jsonify({"top_ev": top})

@app.route("/api/arbitrage")
def api_arbitrage():
    fetch_all_sports()
    arbs = [g for g in games_db.values() if g.get("arb")]
    return jsonify({"arbs": arbs, "count": len(arbs)})

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  🎰  Sports Betting Odds Tracker")
    print("="*50)
    print(f"  Mode: {'DEMO (sample data)' if API_KEY == 'demo' else 'LIVE API'}")
    print("  Opening at: http://localhost:5000")
    print("  Press Ctrl+C to stop")
    print("="*50 + "\n")
    app.run(debug=False, port=5000)
