#!/usr/bin/env python3
"""
浪點台灣 Surf Taiwan — 靜態報告產生器
從 CWA 鄉鎮沿海 + Open-Meteo Marine 抓資料，產生一份 HTML 報告。
"""

import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

# ─── HTTP fetcher: curl.exe fallback for OA proxy SSL ───
# On Windows behind OA proxy, Python urllib fails with SSL cert verification.
# curl.exe with --ssl-no-revoke -k works. On GitHub Actions (Linux), urllib is fine.

def _curl_available() -> bool:
    return platform.system() == "Windows" and shutil.which("curl.exe") is not None

USE_CURL = _curl_available()

def fetch_text(url: str, timeout: int = 15) -> str:
    if USE_CURL:
        try:
            r = subprocess.run(
                ["curl.exe", "-sS", "--ssl-no-revoke", "-k", "--max-time", str(timeout), url],
                capture_output=True, timeout=timeout + 5)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", errors="replace")
            if r.stderr:
                err = r.stderr.decode("utf-8", errors="replace").strip()
                if err:
                    print(f"[WARN] curl stderr: {url} → {err[:120]}")
        except Exception as e:
            print(f"[WARN] curl exception: {url} → {e}")
    # Fallback: urllib (works on GitHub Actions / no-proxy environments)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SurfTaiwan/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[WARN] urllib failed: {url} → {e}")
    return ""

def fetch_json(url: str, timeout: int = 20) -> dict:
    raw = fetch_text(url, timeout)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[WARN] JSON parse failed: {url} → {e}")
        return {}

# ─── 12 浪點定義 ───

SPOTS = [
    {"id": "jinshan",     "name": "金山中角灣",       "county": "新北", "lat": 25.22, "lon": 121.64, "off_lat": 25.20, "off_lon": 121.68, "facing": "N",  "sid": "6501700C01"},
    {"id": "songbo",      "name": "松柏港",           "county": "臺中", "lat": 24.27, "lon": 120.52, "off_lat": 24.25, "off_lon": 120.55, "facing": "W",  "sid": "6601100C01"},
    {"id": "jiangjun",    "name": "將軍漁港",         "county": "臺南", "lat": 23.18, "lon": 120.08, "off_lat": 23.15, "off_lon": 120.05, "facing": "SW", "sid": "6701600C01"},
    {"id": "yuguang",     "name": "漁光島",           "county": "臺南", "lat": 23.04, "lon": 120.17, "off_lat": 23.00, "off_lon": 120.15, "facing": "SW", "sid": "6703600C01"},
    {"id": "yongxin",     "name": "永新漁港鑽石沙灘", "county": "高雄", "lat": 22.87, "lon": 120.37, "off_lat": 22.85, "off_lon": 120.35, "facing": "SW", "sid": "6402800C01"},
    {"id": "qijin",       "name": "旗津",             "county": "高雄", "lat": 22.61, "lon": 120.27, "off_lat": 22.58, "off_lon": 120.25, "facing": "SW", "sid": "6401000C01"},
    {"id": "qingzhou",    "name": "青洲灣沙灘",       "county": "屏東", "lat": 22.44, "lon": 120.77, "off_lat": 22.40, "off_lon": 120.70, "facing": "S",  "sid": "1001317C01"},
    {"id": "nanwan",      "name": "南灣",             "county": "屏東", "lat": 21.96, "lon": 120.76, "off_lat": 21.93, "off_lon": 120.72, "facing": "S",  "sid": "1001304C01"},
    {"id": "jialeshui",   "name": "佳樂水",           "county": "屏東", "lat": 21.99, "lon": 120.86, "off_lat": 21.95, "off_lon": 120.90, "facing": "SE", "sid": "1001324C01"},
    {"id": "jiupeng",     "name": "九棚",             "county": "屏東", "lat": 22.17, "lon": 120.89, "off_lat": 22.13, "off_lon": 120.92, "facing": "SE", "sid": "1001333C01"},
    {"id": "jinzun",      "name": "金樽",             "county": "臺東", "lat": 22.97, "lon": 121.29, "off_lat": 23.00, "off_lon": 121.35, "facing": "SE", "sid": "1001404C01"},
    {"id": "donghe",      "name": "東河",             "county": "臺東", "lat": 22.95, "lon": 121.19, "off_lat": 22.97, "off_lon": 121.25, "facing": "SE", "sid": "1001401C01"},
    {"id": "jiki",        "name": "磯崎",             "county": "花蓮", "lat": 23.72, "lon": 121.55, "off_lat": 23.70, "off_lon": 121.60, "facing": "E",  "sid": "1001506C01"},
    {"id": "beibin",      "name": "北濱",             "county": "花蓮", "lat": 23.98, "lon": 121.60, "off_lat": 24.00, "off_lon": 121.65, "facing": "E",  "sid": "1001501C01"},
    {"id": "wushi",       "name": "烏石港",           "county": "宜蘭", "lat": 24.86, "lon": 121.83, "off_lat": 24.88, "off_lon": 121.90, "facing": "NE", "sid": "1000204C01"},
]

TZ = timezone(timedelta(hours=8))

# ─── CWA 3hr HTML parser ───

def _extract_ws_ms(cell_html: str) -> float:
    """Extract wind speed in m/s from CWA cell HTML.
    CWA structure: <span class="WS hide">12.3</span><span class="WS_KS">24</span><span class="WS_H hide">44.4</span>
    WS = m/s, WS_KS = km/h, WS_H = knots. We want WS (m/s).
    """
    m = re.search(r'<span\s+class="WS[^"]*">([\d.]+)</span>', cell_html, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Fallback: strip tags, try to parse first number
    stripped = re.sub(r"<[^>]+>", "", cell_html).strip()
    try:
        return float(stripped)
    except (ValueError, TypeError):
        return 0.0

def _extract_cs_ms(cell_html: str) -> float:
    """Extract current speed from CWA cell HTML (same span structure as wind)."""
    m = re.search(r'<span\s+class="CS[^"]*">([\d.]+)</span>', cell_html, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    stripped = re.sub(r"<[^>]+>", "", cell_html).strip()
    try:
        return float(stripped)
    except (ValueError, TypeError):
        return 0.0

def parse_cwa_3hr(html: str) -> list[dict]:
    rows = re.split(r"<tr[^>]*>", html, flags=re.IGNORECASE)[1:]
    results = []
    for row in rows:
        # Extract full <td>...</td> cells (with inner HTML preserved)
        td_matches = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, re.IGNORECASE)
        if len(td_matches) < 10:
            continue

        # CWA actual 3hr field order (verified from HTML):
        #   Row with date: TD[0]=date, TD[1]=time, TD[2]=wind_speed(WS span), TD[3]=wind_force, TD[4]=wind_dir,
        #                  TD[5]=wave_height, TD[6]=wave_dir, TD[7]=wave_period, TD[8]=current, TD[9]=current_dir, TD[10]=weather  (11 TDs)
        #   Rows without date: TD[0]=time, TD[1]=wind_speed(WS span), TD[2]=wind_force, TD[3]=wind_dir,
        #                       TD[4]=wave_height, TD[5]=wave_dir, TD[6]=wave_period, TD[7]=current, TD[8]=current_dir, TD[9]=weather  (10 TDs)

        has_date_row = len(td_matches) >= 11 and bool(re.search(r'class="WS', td_matches[2], re.IGNORECASE))
        offset = 1 if has_date_row else 0

        cells = [re.sub(r"<[^>]+>", "", m).strip() for m in td_matches]
        date_str = cells[0] if has_date_row else ""
        time_str = cells[offset]
        wind_speed_html = td_matches[offset + 1]  # The one with WS span
        wind_force = cells[offset + 2]
        wind_dir = cells[offset + 3]
        wave_height_str = cells[offset + 4]
        wave_dir = cells[offset + 5]      # TEXT direction (偏西, 西南 etc)
        wave_period_str = cells[offset + 6]  # NUMBER (6.7, 7.2 etc)
        current_str = cells[offset + 7]  # May have CS span
        current_dir = cells[offset + 8]
        weather_str = cells[offset + 9] if (offset + 9) < len(cells) else ""

        # Convert date
        yr = datetime.now(TZ).year
        if has_date_row:
            mm = date_str.replace(" ", "").split("/")
            # Strip weekday text that may be appended (e.g. "08/07週四")
            date_part = re.match(r"(\d{1,2}/\d{1,2})", date_str)
            if date_part:
                mm = date_part.group(1).split("/")
            if len(mm) >= 2:
                iso = f"{yr}-{mm[0].zfill(2)}-{mm[1].zfill(2)}T{time_str.strip()}:00+08:00"
            else:
                iso = f"{date_str} {time_str}"
        else:
            # No date in this row — inherit from previous result
            if results:
                prev_date = results[-1]["time"][:10]
                iso = f"{prev_date}T{time_str.strip()}:00+08:00"
            else:
                continue

        try:
            wh = float(wave_height_str) if wave_height_str else 0.0
        except ValueError:
            wh = 0.0
        try:
            wp = float(wave_period_str) if wave_period_str else 0.0
        except ValueError:
            wp = 0.0
        ws = _extract_ws_ms(wind_speed_html)

        results.append({
            "time": iso,
            "wave_height": wh,
            "wave_period": wp,
            "wave_dir": wave_dir,
            "wind_speed": ws,       # m/s
            "wind_dir": wind_dir,
            "weather": weather_str,
        })
    return results

# ─── CWA Tide HTML parser ───

def parse_cwa_tide(html: str, day_offset: int = 1) -> list[dict]:
    """Parse CWA Tide HTML. day_offset: 1=today, 2=tomorrow, etc."""
    all_cells = re.findall(r"<(t[dh])[^>]*>([\s\S]*?)</\1>", html, re.IGNORECASE)
    tides = []
    yr = datetime.now(TZ).year
    
    # Date from day_offset (Day1=today, Day2=tomorrow, etc.)
    d = datetime.now(TZ) + timedelta(days=day_offset - 1)
    current_date = d.strftime("%Y-%m-%d")
    
    for i, (tag, raw) in enumerate(all_cells):
        stripped = re.sub(r"<[^>]+>", "", raw).strip()
        
        # Detect tide type (滿潮 or 乾潮)
        if stripped in ("滿潮", "乾潮"):
            ttype = stripped
            ttime = ""
            height = 0
            for j in range(i + 1, min(i + 5, len(all_cells))):
                next_stripped = re.sub(r"<[^>]+>", "", all_cells[j][1]).strip()
                if not ttime and re.match(r"\d{1,2}:\d{2}", next_stripped):
                    ttime = next_stripped
                elif ttime:
                    try:
                        height = float(next_stripped)
                        break
                    except ValueError:
                        continue
            if ttime:
                tides.append({"type": ttype, "time": ttime, "date": current_date, "height": height})
    return tides

# ─── Open-Meteo Marine ───

def fetch_open_meteo(lat: float, lon: float) -> list[dict]:
    url = (f"https://marine-api.open-meteo.com/v1/marine?"
           f"latitude={lat}&longitude={lon}"
           f"&hourly=wave_height,wave_period,wave_direction"
           f"&timezone=Asia/Taipei&forecast_days=7")
    data = fetch_json(url)
    if not data or "hourly" not in data:
        return []
    h = data["hourly"]
    results = []
    for i, t in enumerate(h.get("time", [])):
        results.append({
            "time": t,
            "wave_height": h["wave_height"][i],
            "wave_period": h["wave_period"][i],
            "wave_direction": h["wave_direction"][i],
        })
    return results

# ─── Day grouping ───

WEEKDAY_TW = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

def group_cwa_by_day(rows: list[dict]) -> dict[str, list[dict]]:
    """Group CWA 3hr rows by date string YYYY-MM-DD."""
    days: dict[str, list[dict]] = {}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["time"])
            ymd = dt.strftime("%Y-%m-%d")
        except Exception:
            continue
        days.setdefault(ymd, []).append(r)
    return days

def group_om_by_day(rows: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for r in rows:
        if not r.get("time"):
            continue
        ymd = r["time"][:10]
        days.setdefault(ymd, []).append(r)
    return days

def day_summary(cwa_rows: list[dict], om_rows: list[dict]) -> dict:
    """Compute max wave height, avg period, max wind for a day."""
    wh_all = [r["wave_height"] for r in cwa_rows if r["wave_height"]] + \
             [r["wave_height"] for r in om_rows if r.get("wave_height") is not None]
    wp_all = [r["wave_period"] for r in cwa_rows if r["wave_period"]] + \
             [r["wave_period"] for r in om_rows if r.get("wave_period") is not None]
    ws_all = [r["wind_speed"] for r in cwa_rows if r["wind_speed"]]

    max_wh = max(wh_all) if wh_all else 0
    avg_wp = sum(wp_all) / len(wp_all) if wp_all else 0
    max_ws = max(ws_all) if ws_all else 0

    # Dominant wave direction from CWA (textual)
    wdir = cwa_rows[0]["wave_dir"] if cwa_rows else ""
    # Numeric direction from Open-Meteo
    om_dirs = [r["wave_direction"] for r in om_rows if r.get("wave_direction") is not None]
    om_dir = om_dirs[len(om_dirs)//2] if om_dirs else None

    return {
        "wave_height_max": round(max_wh, 1),
        "wave_period_avg": round(avg_wp, 1),
        "wind_speed_max": round(max_ws, 1),
        "wind_speed_max_kt": round(max_ws * 1.944, 0),  # m/s → knots
        "wave_dir": wdir,
        "wave_dir_deg": om_dir,
    }

def surf_rating(wh: float, wp: float, ws_kt: float) -> str:
    """Return rating emoji + label. Wind >15kt or wave >3m = not suitable."""
    # Hard blocks first
    if ws_kt >= 20:
        return "🚫 暴風"
    if wh >= 3.0 and ws_kt >= 15:
        return "🚫 大浪+強風"
    if wh >= 3.0:
        return "🔴⚠️ 大浪"
    if ws_kt >= 15:
        return "🟠⚠️ 強風"
    # Normal ratings
    if wh < 0.4:
        return "🔵 太平"
    elif wh < 0.9:
        return "🟢 初學可"
    elif wh < 1.5:
        return "🟡 中級"
    elif wh < 2.5:
        return "🟠 進階"
    else:
        return "🔴 大浪"

# ─── Generate report ───

def generate_report() -> str:
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")
    today_label = f"{now.month}/{now.day} ({WEEKDAY_TW[now.weekday()]})"

    def _rating_rank(rating: str) -> int:
        """Lower = more surfable. Sort real-wave spots first, unsafe last."""
        if rating.startswith("🟠") and "⚠" not in rating:
            return 100  # 進階 — best (real waves, manageable)
        if rating.startswith("🟡"):
            return 200  # 中級 — good for intermediates
        if rating.startswith("🔴") and "⚠" not in rating:
            return 250  # 大浪 — advanced only
        if rating.startswith("🟢"):
            return 300  # 初學可 — small waves
        if rating.startswith("🔵"):
            return 350  # 太平 — flat
        if rating.startswith("🟠⚠"):
            return 400  # 強風 — has waves but too windy
        if rating.startswith("🔴⚠"):
            return 500  # 大浪 — too big for most
        if rating.startswith("🚫"):
            return 900  # 暴風/大浪+強風 — dangerous
        return 800

    all_spots_data = []

    for spot in SPOTS:
        # Fetch CWA 3hr
        cwa_html = fetch_text(f"https://www.cwa.gov.tw/V8/C/M/TownCoastal/MOD/3hr/{spot['sid']}.html")
        cwa_rows = parse_cwa_3hr(cwa_html)
        cwa_days = group_cwa_by_day(cwa_rows)

        # Fetch CWA tide (Day1-5)
        tide_all = []
        for d in range(1, 6):
            tide_html = fetch_text(
                f"https://www.cwa.gov.tw/V8/C/M/TownCoastal/MOD/Tide/{spot['sid']}_Day{d}.html")
            tide_all.extend(parse_cwa_tide(tide_html, day_offset=d))
        tide_by_date: dict[str, list[dict]] = {}
        for t in tide_all:
            tide_by_date.setdefault(t["date"], []).append(t)

        # Fetch Open-Meteo
        om_rows = fetch_open_meteo(spot["off_lat"], spot["off_lon"])
        om_days = group_om_by_day(om_rows)

        # Build 4-day forecast (today + 3 days)
        forecast = []
        for i in range(4):
            d = now + timedelta(days=i)
            ymd = d.strftime("%Y-%m-%d")
            cwa = cwa_days.get(ymd, [])
            om = om_days.get(ymd, [])
            tide = tide_by_date.get(ymd, [])
            summ = day_summary(cwa, om)
            rating = surf_rating(summ["wave_height_max"], summ["wave_period_avg"], summ["wind_speed_max_kt"])

            # 3-hourly detail rows (use CWA if available, else sample OM)
            detail = []
            if cwa:
                for r in cwa:
                    detail.append({
                        "time": r["time"][11:16] if len(r["time"]) > 16 else r["time"],
                        "wave_height": r["wave_height"],
                        "wave_period": r["wave_period"],
                        "wave_dir": r["wave_dir"],
                        "wind_speed_kt": round(r["wind_speed"] * 1.944, 0),
                        "wind_dir": r["wind_dir"],
                        "source": "CWA",
                    })
            elif om:
                # Sample every 3 hours
                sampled = [r for idx, r in enumerate(om) if idx % 3 == 0]
                for r in sampled:
                    if r.get("wave_height") is None:
                        continue
                    detail.append({
                        "time": r["time"][11:16] if len(r["time"]) > 16 else r["time"],
                        "wave_height": r["wave_height"],
                        "wave_period": r["wave_period"],
                        "wave_dir": deg_to_compass(r.get("wave_direction")),
                        "wind_speed_kt": None,
                        "wind_dir": "",
                        "source": "OpenMeteo",
                    })

            forecast.append({
                "date": ymd,
                "weekday": WEEKDAY_TW[d.weekday()],
                "short": f"{d.month}/{d.day}",
                "summary": summ,
                "rating": rating,
                "tide": tide,
                "detail": detail,
            })

        # Best day = most surfable day (lowest rating rank = best conditions)
        # Among equal rank, prefer higher wave height
        def _day_surf_score(f):
            return (_rating_rank(f["rating"]), -f["summary"]["wave_height_max"])

        best_day = min(forecast, key=_day_surf_score) if forecast else None

        all_spots_data.append({
            "spot": spot,
            "forecast": forecast,
            "best_day": best_day,
        })

    # ─── Build ranking ───
    # Sort: best rating rank first, then highest wave height, then lowest wind
    # Filter out unsurfable spots (🔴⚠️ 大浪3m+ / 🚫 暴風), take up to 10
    # If ALL spots are unsurfable, still show them so the list isn't empty

    all_rankings = []
    for sd in all_spots_data:
        if sd["best_day"]:
            rating = sd["best_day"]["rating"]
            all_rankings.append({
                "name": sd["spot"]["name"],
                "county": sd["spot"]["county"],
                "facing": sd["spot"]["facing"],
                "best_date": sd["best_day"]["short"],
                "best_weekday": sd["best_day"]["weekday"],
                "wave_height": sd["best_day"]["summary"]["wave_height_max"],
                "wave_period": sd["best_day"]["summary"]["wave_period_avg"],
                "wind_kt": int(sd["best_day"]["summary"]["wind_speed_max_kt"]),
                "rating": rating,
                "_rank": _rating_rank(rating),
            })

    # Sort: best rating rank first, then highest wave height, then lowest wind
    all_rankings.sort(key=lambda r: (r["_rank"], -r["wave_height"], r["wind_kt"]))

    # Filter out unsurfable spots (暴風 / 大浪3m+), take up to 10
    # If ALL spots are unsurfable, still show them so the list isn't empty
    surfable = [r for r in all_rankings if r["_rank"] < 500]  # exclude 🔴⚠️(500) and 🚫(900)
    ranking_source = surfable if surfable else all_rankings

    ranking = []
    for r in ranking_source[:10]:
        ranking.append({k: v for k, v in r.items() if k != "_rank"})

    # ─── Render HTML ───
    html = render_html(now, today_label, ranking, all_spots_data)
    return html


def deg_to_compass(deg) -> str:
    if deg is None:
        return ""
    try:
        d = float(deg)
    except (TypeError, ValueError):
        return str(deg)
    dirs = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]
    return dirs[round(d / 45) % 8]


def render_html(now, today_label, ranking, all_spots_data) -> str:
    generated = now.strftime("%Y-%m-%d %H:%M")

    ranking_rows = ""
    for i, r in enumerate(ranking, 1):
        star = "⭐" if i <= 3 else ""
        ranking_rows += f"""
        <tr class="rank-row rank-{i}">
          <td class="rank-num">{star}{i}</td>
          <td class="rank-name">{escape(r['name'])}<span class="rank-county">{escape(r['county'])}</span></td>
          <td class="rank-facing">{escape(r['facing'])}</td>
          <td class="rank-date">{escape(r['best_date'])} {escape(r['best_weekday'])}</td>
          <td class="rank-wh">{r['wave_height']}m</td>
          <td class="rank-wp">{r['wave_period']}s</td>
          <td class="rank-ws">{r['wind_kt']}kt</td>
          <td class="rank-rating">{r['rating']}</td>
        </tr>"""

    spot_cards = ""
    for sd in all_spots_data:
        spot = sd["spot"]
        fc = sd["forecast"]
        best = sd["best_day"]

        # Header
        best_info = ""
        if best:
            best_info = f'<span class="spot-best">最佳日 {best["short"]} {best["weekday"]} · {best["summary"]["wave_height_max"]}m · {best["rating"]}</span>'

        # Forecast tables (4 days)
        day_tables = ""
        for day in fc:
            summ = day["summary"]

            # Tide rows
            tide_html = ""
            for t in day["tide"]:
                tc = "tide-high" if t["type"] == "滿潮" else "tide-low"
                arrow = "▲" if t["type"] == "滿潮" else "▼"
                tide_html += f'<span class="tide-item {tc}">{arrow} {escape(t["type"])} {escape(t["time"])}<small class="tide-h">{t["height"]}cm</small></span>'

            # Detail rows (3-hourly)
            detail_rows = ""
            for dr in day["detail"]:
                src_badge = '<span class="src-cwa">CWA</span>' if dr["source"] == "CWA" else '<span class="src-om">OM</span>'
                ws_str = f'{dr["wind_speed_kt"]:.0f}kt' if dr["wind_speed_kt"] is not None else "—"
                wh_pct = min(100, max(2, (dr["wave_height"] / 4.0) * 100)) if dr["wave_height"] else 0
                wh_class = wave_color_class(dr["wave_height"])
                detail_rows += f"""
                <tr>
                  <td class="d-time">{escape(dr['time'])}</td>
                  <td class="d-wh-cell"><div class="wh-mini {wh_class}"></div><span class="d-wh-num">{dr['wave_height']}m</span></td>
                  <td class="d-wp">{dr['wave_period']}s</td>
                  <td class="d-dir">{escape(dr['wave_dir'])}</td>
                  <td class="d-ws"><span class="d-ws-num">{ws_str}</span> <span class="d-ws-dir">{escape(dr['wind_dir'])}</span></td>
                </tr>"""

            # Compact tide info in header
            tide_compact = ""
            for t in day["tide"]:
                arrow = "▲" if t["type"] == "滿潮" else "▼"
                tide_compact += f" {arrow}{escape(t['time'])}"
            # Compact tide in header — only show if no detailed tide-strip below
            tide_header = ""
            if tide_compact and not tide_html:
                tide_header = f'<span class="tide-compact">潮汐{escape(tide_compact)}</span>'
            elif tide_compact:
                tide_header = ""

            day_tables += f"""
          <div class="day-block">
            <div class="day-header">
              <span class="day-date">{escape(day['weekday'])} {escape(day['short'])}</span>
              <span class="day-summary">
                {summ['wave_height_max']}m · {summ['wave_period_avg']}s · {summ['wind_speed_max_kt']:.0f}kt · {escape(summ['wave_dir'])}
              </span>
              {tide_header}
              <span class="day-rating">{day['rating']}</span>
            </div>
            {"<div class='tide-strip'>" + tide_html + "</div>" if tide_html else ""}
            <table class="detail-table">
              <thead><tr><th>時刻</th><th>浪高</th><th>週期</th><th>浪向</th><th>風速·風向</th></tr></thead>
              <tbody>{detail_rows}</tbody>
            </table>
          </div>"""

        spot_cards += f"""
      <div class="spot-card" id="{spot['id']}">
        <div class="spot-head" onclick="toggleSpot('{spot['id']}')" role="button" tabindex="0">
          <h2 class="spot-name">{escape(spot['name'])}</h2>
          <span class="spot-badge">{escape(spot['facing'])}</span>
          <span class="spot-county">{escape(spot['county'])}</span>
          <span class="spot-toggle" id="toggle-{spot['id']}">▶</span>
        </div>
        <div class="spot-summary-row">
          {best_info}
          <span class="spot-today-wh">{fc[0]['summary']['wave_height_max']}m</span>
          <span class="spot-today-ws">{fc[0]['summary']['wind_speed_max_kt']:.0f}kt</span>
          <span class="spot-today-rating">{fc[0]['rating']}</span>
        </div>
        <div class="spot-forecast" id="fc-{spot['id']}">{day_tables}</div>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>浪點台灣 · {escape(today_label)}</title>
<!-- Google Analytics 4 -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-CDBNGQ04BY"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-CDBNGQ04BY');
</script>
<style>
:root {{
  --bg: #0a1628; --card: #12263a; --card2: #1a3350;
  --text: #e8f0fe; --dim: #7b9ab8; --accent: #4fc3f7;
  --wave1: #6dd5fa; --wave2: #2196f3; --wave3: #ff9800; --wave4: #ff4081;
  --tide-hi: #26a69a; --tide-lo: #4dd0e1;
  --sand: #d4a574; --border: #1e3a5f;
  --radius: 8px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: "Noto Sans TC","Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; padding:12px; max-width:960px; margin:0 auto; }}
a {{ color:var(--accent); }}

.hero {{ text-align:center; padding:24px 0 16px; }}
.hero-logo {{ width:64px; height:64px; border-radius:50%; object-fit:cover; border:2px solid var(--border); vertical-align:middle; margin-right:8px; }}
.hero h1 {{ font-size:clamp(1.8rem,5vw,2.6rem); font-weight:700; background:linear-gradient(120deg,#fff 30%,var(--accent) 80%); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; display:inline; vertical-align:middle; }}
.hero .sub {{ color:var(--dim); font-size:.9rem; margin-top:4px; }}
.hero .meta {{ color:var(--dim); font-size:.75rem; margin-top:8px; }}

/* Ranking */
.ranking {{ margin:20px 0; }}
.ranking h2 {{ font-size:1.2rem; margin-bottom:10px; }}
.rank-table {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
.rank-table th {{ text-align:left; color:var(--dim); padding:6px 8px; border-bottom:1px solid var(--border); font-weight:400; letter-spacing:.04em; }}
.rank-table td {{ padding:8px; border-bottom:1px solid rgba(255,255,255,.05); }}
.rank-num {{ font-weight:700; min-width:32px; }}
.rank-name {{ font-weight:600; }}
.rank-county {{ color:var(--dim); font-weight:400; margin-left:6px; font-size:.78rem; }}
.rank-1 .rank-num, .rank-2 .rank-num, .rank-3 .rank-num {{ color:var(--sand); }}
.rank-wh {{ font-weight:700; font-variant-numeric:tabular-nums; }}
.rank-rating {{ white-space:nowrap; }}

/* Spot cards */
.spot-card {{ background:var(--card); border-radius:var(--radius); margin:8px 0; border:1px solid var(--border); overflow:hidden; }}
.spot-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:14px 16px; cursor:pointer; user-select:none; }}
.spot-head:hover {{ background:rgba(79,195,247,.04); }}
.spot-name {{ font-size:1.35rem; font-weight:700; }}
.spot-badge {{ background:rgba(79,195,247,.15); color:var(--accent); padding:2px 8px; border-radius:12px; font-size:.75rem; font-weight:600; }}
.spot-county {{ color:var(--dim); font-size:.85rem; }}
.spot-toggle {{ margin-left:auto; color:var(--dim); font-size:.8rem; transition:transform .2s; }}
.spot-toggle.open {{ transform:rotate(90deg); }}
.spot-summary-row {{ display:flex; align-items:center; gap:12px; padding:0 16px 12px; flex-wrap:wrap; }}
.spot-best {{ color:var(--sand); font-size:.85rem; }}
.spot-today-wh {{ font-weight:800; font-size:1.1rem; color:#fff; }}
.spot-today-ws {{ color:var(--dim); font-size:.85rem; }}
.spot-today-rating {{ font-weight:600; }}
.spot-forecast {{ display:none; padding:0 12px 12px; }}
.spot-forecast.open {{ display:block; }}

/* Day block */
.day-block {{ margin:10px 0; background:var(--card2); border-radius:6px; padding:10px 12px; }}
.day-header {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
.day-date {{ font-weight:700; font-size:.95rem; min-width:80px; }}
.day-summary {{ color:var(--dim); font-size:.82rem; }}
.day-rating {{ font-size:.9rem; font-weight:600; }}
.rating-danger {{ color:#ff4081; }}
.rating-warn {{ color:#ff9800; }}

/* Tide */
.tide-strip {{ display:flex; gap:4px; flex-wrap:wrap; margin:6px 0 8px; font-size:.76rem; }}
.tide-compact {{ color:var(--tide-hi); font-size:.78rem; white-space:nowrap; }}
.tide-item {{ padding:2px 6px; border-radius:10px; white-space:nowrap; }}
.tide-h {{ font-size:.62rem; opacity:.7; margin-left:2px; }}
.tide-high {{ background:rgba(38,166,154,.15); color:var(--tide-hi); }}
.tide-low {{ background:rgba(77,208,225,.12); color:var(--tide-lo); }}

/* Detail table */
.detail-table {{ width:100%; border-collapse:collapse; font-size:.78rem; }}
.detail-table th {{ color:var(--dim); text-align:left; padding:4px 6px; font-weight:400; border-bottom:1px solid rgba(255,255,255,.08); }}
.detail-table td {{ padding:4px 6px; border-bottom:1px solid rgba(255,255,255,.04); }}
.d-time {{ color:var(--dim); font-variant-numeric:tabular-nums; min-width:38px; }}
.d-wh-cell {{ display:flex; align-items:center; gap:2px; min-width:42px; }}
.wh-mini {{ width:3px; height:14px; border-radius:2px; flex-shrink:0; }}
.wh-mini.wh-c1 {{ background:linear-gradient(180deg,var(--wave1),var(--wave2)); }}
.wh-mini.wh-c2 {{ background:linear-gradient(180deg,var(--wave2),var(--wave3)); }}
.wh-mini.wh-c3 {{ background:linear-gradient(180deg,var(--wave3),var(--wave4)); }}
.wh-mini.wh-c4 {{ background:linear-gradient(180deg,#ff4081,#d50000); }}
.wh-mini.wh-c0 {{ background:rgba(255,255,255,.15); }}
.d-wh-num {{ font-weight:800; font-size:.82rem; font-variant-numeric:tabular-nums; color:#fff; text-shadow:0 0 6px rgba(0,0,0,.6); }}

.d-wp {{ font-variant-numeric:tabular-nums; min-width:30px; }}
.d-dir {{ min-width:28px; }}
.d-ws {{ min-width:52px; }}

/* Responsive */
@media(max-width:600px) {{
  .rank-table {{ font-size:.72rem; }}
  .rank-table th, .rank-table td {{ padding:5px 4px; }}
  .detail-table {{ font-size:.68rem; }}
  .detail-table th, .detail-table td {{ padding:3px 3px; }}
  .d-wh-num {{ font-size:.74rem; }}
  .d-time {{ min-width:32px; }}
  .d-wh-cell {{ min-width:36px; }}
  .d-wp {{ min-width:24px; }}
  .d-dir {{ min-width:24px; }}
  .d-ws {{ min-width:44px; }}
}}

/* Footer */
.footer {{ text-align:center; color:var(--dim); font-size:.72rem; margin-top:32px; padding:16px 0; border-top:1px solid var(--border); }}
</style>
</head>
<body>

<div class="hero">
  <h1><img class="hero-logo" src="surf_image.png" alt="浪點台灣"> 浪點台灣</h1>
  <p class="sub">台灣 15 浪點 · {escape(today_label)} · 4 日預報</p>
  <p class="meta">CWA 鄉鎮沿海 + Open-Meteo Marine · 產生時間 {escape(generated)}</p>
</div>

<div class="ranking">
  <h2>🏆 本週衝浪推薦</h2>
  <table class="rank-table">
    <thead><tr><th>#</th><th>浪點</th><th>面</th><th>最佳日</th><th>浪高</th><th>週期</th><th>風速</th><th>評分</th></tr></thead>
    <tbody>{ranking_rows}</tbody>
  </table>
</div>

{spot_cards}

<div class="footer">
  浪點台灣 Surf Taiwan · 資料來源：CWA 鄉鎮沿海預報 + Open-Meteo Marine API<br>
  風速 1 m/s ≈ 1.94 節（kt）· 浪高為有效波高（Significant Wave Height）
</div>

<script>
function toggleSpot(id) {{
  var fc = document.getElementById('fc-' + id);
  var tg = document.getElementById('toggle-' + id);
  if (fc.classList.contains('open')) {{
    fc.classList.remove('open');
    tg.classList.remove('open');
  }} else {{
    fc.classList.add('open');
    tg.classList.add('open');
  }}
}}
// Allow keyboard enter/space to toggle
document.querySelectorAll('.spot-head').forEach(function(el) {{
  el.addEventListener('keydown', function(e) {{
    if (e.key === 'Enter' || e.key === ' ') {{
      e.preventDefault();
      el.click();
    }}
  }});
}});
</script>

</body>
</html>"""


def wave_color_class(wh: float) -> str:
    """Return CSS class for wave height bar. Number is always white via .d-wh-num."""
    if not wh or wh <= 0:
        return "wh-c0"
    if wh < 0.9:
        return "wh-c1"
    if wh < 1.5:
        return "wh-c2"
    if wh < 2.5:
        return "wh-c3"
    return "wh-c4"


# ─── Main ───

if __name__ == "__main__":
    import os
    html = generate_report()
    out_dir = os.path.join(os.path.dirname(__file__), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report generated: {out_path}")
    print(f"File size: {os.path.getsize(out_path)} bytes")
