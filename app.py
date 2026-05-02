"""
EdgeFinder — Sports Betting Odds Tracker
=========================================
Three features that actually give bettors value:

1. Best Line Finder — find the best available odds across all books
2. Arbitrage Calculator — exact dollar amounts for guaranteed profit
3. Odds Comparison — clean side-by-side book comparison

Data Structures:
- Hash Map: O(1) game lookup by ID
- Min-Heap: best odds ranking across books
- Stack: arbitrage alert history (LIFO)

Run: python3 app.py → http://localhost:5000
"""

from flask import Flask, render_template, jsonify, request
import requests
import time
import heapq
from datetime import datetime
from collections import defaultdict
import os

app = Flask(__name__)

API_KEY  = os.environ.get("ODDS_API_KEY", "demo")
BASE_URL = "https://api.the-odds-api.com/v4"

SPORTS = {
    "basketball_nba":       "🏀 NBA",
    "baseball_mlb":         "⚾ MLB",
    "golf_pga_tour_winner": "⛳ PGA Tour",
    "icehockey_nhl":        "🏒 NHL",
    "americanfootball_nfl": "🏈 NFL",
}

BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "pointsbet", "espnbet"]

# ─────────────────────────────────────────────────────────────
#  DATA STRUCTURES
# ─────────────────────────────────────────────────────────────
games_db     = {}        # Hash map: game_id -> processed game
arb_stack    = []        # Stack: arb alerts LIFO
last_fetched = {}
cache_ttl    = 60        # seconds


# ─────────────────────────────────────────────────────────────
#  ODDS MATH
# ─────────────────────────────────────────────────────────────
def american_to_decimal(p: int) -> float:
    return (p / 100) + 1 if p > 0 else (100 / abs(p)) + 1

def decimal_to_prob(d: float) -> float:
    return 1 / d if d > 0 else 0

def american_to_implied_prob(p: int) -> float:
    return decimal_to_prob(american_to_decimal(p))

def calculate_ev(odds: int, true_prob: float) -> float:
    d  = american_to_decimal(odds)
    ev = (true_prob * (d - 1)) - ((1 - true_prob) * 1.0)
    return round(ev * 100, 2)

def arb_bet_amounts(outcomes: list, total_stake: float = 100) -> list:
    """
    Calculate exact bet amounts for each leg of an arbitrage.
    Each amount = (total_stake / decimal_odds) / sum(1/decimal_odds for all legs)
    Returns list of {team, book, odds, stake, profit} dicts.
    """
    decimals = [american_to_decimal(o["price"]) for o in outcomes]
    weights  = [1 / d for d in decimals]
    total_w  = sum(weights)
    results  = []
    for i, o in enumerate(outcomes):
        stake  = round((weights[i] / total_w) * total_stake, 2)
        payout = round(stake * decimals[i], 2)
        profit = round(payout - total_stake, 2)
        results.append({
            "team":   o["name"],
            "book":   o["book"],
            "odds":   o["price"],
            "stake":  stake,
            "payout": payout,
            "profit": profit,
        })
    return results

def check_arbitrage(outcomes: list) -> dict:
    """
    Check for arbitrage: sum of implied probs < 1.
    Returns full arb info including exact bet amounts.
    """
    if len(outcomes) < 2:
        return None
    total_implied = sum(american_to_implied_prob(o["price"]) for o in outcomes)
    if total_implied < 1.0:
        profit_pct = round((1 - total_implied) * 100, 2)
        bet_amounts = arb_bet_amounts(outcomes)
        return {
            "profit_pct":    profit_pct,
            "total_implied": round(total_implied * 100, 2),
            "outcomes":      outcomes,
            "bet_amounts":   bet_amounts,
            "roi":           round(profit_pct, 2),
        }
    return None

def get_best_odds_per_team(bookmakers: list) -> dict:
    """
    Use max-heap to find the single best available odds per team
    across all sportsbooks. O(n log n) where n = total outcomes.
    """
    heaps = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    dec = american_to_decimal(o["price"])
                    # Push negative decimal (min-heap simulates max-heap)
                    heapq.heappush(heaps[o["name"]], (-dec, o["price"], book["title"]))

    best = {}
    for team, heap in heaps.items():
        if heap:
            neg_dec, price, book = heap[0]
            best[team] = {
                "price":        price,
                "book":         book,
                "decimal":      round(-neg_dec, 4),
                "implied_prob": round(decimal_to_prob(-neg_dec) * 100, 1),
            }
    return best

def build_odds_table(bookmakers: list) -> dict:
    """
    Build full odds table: {team: {book: price}} for all books.
    Also flags the best odds per team across all books.
    """
    table      = defaultdict(dict)   # team -> {book -> price}
    best_price = defaultdict(lambda: -float("inf"))
    best_book  = {}

    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    team  = o["name"]
                    price = o["price"]
                    dec   = american_to_decimal(price)
                    table[team][book["title"]] = price
                    if dec > best_price[team]:
                        best_price[team] = dec
                        best_book[team]  = book["title"]

    return {
        "rows":      dict(table),
        "best_book": best_book,
        "books":     [b["title"] for b in bookmakers],
    }


def get_best_spread_per_team(bookmakers: list) -> dict:
    """
    Find best spread per team across all books using max-heap.
    For spreads, best = highest odds (most money back if spread covers).
    Returns: {team: {point: -3.5, price: -108, book: "DraftKings"}}
    """
    heaps = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "spreads":
                for o in market["outcomes"]:
                    dec   = american_to_decimal(o["price"])
                    point = o.get("point", 0)
                    heapq.heappush(heaps[o["name"]], (-dec, o["price"], book["title"], point))

    best = {}
    for team, heap in heaps.items():
        if heap:
            neg_dec, price, book, point = heap[0]
            best[team] = {
                "price": price,
                "point": point,
                "book":  book,
                "label": f"{'+' if point > 0 else ''}{point}",
            }
    return best

def get_best_total(bookmakers: list) -> dict:
    """
    Find best over/under odds across all books using max-heap.
    Returns: {"over": {price, point, book}, "under": {price, point, book}}
    """
    over_heap  = []
    under_heap = []
    line_point = None

    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "totals":
                for o in market["outcomes"]:
                    dec   = american_to_decimal(o["price"])
                    point = o.get("point", 0)
                    if line_point is None:
                        line_point = point
                    if o["name"] == "Over":
                        heapq.heappush(over_heap,  (-dec, o["price"], book["title"], point))
                    elif o["name"] == "Under":
                        heapq.heappush(under_heap, (-dec, o["price"], book["title"], point))

    result = {}
    if over_heap:
        _, price, book, point = over_heap[0]
        result["over"]  = {"price": price, "book": book, "point": point}
    if under_heap:
        _, price, book, point = under_heap[0]
        result["under"] = {"price": price, "book": book, "point": point}

    return result

def build_spreads_totals_table(bookmakers: list) -> dict:
    """
    Build comparison tables for spreads and totals across all books.
    Returns: {spreads: {team: {book: {price, point}}}, totals: {book: {over, under}}}
    """
    spreads = defaultdict(dict)   # team -> {book -> {price, point}}
    totals  = defaultdict(dict)   # "over"/"under" -> {book -> {price, point}}
    books   = [b["title"] for b in bookmakers]

    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "spreads":
                for o in market["outcomes"]:
                    spreads[o["name"]][book["title"]] = {
                        "price": o["price"],
                        "point": o.get("point", 0),
                        "label": f"{'+' if o.get('point',0) > 0 else ''}{o.get('point',0)} ({'+' if o['price'] > 0 else ''}{o['price']})"
                    }
            elif market["key"] == "totals":
                for o in market["outcomes"]:
                    totals[o["name"]][book["title"]] = {
                        "price": o["price"],
                        "point": o.get("point", 0),
                        "label": f"o/u {o.get('point',0)} ({'+' if o['price'] > 0 else ''}{o['price']})"
                    }

    return {
        "spreads": dict(spreads),
        "totals":  dict(totals),
        "books":   books,
    }

def process_game(raw: dict) -> dict:
    gid        = raw["id"]
    bookmakers = raw.get("bookmakers", [])
    best_odds  = get_best_odds_per_team(bookmakers)
    odds_table = build_odds_table(bookmakers)
    best_spreads = get_best_spread_per_team(bookmakers)
    best_total   = get_best_total(bookmakers)
    spreads_totals_table = build_spreads_totals_table(bookmakers)
    teams      = list(best_odds.keys())[:2]

    # Consensus implied probability (average across all books, no-vig)
    team_probs = defaultdict(list)
    for book in bookmakers:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    team_probs[o["name"]].append(american_to_implied_prob(o["price"]))

    raw_probs = {t: sum(ps) / len(ps) for t, ps in team_probs.items() if ps}
    total     = sum(raw_probs.values())
    consensus = {t: round(p / total * 100, 1) for t, p in raw_probs.items()} if total > 0 else {}

    # EV based on consensus probability
    ev_data = {}
    for team, info in best_odds.items():
        true_p         = consensus.get(team, 50) / 100
        ev_data[team]  = calculate_ev(info["price"], true_p)

    # Arbitrage check using best available odds
    arb_outcomes = [
        {"name": t, "price": best_odds[t]["price"], "book": best_odds[t]["book"]}
        for t in teams
    ]
    arb = check_arbitrage(arb_outcomes) if len(arb_outcomes) == 2 else None

    if arb:
        arb_stack.append({
            "game":       f"{raw['away_team']} @ {raw['home_team']}",
            "profit_pct": arb["profit_pct"],
            "time":       datetime.now().strftime("%H:%M:%S"),
        })
        if len(arb_stack) > 50:
            arb_stack.pop(0)

    # Determine favorite (highest implied prob)
    favorite = max(consensus, key=consensus.get) if consensus else None

    # How much better is the best line vs worst line (savings per $100)
    savings = {}
    for team in teams:
        book_odds = list(odds_table["rows"].get(team, {}).values())
        if len(book_odds) >= 2:
            best_dec  = max(american_to_decimal(p) for p in book_odds)
            worst_dec = min(american_to_decimal(p) for p in book_odds)
            # Savings = difference in payout per $100 bet
            savings[team] = round((best_dec - worst_dec) * 100, 2)

    return {
        "id":             gid,
        "sport":          raw.get("sport_title", ""),
        "sport_key":      raw.get("sport_key", ""),
        "home":           raw["home_team"],
        "away":           raw["away_team"],
        "commence_time":  raw.get("commence_time", ""),
        "best_odds":      best_odds,
        "odds_table":     odds_table,
        "best_spreads":   best_spreads,
        "best_total":     best_total,
        "spreads_totals": spreads_totals_table,
        "arb":            arb,
        "ev":             ev_data,
        "consensus":      consensus,
        "favorite":       favorite,
        "num_books":      len(bookmakers),
        "savings":        savings,
    }


# ─────────────────────────────────────────────────────────────
#  DEMO DATA
# ─────────────────────────────────────────────────────────────
def get_demo_data():
    return [
        {
            "id": "nba_001", "sport_key": "basketball_nba", "sport_title": "NBA",
            "commence_time": "2026-05-02T23:30:00Z",
            "home_team": "Boston Celtics", "away_team": "Cleveland Cavaliers",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Boston Celtics", "price": -185}, {"name": "Cleveland Cavaliers", "price": 155}]},
                    {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": -108, "point": -4.5}, {"name": "Cleveland Cavaliers", "price": -112, "point": 4.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over",  "price": -110, "point": 218.5}, {"name": "Under", "price": -110, "point": 218.5}]},
                ]},
                {"key": "fanduel",    "title": "FanDuel",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Boston Celtics", "price": -192}, {"name": "Cleveland Cavaliers", "price": 162}]},
                    {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": -110, "point": -4.5}, {"name": "Cleveland Cavaliers", "price": -110, "point": 4.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over",  "price": -112, "point": 218.5}, {"name": "Under", "price": -108, "point": 218.5}]},
                ]},
                {"key": "betmgm",     "title": "BetMGM",     "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Boston Celtics", "price": -180}, {"name": "Cleveland Cavaliers", "price": 150}]},
                    {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": -105, "point": -4.5}, {"name": "Cleveland Cavaliers", "price": -115, "point": 4.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over",  "price": -108, "point": 219.5}, {"name": "Under", "price": -112, "point": 219.5}]},
                ]},
                {"key": "caesars",    "title": "Caesars",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Boston Celtics", "price": -188}, {"name": "Cleveland Cavaliers", "price": 158}]},
                    {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": -112, "point": -4.5}, {"name": "Cleveland Cavaliers", "price": -108, "point": 4.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over",  "price": -110, "point": 218.5}, {"name": "Under", "price": -110, "point": 218.5}]},
                ]},
                {"key": "espnbet",    "title": "ESPN Bet",   "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Boston Celtics", "price": -175}, {"name": "Cleveland Cavaliers", "price": 148}]},
                    {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": -102, "point": -4.5}, {"name": "Cleveland Cavaliers", "price": -118, "point": 4.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over",  "price": -105, "point": 218.5}, {"name": "Under", "price": -115, "point": 218.5}]},
                ]},
            ]
        },
        {
            "id": "nba_002", "sport_key": "basketball_nba", "sport_title": "NBA",
            "commence_time": "2026-05-02T22:00:00Z",
            "home_team": "Oklahoma City Thunder", "away_team": "Denver Nuggets",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Oklahoma City Thunder", "price": -210}, {"name": "Denver Nuggets", "price": 175}]},
                    {"key": "spreads", "outcomes": [{"name": "Oklahoma City Thunder", "price": -110, "point": -5.5}, {"name": "Denver Nuggets", "price": -110, "point": 5.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -108, "point": 224.5}, {"name": "Under", "price": -112, "point": 224.5}]},
                ]},
                {"key": "fanduel",    "title": "FanDuel",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Oklahoma City Thunder", "price": -215}, {"name": "Denver Nuggets", "price": 180}]},
                    {"key": "spreads", "outcomes": [{"name": "Oklahoma City Thunder", "price": -112, "point": -5.5}, {"name": "Denver Nuggets", "price": -108, "point": 5.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -110, "point": 224.5}, {"name": "Under", "price": -110, "point": 224.5}]},
                ]},
                {"key": "betmgm",     "title": "BetMGM",     "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Oklahoma City Thunder", "price": -205}, {"name": "Denver Nuggets", "price": 170}]},
                    {"key": "spreads", "outcomes": [{"name": "Oklahoma City Thunder", "price": -108, "point": -5.5}, {"name": "Denver Nuggets", "price": -112, "point": 5.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -105, "point": 225.5}, {"name": "Under", "price": -115, "point": 225.5}]},
                ]},
                {"key": "caesars",    "title": "Caesars",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Oklahoma City Thunder", "price": -208}, {"name": "Denver Nuggets", "price": 174}]},
                    {"key": "spreads", "outcomes": [{"name": "Oklahoma City Thunder", "price": -110, "point": -5.5}, {"name": "Denver Nuggets", "price": -110, "point": 5.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -110, "point": 224.5}, {"name": "Under", "price": -110, "point": 224.5}]},
                ]},
            ]
        },
        {
            "id": "mlb_001", "sport_key": "baseball_mlb", "sport_title": "MLB",
            "commence_time": "2026-05-02T23:10:00Z",
            "home_team": "Seattle Mariners", "away_team": "Kansas City Royals",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Seattle Mariners", "price": -145}, {"name": "Kansas City Royals", "price": 122}]},
                    {"key": "spreads", "outcomes": [{"name": "Seattle Mariners", "price": -115, "point": -1.5}, {"name": "Kansas City Royals", "price": -105, "point": 1.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -110, "point": 8.5}, {"name": "Under", "price": -110, "point": 8.5}]},
                ]},
                {"key": "fanduel",    "title": "FanDuel",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Seattle Mariners", "price": -148}, {"name": "Kansas City Royals", "price": 126}]},
                    {"key": "spreads", "outcomes": [{"name": "Seattle Mariners", "price": -118, "point": -1.5}, {"name": "Kansas City Royals", "price": -102, "point": 1.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -108, "point": 8.5}, {"name": "Under", "price": -112, "point": 8.5}]},
                ]},
                {"key": "betmgm",     "title": "BetMGM",     "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Seattle Mariners", "price": -140}, {"name": "Kansas City Royals", "price": 118}]},
                    {"key": "spreads", "outcomes": [{"name": "Seattle Mariners", "price": -112, "point": -1.5}, {"name": "Kansas City Royals", "price": -108, "point": 1.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -112, "point": 8.5}, {"name": "Under", "price": -108, "point": 8.5}]},
                ]},
                {"key": "caesars",    "title": "Caesars",    "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Seattle Mariners", "price": -143}, {"name": "Kansas City Royals", "price": 124}]},
                    {"key": "spreads", "outcomes": [{"name": "Seattle Mariners", "price": -110, "point": -1.5}, {"name": "Kansas City Royals", "price": -110, "point": 1.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -110, "point": 8.5}, {"name": "Under", "price": -110, "point": 8.5}]},
                ]},
                {"key": "espnbet",    "title": "ESPN Bet",   "markets": [
                    {"key": "h2h",     "outcomes": [{"name": "Seattle Mariners", "price": -138}, {"name": "Kansas City Royals", "price": 120}]},
                    {"key": "spreads", "outcomes": [{"name": "Seattle Mariners", "price": -108, "point": -1.5}, {"name": "Kansas City Royals", "price": -112, "point": 1.5}]},
                    {"key": "totals",  "outcomes": [{"name": "Over", "price": -105, "point": 8.5}, {"name": "Under", "price": -115, "point": 8.5}]},
                ]},
            ]
        },
        {
            "id": "mlb_002", "sport_key": "baseball_mlb", "sport_title": "MLB",
            "commence_time": "2026-05-02T22:40:00Z",
            "home_team": "Los Angeles Dodgers", "away_team": "San Diego Padres",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Dodgers", "price": -168}, {"name": "San Diego Padres", "price": 142}]}]},
                {"key": "fanduel",    "title": "FanDuel",    "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Dodgers", "price": -172}, {"name": "San Diego Padres", "price": 146}]}]},
                {"key": "betmgm",     "title": "BetMGM",     "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Dodgers", "price": -162}, {"name": "San Diego Padres", "price": 136}]}]},
                {"key": "caesars",    "title": "Caesars",    "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Dodgers", "price": -165}, {"name": "San Diego Padres", "price": 140}]}]},
            ]
        },
        {
            "id": "mlb_003", "sport_key": "baseball_mlb", "sport_title": "MLB",
            "commence_time": "2026-05-03T01:10:00Z",
            "home_team": "New York Yankees", "away_team": "Chicago White Sox",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": -220}, {"name": "Chicago White Sox", "price": 185}]}]},
                {"key": "fanduel",    "title": "FanDuel",    "markets": [{"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": -225}, {"name": "Chicago White Sox", "price": 190}]}]},
                {"key": "betmgm",     "title": "BetMGM",     "markets": [{"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": -215}, {"name": "Chicago White Sox", "price": 180}]}]},
                {"key": "caesars",    "title": "Caesars",    "markets": [{"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": -218}, {"name": "Chicago White Sox", "price": 184}]}]},
                # Arb opportunity built in
                {"key": "espnbet",    "title": "ESPN Bet",   "markets": [{"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": -195}, {"name": "Chicago White Sox", "price": 192}]}]},
            ]
        },
        {
            "id": "pga_001", "sport_key": "golf_pga_tour_winner", "sport_title": "PGA Tour",
            "commence_time": "2026-05-08T12:00:00Z",
            "home_team": "Scottie Scheffler", "away_team": "Field",
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Scottie Scheffler", "price": 450}, {"name": "Rory McIlroy", "price": 700},
                    {"name": "Xander Schauffele", "price": 1000}, {"name": "Collin Morikawa", "price": 1400},
                ]}]},
                {"key": "fanduel", "title": "FanDuel", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Scottie Scheffler", "price": 500}, {"name": "Rory McIlroy", "price": 650},
                    {"name": "Xander Schauffele", "price": 950}, {"name": "Collin Morikawa", "price": 1300},
                ]}]},
                {"key": "betmgm", "title": "BetMGM", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Scottie Scheffler", "price": 475}, {"name": "Rory McIlroy", "price": 725},
                    {"name": "Xander Schauffele", "price": 1100}, {"name": "Collin Morikawa", "price": 1500},
                ]}]},
            ]
        },
    ]


# ─────────────────────────────────────────────────────────────
#  FETCH
# ─────────────────────────────────────────────────────────────
def fetch_odds(sport_key: str) -> list:
    now = time.time()
    if sport_key in last_fetched and now - last_fetched[sport_key] < cache_ttl:
        cached = [g for g in games_db.values() if g["sport_key"] == sport_key]
        if cached:
            return cached

    if API_KEY == "demo":
        raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key]
    else:
        try:
            r = requests.get(
                f"{BASE_URL}/sports/{sport_key}/odds",
                params={
                    "apiKey":      API_KEY,
                    "regions":     "us",
                    "markets":     "h2h,spreads,totals",
                    "oddsFormat":  "american",
                    "bookmakers":  ",".join(BOOKS),
                },
                timeout=10,
            )
            raw_games = r.json() if r.status_code == 200 else []
        except Exception:
            raw_games = [g for g in get_demo_data() if g["sport_key"] == sport_key]

    processed = []
    for raw in raw_games:
        game = process_game(raw)
        games_db[game["id"]] = game
        processed.append(game)

    last_fetched[sport_key] = now
    return processed

def fetch_all() -> list:
    games = []
    for sk in SPORTS:
        games.extend(fetch_odds(sk))
    return games


# ─────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/games")
def api_games():
    sport    = request.args.get("sport", "all")
    sort_by  = request.args.get("sort", "time")
    filter_by = request.args.get("filter", "all")

    games = fetch_all() if sport == "all" else fetch_odds(sport)

    if filter_by == "arb":
        games = [g for g in games if g.get("arb")]
    elif filter_by == "posev":
        games = [g for g in games if any(v > 0 for v in g.get("ev", {}).values())]

    if sort_by == "arb":
        games.sort(key=lambda g: g["arb"]["profit_pct"] if g.get("arb") else 0, reverse=True)
    elif sort_by == "ev":
        games.sort(key=lambda g: max(g.get("ev", {}).values() or [0]), reverse=True)
    elif sort_by == "books":
        games.sort(key=lambda g: g["num_books"], reverse=True)

    return jsonify({"games": games, "count": len(games), "demo": API_KEY == "demo"})

@app.route("/api/game/<game_id>")
def api_game(game_id):
    game = games_db.get(game_id)
    if not game:
        return jsonify({"error": "not found"}), 404
    return jsonify({"game": game})

@app.route("/api/arbitrage")
def api_arbitrage():
    fetch_all()
    arbs = [g for g in games_db.values() if g.get("arb")]
    return jsonify({"arbs": arbs, "count": len(arbs)})

@app.route("/api/arb_calc")
def api_arb_calc():
    """Calculate exact bet amounts for a given stake."""
    game_id = request.args.get("game_id")
    stake   = float(request.args.get("stake", 100))
    game    = games_db.get(game_id)
    if not game or not game.get("arb"):
        return jsonify({"error": "no arb found"}), 404
    arb = game["arb"]
    amounts = arb_bet_amounts(arb["outcomes"], stake)
    return jsonify({
        "amounts":    amounts,
        "profit":     round(stake * arb["profit_pct"] / 100, 2),
        "profit_pct": arb["profit_pct"],
        "stake":      stake,
    })

@app.route("/api/parlay_ev", methods=["POST"])
def api_parlay_ev():
    """
    Calculate true EV of a parlay vs what the book pays.

    Key insight: the vig on each individual bet (~4.5% for -110 lines)
    COMPOUNDS with every leg added. A 4-leg parlay means the bettor
    is effectively paying 4x the vig exposure vs a single bet.

    This calculator shows:
    - Implied probability of each leg (with vig)
    - Book parlay odds (product of all decimals)
    - Compounded vig / house edge across all legs
    - EV of the parlay
    - How much you are underpaid vs the fair price
    """
    data  = request.json or {}
    legs  = data.get("legs", [])
    stake = float(data.get("stake", 100))

    if len(legs) < 2:
        return jsonify({"error": "Need at least 2 legs"}), 400
    if len(legs) > 10:
        return jsonify({"error": "Max 10 legs"}), 400

    # Step 1: Process each leg
    # Standard sportsbook vig ≈ 4.55% per leg (based on -110/-110 markets)
    STANDARD_VIG = 0.0455
    leg_results      = []
    raw_parlay_prob  = 1.0   # with vig
    fair_parlay_prob_calc = 1.0  # no vig

    for i, leg in enumerate(legs):
        odds     = int(leg["odds"])
        dec      = american_to_decimal(odds)
        raw_prob = decimal_to_prob(dec)
        # Remove standard vig to get fair probability
        fair_prob = raw_prob / (1 + STANDARD_VIG)

        raw_parlay_prob  *= raw_prob
        fair_parlay_prob_calc *= fair_prob
        leg_results.append({
            "label":     leg.get("label", f"Leg {i+1}"),
            "odds":      odds,
            "decimal":   round(dec, 4),
            "raw_prob":  round(raw_prob * 100, 2),
            "fair_prob": round(fair_prob * 100, 2),
        })

    # Step 2: Book parlay decimal = product of all leg decimals
    book_decimal = 1.0
    for leg in leg_results:
        book_decimal *= leg["decimal"]
    book_decimal = round(book_decimal, 4)

    def to_american(dec):
        if dec >= 2: return int(round((dec - 1) * 100))
        return int(round(-100 / (dec - 1)))

    book_american = to_american(book_decimal)

    # Step 3: Fair (no-vig) parlay probability (already computed above)
    fair_parlay_prob = fair_parlay_prob_calc

    true_fair_decimal  = round(1 / fair_parlay_prob, 4)
    true_fair_american = to_american(true_fair_decimal)

    # Step 4: Payouts and EV
    book_payout = round(stake * (book_decimal - 1), 2)
    fair_payout = round(stake * (true_fair_decimal - 1), 2)
    payout_diff = round(fair_payout - book_payout, 2)

    # EV using fair (no-vig) probability
    ev      = round((fair_parlay_prob * book_payout) - ((1 - fair_parlay_prob) * stake), 2)
    ev_pct  = round(ev / stake * 100, 2)

    # House edge: how much the book keeps on average per $100 wagered
    # This is the vig compounding effect across all legs
    house_edge = round((1 - fair_parlay_prob * book_decimal) * 100, 2)
    breakeven  = round((1 / book_decimal) * 100, 2)

    # Per-leg vig breakdown for education
    per_leg_vig = round((1 - (1 - house_edge/100) ** (1/len(legs))) * 100, 2)

    verdict = "AVOID" if ev_pct < -10 else "MARGINAL" if ev_pct < -3 else "DECENT"

    return jsonify({
        "legs":               leg_results,
        "stake":              stake,
        "true_parlay_prob":   round(fair_parlay_prob * 100, 3),
        "book_decimal":       book_decimal,
        "book_american":      book_american,
        "true_fair_decimal":  true_fair_decimal,
        "true_fair_american": true_fair_american,
        "book_payout":        book_payout,
        "fair_payout":        fair_payout,
        "payout_diff":        payout_diff,
        "per_leg_vig":        per_leg_vig,
        "ev":                 ev,
        "ev_pct":             ev_pct,
        "house_edge":         house_edge,
        "breakeven":          breakeven,
        "verdict":            verdict,
    })

@app.route("/api/best_lines")
def api_best_lines():
    """Return the best available line per team across all games."""
    fetch_all()
    best_lines = []
    for game in games_db.values():
        for team, info in game.get("best_odds", {}).items():
            ev = game.get("ev", {}).get(team, 0)
            best_lines.append({
                "game":    f"{game['away']} @ {game['home']}",
                "sport":   game["sport"],
                "team":    team,
                "odds":    info["price"],
                "book":    info["book"],
                "implied": info["implied_prob"],
                "ev":      ev,
                "savings": game.get("savings", {}).get(team, 0),
            })
    best_lines.sort(key=lambda x: x["savings"], reverse=True)
    return jsonify({"lines": best_lines})

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  🎰  EdgeFinder — Sports Betting Tracker")
    print("=" * 50)
    print(f"  Mode: {'⚡ DEMO' if API_KEY == 'demo' else '🟢 LIVE'}")
    print("  http://localhost:5000  |  Ctrl+C to stop\n")
    app.run(debug=False, port=5000)
