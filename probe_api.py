#!/usr/bin/env python3
"""One-shot probe to discover live Polymarket API endpoints."""
import requests, json, sys

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
})

def try_url(url, params=None):
    try:
        r = S.get(url, params=params, timeout=10)
        snippet = r.text[:300].replace("\n", " ")
        print(f"  {r.status_code}  {url}{'?'+str(params) if params else ''}  →  {snippet}")
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ERR  {url}  →  {e}")
    return None

print("=== LEADERBOARD candidates ===")
try_url("https://data-api.polymarket.com/leaderboard")
try_url("https://data-api.polymarket.com/leaderboard", {"limit": 5})
try_url("https://data-api.polymarket.com/leaderboard", {"window": "1w", "limit": 5})
try_url("https://data-api.polymarket.com/leaderboard", {"period": "week", "limit": 5})
try_url("https://data-api.polymarket.com/leaderboard/top", {"limit": 5})
try_url("https://data-api.polymarket.com/leaderboard/profit", {"limit": 5})
try_url("https://data-api.polymarket.com/leaderboard/volume", {"limit": 5})
try_url("https://data-api.polymarket.com/v1/leaderboard", {"limit": 5})
try_url("https://data-api.polymarket.com/v2/leaderboard", {"limit": 5})
try_url("https://polymarket.com/api/leaderboard", {"limit": 5})
try_url("https://polymarket.com/api/v1/leaderboard", {"limit": 5})
try_url("https://strapi.polymarket.com/leaderboard", {"limit": 5})

print("\n=== POSITIONS / PROFILES candidates ===")
try_url("https://data-api.polymarket.com/positions", {"user": "0xf9e7e12beab0d09248e51dd77e049d4979d8bed5", "limit": 3})
try_url("https://data-api.polymarket.com/profiles", {"limit": 5})
try_url("https://data-api.polymarket.com/v1/positions", {"user": "0xf9e7e12beab0d09248e51dd77e049d4979d8bed5", "limit": 3})

print("\n=== GAMMA markets ===")
data = try_url("https://gamma-api.polymarket.com/markets", {"limit": 2, "active": "true"})
if data:
    print("  GAMMA MARKETS SAMPLE:", json.dumps(data[0] if isinstance(data, list) else data, indent=2)[:600])

print("\n=== CLOB ===")
try_url("https://clob.polymarket.com/markets", {"limit": 2})
