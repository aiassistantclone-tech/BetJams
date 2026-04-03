from datetime import datetime
import re
from collections import defaultdict
import os

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("API_KEY")
API_BASE = "https://api.sportsgameodds.com/v2"
TIMEOUT = 30

LEAGUE = "NBA"
BOOKMAKERS = ["fanduel", "draftkings", "betmgm", "caesars"]
MIN_ARB_PCT = 0.1
SHOW_TOP = 50

STAT_NAMES = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "threePointersMade": "Threes",
    "pointsReboundsAssists": "PRA",
}


def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return (odds / 100.0) + 1.0
    return (100.0 / abs(odds)) + 1.0


def format_start(starts_at):
    if not starts_at:
        return "Start time unavailable"
    try:
        dt = datetime.fromisoformat(str(starts_at).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return str(starts_at)


def clean_player_name(player_id: str) -> str:
    name = str(player_id)
    if name.endswith("_NBA"):
        name = name[:-4]
    name = re.sub(r"_\\d+$", "", name)
    parts = [p for p in name.split("_") if p]
    return " ".join(p.capitalize() for p in parts) if parts else str(player_id)


def extract_player_id(odd_id: str):
    parts = str(odd_id).split("-")
    if len(parts) < 5:
        return None
    stat_entity = parts[1]
    if stat_entity in {"home", "away", "all"}:
        return None
    return stat_entity


def stat_key_from_odd_id(odd_id: str):
    return str(odd_id).split("-")[0]


def calculate_true_arb(over_dec: float, under_dec: float):
    implied = (1 / over_dec) + (1 / under_dec)
    if implied >= 1:
        return None
    arb_pct = ((1 / implied) - 1) * 100
    return {
        "implied": implied,
        "arb_pct": arb_pct,
    }


@app.get("/")
def root():
    return {"status": "SportsGameOdds API running"}


@app.get("/debug")
def debug():
    try:
        params = {
            "leagueID": LEAGUE,
            "oddsAvailable": "true",
            "finalized": "false",
            "bookmakerID": ",".join(BOOKMAKERS),
            "limit": 2,
            "includeAltLines": "false",
        }

        response = requests.get(
            f"{API_BASE}/events",
            params=params,
            headers={"x-api-key": API_KEY},
            timeout=TIMEOUT,
        )

        return {
            "status_code": response.status_code,
            "top_level_keys": list(response.json().keys()) if response.headers.get("content-type", "").startswith("application/json") else [],
            "text_preview": response.text[:1000],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/arbs")
def get_arbs():
    try:
        params = {
            "leagueID": LEAGUE,
            "oddsAvailable": "true",
            "finalized": "false",
            "bookmakerID": ",".join(BOOKMAKERS),
            "limit": 50,
            "includeAltLines": "false",
        }

        response = requests.get(
            f"{API_BASE}/events",
            params=params,
            headers={"x-api-key": API_KEY},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        events = payload.get("data", [])
        grouped = defaultdict(lambda: {"over": [], "under": [], "meta": {}})
        output_rows = []

        for event in events:
            teams = event.get("teams") or {}
            away = (((teams.get("away") or {}).get("names") or {}).get("long")) or "Away"
            home = (((teams.get("home") or {}).get("names") or {}).get("long")) or "Home"
            matchup = f"{away} @ {home}"
            starts_at = ((event.get("status") or {}).get("startsAt")) or ""
            odds_map = event.get("odds") or {}

            if not isinstance(odds_map, dict):
                continue

            for odd_id, odd in odds_map.items():
                player_id = extract_player_id(odd_id)
                stat_key = stat_key_from_odd_id(odd_id)

                if not player_id or stat_key not in STAT_NAMES:
                    continue

                if not isinstance(odd, dict):
                    continue

                side = odd.get("sideID")
                if side not in {"over", "under"}:
                    continue

                by_bookmaker = odd.get("byBookmaker") or {}
                if not isinstance(by_bookmaker, dict):
                    continue

                for bookmaker, bm_data in by_bookmaker.items():
                    if bookmaker not in BOOKMAKERS:
                        continue
                    if not isinstance(bm_data, dict):
                        continue
                    if not bm_data.get("available", True):
                        continue

                    odds_raw = bm_data.get("odds")
                    line_raw = bm_data.get("overUnder")

                    if odds_raw in (None, "") or line_raw in (None, ""):
                        continue

                    try:
                        american = int(str(odds_raw))
                        line = float(str(line_raw))
                    except Exception:
                        continue

                    key = (player_id, stat_key, line, event.get("eventID"))
                    grouped[key]["meta"] = {
                        "player": clean_player_name(player_id),
                        "stat": STAT_NAMES[stat_key],
                        "matchup": matchup,
                        "start": format_start(starts_at),
                        "line": line,
                    }

                    grouped[key][side].append(
                        {
                            "book": bookmaker,
                            "odds": american,
                            "decimal": american_to_decimal(american),
                            "link": bm_data.get("deepLink") or "#",
                        }
                    )

        row_id = 1
        for data in grouped.values():
            if not data["over"] or not data["under"]:
                continue

            best_over = max(data["over"], key=lambda x: x["decimal"])
            best_under = max(data["under"], key=lambda x: x["decimal"])

            if best_over["book"] == best_under["book"]:
                continue

            arb = calculate_true_arb(best_over["decimal"], best_under["decimal"])
            if not arb or arb["arb_pct"] < MIN_ARB_PCT:
                continue

            meta = data["meta"]
            output_rows.append(
                {
                    "id": row_id,
                    "player": meta["player"],
                    "stat": meta["stat"],
                    "matchup": meta["matchup"],
                    "start": meta["start"],
                    "line": meta["line"],
                    "over_odds": best_over["odds"],
                    "under_odds": best_under["odds"],
                    "over_book": best_over["book"].title(),
                    "under_book": best_under["book"].title(),
                    "over_link": best_over["link"],
                    "under_link": best_under["link"],
                    "arb_pct": round(arb["arb_pct"], 2),
                }
            )
            row_id += 1

        output_rows.sort(key=lambda x: x["arb_pct"], reverse=True)
        return output_rows[:SHOW_TOP]

    except requests.HTTPError as e:
        try:
            body = e.response.text[:1000]
        except Exception:
            body = "No response body"
        return {
            "ok": False,
            "error": "HTTPError",
            "details": str(e),
            "response_text": body,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "details": str(e),
        }
