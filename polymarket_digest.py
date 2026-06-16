#!/usr/bin/env python3
"""
Polymarket Daily Digest
-----------------------
Pulls top leaderboard trader positions from Polymarket, filters for
World Cup, Politics, Tech, and Economics markets, synthesises a consensus
summary, and emails it to the configured recipient every morning.

Required environment variables (set in .env or GitHub Actions secrets):
  GMAIL_SENDER      - sender Gmail address
  GMAIL_APP_PASSWORD - Gmail App Password (not your account password)
  EMAIL_RECIPIENT   - destination address (defaults to GMAIL_SENDER)

Optional:
  TOP_N_TRADERS     - how many top traders to sample per category (default 10)
  LEADERBOARD_PERIOD - DAY | WEEK | MONTH | ALL (default WEEK)
"""

import os
import sys
import json
import time
import smtplib
import logging
import textwrap
from datetime import datetime, timezone
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GAMMA_API   = "https://gamma-api.polymarket.com"
DATA_API    = "https://data-api.polymarket.com"

TOP_N       = int(os.getenv("TOP_N_TRADERS", "10"))
LB_PERIOD   = os.getenv("LEADERBOARD_PERIOD", "WEEK")   # DAY|WEEK|MONTH|ALL
MAX_POSITIONS_PER_TRADER = 50

# Categories we care about and how they map to Polymarket's leaderboard slugs
CATEGORIES = {
    "Politics":   "POLITICS",
    "Tech":       "TECH",
    "Economics":  "ECONOMICS",
    "Sports":     "SPORTS",          # World Cup lives here
}

# Extra keyword filter for the Sports bucket so we only show World Cup markets
SPORTS_KEYWORDS = ["world cup", "fifa", "soccer", "football championship"]

GMAIL_SENDER   = os.getenv("GMAIL_SENDER", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
RECIPIENT      = os.getenv("EMAIL_RECIPIENT", GMAIL_SENDER)

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

def get(url: str, params: dict | None = None, retries: int = 3) -> Any:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None

# ---------------------------------------------------------------------------
# Polymarket API calls
# ---------------------------------------------------------------------------
def fetch_leaderboard(category_slug: str, limit: int = TOP_N) -> list[dict]:
    """Return top traders for a given leaderboard category."""
    data = get(f"{DATA_API}/leaderboard", {
        "category":   category_slug,
        "timePeriod": LB_PERIOD,
        "orderBy":    "PNL",
        "limit":      limit,
    })
    if data is None:
        return []
    # API may return a list directly or {"data": [...]}
    if isinstance(data, list):
        return data
    return data.get("data", data.get("leaderboard", []))


def fetch_positions(wallet: str, limit: int = MAX_POSITIONS_PER_TRADER) -> list[dict]:
    """Return open positions for a wallet address."""
    data = get(f"{DATA_API}/positions", {
        "user":   wallet,
        "limit":  limit,
        "sizeThreshold": "0",
    })
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return data.get("data", [])


def fetch_market(condition_id: str) -> dict | None:
    """Fetch market metadata from Gamma API."""
    data = get(f"{GAMMA_API}/markets", {"conditionId": condition_id})
    if isinstance(data, list) and data:
        return data[0]
    return None

# ---------------------------------------------------------------------------
# Data processing
# ---------------------------------------------------------------------------
def is_world_cup_market(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in SPORTS_KEYWORDS)


def gather_category_positions(category_name: str, category_slug: str) -> list[dict]:
    """
    For a given category, pull top traders and their positions,
    returning a flat list of position records enriched with trader info.
    """
    log.info("Fetching leaderboard: %s (%s)", category_name, category_slug)
    traders = fetch_leaderboard(category_slug)
    if not traders:
        log.warning("No traders returned for %s", category_name)
        return []

    log.info("  %d traders found, fetching positions …", len(traders))
    all_positions: list[dict] = []

    for trader in traders:
        wallet = trader.get("proxyWallet") or trader.get("address") or ""
        if not wallet:
            continue
        username = trader.get("userName") or trader.get("name") or wallet[:8]
        pnl      = trader.get("pnl", 0)

        positions = fetch_positions(wallet)
        for pos in positions:
            title = pos.get("title") or pos.get("market") or ""

            # For Sports, enforce World Cup keyword filter
            if category_slug == "SPORTS" and not is_world_cup_market(title):
                continue

            # Only include positions with meaningful size
            size = float(pos.get("size") or pos.get("currentValue") or 0)
            if size <= 0:
                continue

            outcome      = pos.get("outcome") or pos.get("side") or "YES"
            avg_price    = float(pos.get("avgPrice") or pos.get("price") or 0)
            cash_pnl     = float(pos.get("cashPnl") or pos.get("pnl") or 0)
            condition_id = pos.get("conditionId") or pos.get("market") or ""

            all_positions.append({
                "category":     category_name,
                "trader":       username,
                "trader_pnl":   pnl,
                "market_title": title,
                "condition_id": condition_id,
                "outcome":      outcome,
                "avg_price":    avg_price,
                "size":         size,
                "cash_pnl":     cash_pnl,
            })

    return all_positions


def compute_consensus(positions: list[dict]) -> dict[str, dict]:
    """
    For each unique market, compute:
    - weighted-average implied probability (by position size)
    - net YES/NO trader count
    - total volume
    """
    markets: dict[str, dict] = {}

    for pos in positions:
        title = pos["market_title"]
        if not title:
            continue
        if title not in markets:
            markets[title] = {
                "category":    pos["category"],
                "condition_id": pos["condition_id"],
                "yes_weight":  0.0,
                "no_weight":   0.0,
                "yes_traders": 0,
                "no_traders":  0,
                "total_size":  0.0,
                "price_x_size": 0.0,
            }

        m = markets[title]
        outcome = (pos["outcome"] or "YES").upper()
        size    = pos["size"]
        price   = pos["avg_price"]

        m["total_size"] += size

        if outcome in ("YES", "LONG", "BUY", "1"):
            m["yes_weight"]  += size
            m["yes_traders"] += 1
            m["price_x_size"] += price * size
        else:
            m["no_weight"]   += size
            m["no_traders"]  += 1
            # NO at price p implies YES at (1-p)
            m["price_x_size"] += (1.0 - price) * size

    # Compute consensus probability
    for title, m in markets.items():
        total = m["yes_weight"] + m["no_weight"]
        if total > 0:
            # Weighted average implied probability of YES
            m["consensus_prob"] = m["price_x_size"] / total
        else:
            m["consensus_prob"] = 0.5
        m["trader_lean"] = (
            "YES" if m["yes_traders"] >= m["no_traders"] else "NO"
        )
        m["title"] = title

    return markets


def top_markets_by_category(
    all_positions: list[dict], top_n: int = 5
) -> dict[str, list[dict]]:
    """Group markets by category, sort by total position size, return top N each."""
    consensus = compute_consensus(all_positions)

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for title, m in consensus.items():
        by_cat[m["category"]].append(m)

    result = {}
    for cat, mkts in by_cat.items():
        sorted_mkts = sorted(mkts, key=lambda x: x["total_size"], reverse=True)
        result[cat] = sorted_mkts[:top_n]

    return result

# ---------------------------------------------------------------------------
# Email composition
# ---------------------------------------------------------------------------
def pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def build_html(
    by_category: dict[str, list[dict]],
    generated_at: str,
    period: str,
) -> str:
    cat_order = ["Politics", "Economics", "Tech", "Sports"]

    rows_html = ""
    for cat in cat_order:
        mkts = by_category.get(cat)
        if not mkts:
            continue

        display = cat if cat != "Sports" else "World Cup / Sports"
        rows_html += f"""
        <tr>
          <td colspan="4" style="background:#1a1a2e;color:#e0e0e0;
              padding:10px 16px;font-weight:bold;font-size:15px;
              letter-spacing:.5px;">{display}</td>
        </tr>"""

        for m in mkts:
            prob  = m["consensus_prob"]
            lean  = m["trader_lean"]
            yes_c = m["yes_traders"]
            no_c  = m["no_traders"]
            size  = m["total_size"]

            # Colour the bar green/red based on lean
            bar_color = "#27ae60" if lean == "YES" else "#e74c3c"
            bar_width = int(prob * 100) if lean == "YES" else int((1 - prob) * 100)
            bar_label = f"YES {pct(prob)}" if lean == "YES" else f"NO {pct(1-prob)}"

            title_display = textwrap.shorten(m["title"], width=80, placeholder="…")

            rows_html += f"""
        <tr style="border-bottom:1px solid #2d2d2d;">
          <td style="padding:10px 16px;color:#f0f0f0;max-width:340px;">
            {title_display}
          </td>
          <td style="padding:10px 8px;text-align:center;white-space:nowrap;">
            <div style="background:#2d2d2d;border-radius:4px;overflow:hidden;
                        width:120px;display:inline-block;vertical-align:middle;">
              <div style="background:{bar_color};width:{bar_width}%;padding:4px 6px;
                          color:#fff;font-size:12px;font-weight:bold;
                          white-space:nowrap;">{bar_label}</div>
            </div>
          </td>
          <td style="padding:10px 8px;text-align:center;color:#aaa;
                     font-size:13px;white-space:nowrap;">
            👍 {yes_c} &nbsp;👎 {no_c}
          </td>
          <td style="padding:10px 8px;text-align:right;color:#aaa;
                     font-size:13px;white-space:nowrap;">
            ${size:,.0f}
          </td>
        </tr>"""

    if not rows_html:
        rows_html = """<tr><td colspan="4" style="padding:20px;color:#aaa;
            text-align:center;">No matching positions found today.</td></tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#0d0d0d;font-family:'Segoe UI',
             Arial,sans-serif;color:#e0e0e0;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="max-width:680px;margin:24px auto;">
    <tr>
      <td style="background:#1a1a2e;padding:24px 32px;border-radius:8px 8px 0 0;">
        <h1 style="margin:0;font-size:22px;color:#fff;letter-spacing:.5px;">
          📊 Polymarket Daily Digest
        </h1>
        <p style="margin:6px 0 0;color:#8888cc;font-size:13px;">
          Smart-money consensus · {period} leaderboard · {generated_at}
        </p>
      </td>
    </tr>
    <tr>
      <td style="background:#141414;border-radius:0 0 8px 8px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <thead>
            <tr style="background:#1e1e1e;color:#888;font-size:12px;
                       text-transform:uppercase;letter-spacing:.5px;">
              <th style="padding:8px 16px;text-align:left;">Market</th>
              <th style="padding:8px;text-align:center;">Consensus</th>
              <th style="padding:8px;text-align:center;">Traders</th>
              <th style="padding:8px 16px;text-align:right;">Volume ($)</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding:16px 0;text-align:center;color:#444;font-size:12px;">
        Generated automatically · Data from Polymarket public API ·
        <a href="https://polymarket.com" style="color:#555;">polymarket.com</a>
      </td>
    </tr>
  </table>
</body>
</html>"""


def build_plain(by_category: dict[str, list[dict]], generated_at: str) -> str:
    lines = [
        "POLYMARKET DAILY DIGEST",
        f"Generated: {generated_at}",
        "=" * 60,
    ]
    cat_order = ["Politics", "Economics", "Tech", "Sports"]
    for cat in cat_order:
        mkts = by_category.get(cat)
        if not mkts:
            continue
        label = cat if cat != "Sports" else "World Cup / Sports"
        lines += ["", f"[ {label.upper()} ]", "-" * 40]
        for m in mkts:
            prob  = m["consensus_prob"]
            lean  = m["trader_lean"]
            label_str = f"YES {pct(prob)}" if lean == "YES" else f"NO {pct(1-prob)}"
            lines.append(
                f"  {textwrap.shorten(m['title'], 62)}\n"
                f"    Consensus: {label_str}  |  "
                f"Traders YES:{m['yes_traders']} NO:{m['no_traders']}  |  "
                f"Vol: ${m['total_size']:,.0f}"
            )
    lines += ["", "=" * 60, "polymarket.com"]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------
def send_email(subject: str, html_body: str, plain_body: str) -> None:
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        log.error(
            "GMAIL_SENDER / GMAIL_APP_PASSWORD not set — printing digest to stdout."
        )
        print(plain_body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = RECIPIENT

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))

    log.info("Sending email to %s …", RECIPIENT)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_PASSWORD)
        smtp.sendmail(GMAIL_SENDER, RECIPIENT, msg.as_string())
    log.info("Email sent.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    now     = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %B %-d %Y")   # e.g. "Monday, June 16 2026"
    ts_str  = now.strftime("%Y-%m-%d %H:%M UTC")

    all_positions: list[dict] = []

    for cat_name, cat_slug in CATEGORIES.items():
        positions = gather_category_positions(cat_name, cat_slug)
        log.info("  → %d qualifying positions in %s", len(positions), cat_name)
        all_positions.extend(positions)

    if not all_positions:
        log.warning("No positions gathered — check API connectivity.")

    by_category = top_markets_by_category(all_positions, top_n=5)

    html  = build_html(by_category, ts_str, LB_PERIOD.capitalize())
    plain = build_plain(by_category, ts_str)

    subject = f"📊 Polymarket Digest — {date_str}"
    send_email(subject, html, plain)


if __name__ == "__main__":
    main()
