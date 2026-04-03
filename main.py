from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import os

app = FastAPI()

# Allow frontend (GitHub Pages / local)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can lock this down later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔐 API KEY (from Render env)
API_KEY = os.getenv("API_KEY")

BOOKS = ["fanduel", "draftkings", "betmgm", "caesars"]


def american_to_decimal(odds):
    odds = int(odds)
    if odds > 0:
        return 1 + (odds / 100)
    else:
        return 1 + (100 / abs(odds))


@app.get("/")
def home():
    return {"status": "SportsGameOdds API running"}


@app.get("/debug")
def debug():
    url = "https://api.sportsgameodds.com/v2/events?leagueID=NBA&oddsAvailable=true"

    headers = {"x-api-key": API_KEY}

    r = requests.get(url, headers=headers)

    return {
        "status_code": r.status_code,
        "top_level_keys": list(r.json().keys()) if r.status_code == 200 else None,
        "text_preview": r.text[:500]
    }


@app.get("/arbs")
def get_arbs():
    url = "https://api.sportsgameodds.com/v2/events?leagueID=NBA&oddsAvailable=true"

    headers = {"x-api-key": API_KEY}

    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        return {"error": r.text}

    data = r.json()

    arbs = []

    for event in data.get("data", []):
        matchup = f"{event['teams']['away']['names']['medium']} @ {event['teams']['home']['names']['medium']}"
        start = event["status"]["startsAt"]

        for market in event.get("odds", []):
            if market.get("type") != "playerPoints":
                continue

            player = market.get("player", "Unknown")

            best_over = None
            best_under = None

            for book in market.get("sportsbooks", []):
                if book["name"].lower() not in BOOKS:
                    continue

                for outcome in book.get("outcomes", []):
                    if outcome["type"] == "over":
                        if not best_over or outcome["odds"] > best_over["odds"]:
                            best_over = {
                                "odds": outcome["odds"],
                                "line": outcome.get("line"),
                                "book": book["name"]
                            }

                    if outcome["type"] == "under":
                        if not best_under or outcome["odds"] > best_under["odds"]:
                            best_under = {
                                "odds": outcome["odds"],
                                "line": outcome.get("line"),
                                "book": book["name"]
                            }

            if best_over and best_under:
                o = american_to_decimal(best_over["odds"])
                u = american_to_decimal(best_under["odds"])

                arb = (1 / o) + (1 / u)

                if arb < 1:
                    profit_pct = (1 - arb) * 100

                    arbs.append({
                        "player": player,
                        "matchup": matchup,
                        "start": start,
                        "line": best_over["line"],
                        "over_odds": best_over["odds"],
                        "under_odds": best_under["odds"],
                        "over_book": best_over["book"],
                        "under_book": best_under["book"],
                        "arb_pct": round(profit_pct, 2)
                    })

    return sorted(arbs, key=lambda x: x["arb_pct"], reverse=True)[:20]
