# EdgeFinder — Sports Betting Odds Tracker

A web app that tracks sports betting odds across multiple sportsbooks, finds arbitrage opportunities, calculates parlay EV, and recommends optimal bet sizing using the Kelly Criterion.

## Features

- **Odds Comparison** — side-by-side moneyline, spread, and totals across DraftKings, FanDuel, BetMGM, Caesars, and ESPN Bet
- **Arbitrage Finder** — detects guaranteed-profit opportunities and calculates exact bet amounts per leg
- **Best Lines** — ranks every team by how much you save betting the best book vs the worst
- **Parlay EV Calculator** — shows the true house edge compounding across each leg and what the parlay should actually pay
- **Kelly Criterion Calculator** — given your edge estimate, tells you exactly how much of your bankroll to bet

## Data Structures Used

| Structure | Where | Why |
|---|---|---|
| Hash Map (`dict`) | `games_db` | O(1) game lookup by ID |
| Min-Heap (`heapq`) | `get_best_odds_per_team`, `get_best_spread_per_team`, `get_best_total` | O(n log n) best-odds ranking across all books |
| Stack (`deque`) | `arb_stack` | LIFO arbitrage alert history, capped at 50 entries |

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 in your browser.

## Live Data (optional)

By default the app runs in demo mode with sample data. To use real odds:

```bash
export ODDS_API_KEY=your_key_here
python app.py
```

Get a free API key at [the-odds-api.com](https://the-odds-api.com).
