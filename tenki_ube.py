# -*- coding: utf-8 -*-
"""
tenki.jp（日本気象協会）から山口県宇部市の天気予報を取得するスクレイピング関数。

依存ライブラリ:
    requests
    beautifulsoup4 (bs4)

Anaconda 環境では標準で同梱されている。
インストールする場合:
    pip install requests beautifulsoup4
    または
    conda install requests beautifulsoup4
"""

import re
import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# tenki.jp 宇部市の URL
URL_TODAY  = "https://tenki.jp/forecast/7/38/8110/35202/"
URL_1HOUR  = "https://tenki.jp/forecast/7/38/8110/35202/1hour.html"
URL_WEEKLY = "https://tenki.jp/forecast/7/38/8110/35202/10days.html"

# tenki.jp はブラウザを偽装した User-Agent が無いとブロックされることがあるため必須
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _to_float(s: Optional[str]) -> Optional[float]:
    """文字列から float を取り出す。失敗したら None。"""
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _to_int(s: Optional[str]) -> Optional[int]:
    """文字列から int を取り出す。失敗したら None。"""
    if s is None:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _fetch(url: str) -> BeautifulSoup:
    """URL を GET して BeautifulSoup オブジェクトを返す。"""
    res = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    # tenki.jp は UTF-8
    res.encoding = res.apparent_encoding or "utf-8"
    return BeautifulSoup(res.text, "html.parser")


def _parse_today(soup: BeautifulSoup) -> dict:
    """今日明日ページから今日の概況・気温・日の出日の入を取り出す。"""
    out = {
        "weather": None,
        "weather_icon_url": None,
        "temp_now": None,
        "temp_diff_yesterday": None,
        "temp_max": None,
        "temp_min": None,
        "sunrise": None,
        "sunset": None,
    }

    # --- 今日の天気概況とアイコン ---
    # <div class="today-weather"> 配下の <p class="weather-telop">晴</p> と <img alt="晴">
    today_box = soup.select_one(".today-weather")
    if today_box is not None:
        telop = today_box.select_one(".weather-telop")
        if telop is not None:
            out["weather"] = telop.get_text(strip=True)
        img = today_box.select_one(".weather-icon img")
        if img is not None:
            # alt は日本語（例: "晴"）。アイコン URL も入れておく
            out["weather_icon_url"] = img.get("src")
            if not out["weather"]:
                out["weather"] = img.get("alt")

    # --- 最高気温・最低気温 ---
    # <dd class="high-temp temp"><span class="value">22</span><span class="unit">℃</span></dd>
    high = soup.select_one(".today-weather .high-temp.temp .value")
    if high is None:
        high = soup.select_one(".high-temp.temp .value")
    low = soup.select_one(".today-weather .low-temp.temp .value")
    if low is None:
        low = soup.select_one(".low-temp.temp .value")
    if high is not None:
        out["temp_max"] = _to_float(high.get_text())
    if low is not None:
        out["temp_min"] = _to_float(low.get_text())

    # --- 日の出 / 日の入 ---
    # <p class="sunrise">日の出｜<span>05時19分</span></p>
    sunrise_el = soup.select_one("p.sunrise span")
    sunset_el = soup.select_one("p.sunset span")
    if sunrise_el is not None:
        m = re.search(r"(\d{1,2})時(\d{1,2})分", sunrise_el.get_text())
        if m:
            out["sunrise"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    if sunset_el is not None:
        m = re.search(r"(\d{1,2})時(\d{1,2})分", sunset_el.get_text())
        if m:
            out["sunset"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"

    # --- 現在気温と前日差 ---
    # ページ下部の実況ブロックに以下のような HTML がある:
    # <li class="temp"><a ...>15.7<span class="unit">℃</span>
    #   <span class="diff">(前日差:-0.8℃)</span></a></li>
    # （注意: 最高/最低の dd にも high-temp/low-temp class があるため、
    #  li.temp に絞る。さらに span.diff の存在で実況ブロックを判定）
    temp_li = None
    for li in soup.select("li.temp"):
        if li.select_one("span.diff") is not None:
            temp_li = li
            break
    if temp_li is None:
        # フォールバック: span.diff を含む要素から親 li を辿る
        diff_el = soup.select_one("span.diff")
        if diff_el is not None:
            temp_li = diff_el.find_parent("li")
    if temp_li is not None:
        text = temp_li.get_text(" ", strip=True)
        # 例: "気温 15.7 ℃ (前日差:-0.8℃)"
        m_now = re.search(r"(-?\d+(?:\.\d+)?)\s*℃", text)
        if m_now:
            out["temp_now"] = float(m_now.group(1))
        m_diff = re.search(r"前日差[:：]?\s*(-?\d+(?:\.\d+)?)\s*℃", text)
        if m_diff:
            out["temp_diff_yesterday"] = float(m_diff.group(1))

    return out


def _parse_hourly(soup: BeautifulSoup) -> tuple:
    """1hour.html から1時間ごとの予報を取得（最大72時間：今日・明日・明後日）。

    tr.hour / tr.weather 等は日数分（1〜3個）存在するため select() で全取得して結合する。

    戻り値: (hourly_list, humidity_now)
    hourly_list の各要素: {"day_offset":int, "hour":int, "temp":float, "weather":str, "pop":int}
      day_offset = 0:今日, 1:明日, 2:明後日
    """
    hourly = []

    hour_trs    = soup.select("tr.hour")
    weather_trs = soup.select("tr.weather")
    temp_trs    = soup.select("tr.temperature")
    pop_trs     = soup.select("tr.prob-precip")
    humid_trs   = soup.select("tr.humidity")
    # 降水量(mm/h)は tr.precipitation。各 td 内 <span>値</span> の構造（実HTML確認済）
    precip_trs  = soup.select("tr.precipitation")

    if not hour_trs:
        return hourly, None

    def cells(tr):
        return tr.find_all("td") if tr is not None else []

    hours_humidity = []  # (day_offset, hour, humidity, is_past)

    for d in range(len(hour_trs)):
        hour_cells    = cells(hour_trs[d])
        weather_cells = cells(weather_trs[d]) if d < len(weather_trs) else []
        temp_cells    = cells(temp_trs[d])    if d < len(temp_trs)    else []
        pop_cells     = cells(pop_trs[d])     if d < len(pop_trs)     else []
        humid_cells   = cells(humid_trs[d])   if d < len(humid_trs)   else []
        precip_cells  = cells(precip_trs[d])  if d < len(precip_trs)  else []

        for i in range(len(hour_cells)):
            # 時刻
            hour_span = hour_cells[i].find("span")
            hour_text = hour_span.get_text(strip=True) if hour_span else hour_cells[i].get_text(strip=True)
            hour_val = _to_int(hour_text)
            # 24時表記は翌日0時 → day_offset も +1 して翌日扱いにする
            next_day_midnight = (hour_val == 24)
            if hour_val == 24:
                hour_val = 0
            is_past = bool(hour_span and "past" in (hour_span.get("class") or []))

            # 天気
            weather_val = None
            if i < len(weather_cells):
                wc = weather_cells[i]
                img = wc.find("img")
                if img is not None and img.get("alt"):
                    weather_val = img.get("alt")
                else:
                    p = wc.find("p")
                    if p is not None:
                        weather_val = p.get_text(strip=True)

            # 気温
            temp_val = None
            if i < len(temp_cells):
                sp = temp_cells[i].find("span")
                temp_val = _to_float(sp.get_text() if sp else temp_cells[i].get_text())

            # 降水確率（"---" は None）
            pop_val = None
            if i < len(pop_cells):
                sp = pop_cells[i].find("span")
                txt = (sp.get_text() if sp else pop_cells[i].get_text()).strip()
                if txt and txt != "---":
                    pop_val = _to_int(txt)

            # 湿度
            humid_val = None
            if i < len(humid_cells):
                sp = humid_cells[i].find("span")
                humid_val = _to_int(sp.get_text() if sp else humid_cells[i].get_text())

            # 降水量(mm/h)
            # 実HTML: <td><span>0</span></td> または <td><span class="past">0</span></td>
            # 「---」が来た場合は None、それ以外は数値（0 含む）を float で返す
            precip_val = None
            if i < len(precip_cells):
                sp = precip_cells[i].find("span")
                txt = (sp.get_text() if sp else precip_cells[i].get_text()).strip()
                if txt and txt != "---":
                    precip_val = _to_float(txt)
                    if precip_val is None:
                        # 数値抽出失敗時は 0.0 ではなく None を維持
                        pass

            hourly.append({
                "day_offset":    d + 1 if next_day_midnight else d,
                "hour":          hour_val,
                "temp":          temp_val,
                "weather":       weather_val,
                "pop":           pop_val,
                "precipitation": precip_val,
            })
            hours_humidity.append((d, hour_val, humid_val, is_past))

    # 現在湿度：past クラス付きセルの最後（=現在に最も近い過去時刻）の値
    humidity_now = None
    past_humids = [h for (_d, _hr, h, p) in hours_humidity if p and h is not None]
    if past_humids:
        humidity_now = past_humids[-1]
    else:
        for (_, _, h, _p) in hours_humidity:
            if h is not None:
                humidity_now = h
                break

    return hourly, humidity_now


def _parse_weekly(soup: BeautifulSoup) -> list:
    """10days.html から週間予報を取得する。

    実HTML確認済の構造:
        <dl class="forecast10days-list">
          <dt class="forecast10days-title">…ヘッダ…</dt>
          <dd class="forecast10days-actab">
            <div class="days">05月09日(<span class="saturday">土</span>)</div>
            <div class="forecast"><img alt="晴">…<span class="forecast-telop">晴</span></div>
            <div class="temp"><span class="high-temp">22℃</span><span class="low-temp">11℃</span></div>
            <div class="prob-precip">0%</div>
            <div class="precip">0mm</div>
            …
          </dd>
          …各日…
        </dl>

    戻り値:
        [{"date":"2026-05-10","weekday":"日","weather":"晴",
          "temp_max":23.0,"temp_min":9.0,"pop":10}, ...]
        date は ISO（YYYY-MM-DD）形式。年は実行時の年を基準とし、
        月の数値が現在月より小さければ翌年として補正（年跨ぎ対応）。
    """
    weekly = []
    today = datetime.date.today()

    items = soup.select("dd.forecast10days-actab")
    for it in items:
        # --- 日付・曜日 ---
        days_div = it.select_one(".days")
        if days_div is None:
            continue
        date_text = days_div.get_text(" ", strip=True)  # 例: "05月09日( 土 )"
        m = re.search(r"(\d{1,2})月\s*(\d{1,2})日", date_text)
        if not m:
            continue
        mon = int(m.group(1))
        day = int(m.group(2))
        # 年跨ぎ補正：取得月が今日の月より大幅に小さければ翌年扱い
        year = today.year
        if mon < today.month - 6:
            year += 1
        elif mon > today.month + 6:
            year -= 1
        try:
            d = datetime.date(year, mon, day)
        except ValueError:
            continue
        # 曜日 span の中身
        wd_span = days_div.find("span")
        weekday = wd_span.get_text(strip=True) if wd_span is not None else ""

        # --- 天気概況 ---
        weather = None
        telop = it.select_one(".forecast .forecast-telop")
        if telop is not None:
            weather = telop.get_text(strip=True)
        else:
            img = it.select_one(".forecast img")
            if img is not None:
                weather = img.get("alt")

        # --- 最高/最低気温 ---
        high_el = it.select_one(".temp .high-temp")
        low_el  = it.select_one(".temp .low-temp")
        temp_max = _to_float(high_el.get_text()) if high_el is not None else None
        temp_min = _to_float(low_el.get_text())  if low_el  is not None else None

        # --- 降水確率 ---
        pop_el = it.select_one(".prob-precip")
        pop = _to_int(pop_el.get_text()) if pop_el is not None else None

        weekly.append({
            "date":     d.isoformat(),
            "weekday":  weekday,
            "weather":  weather,
            "temp_max": temp_max,
            "temp_min": temp_min,
            "pop":      pop,
        })

    return weekly


def fetch_weekly_tenki() -> list:
    """tenki.jp から宇部市の週間予報（10日分）を取得して list で返す。

    取得失敗時は空リストを返す（例外で落とさない方針）。
    """
    try:
        soup = _fetch(URL_WEEKLY)
        return _parse_weekly(soup)
    except Exception as e:
        print(f"[warn] weekly ページ取得/解析失敗: {e}")
        return []


def fetch_weather_tenki() -> dict:
    """tenki.jp から宇部市の天気予報を取得して dict で返す。

    取得失敗した項目は None を入れる（例外で落とさない方針）。
    """
    result = {
        "weather": None,
        "weather_icon_url": None,
        "temp_now": None,
        "temp_diff_yesterday": None,
        "temp_max": None,
        "temp_min": None,
        "humidity": None,
        "sunrise": None,
        "sunset": None,
        "hourly": [],
        "weekly": [],
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "tenki.jp",
    }

    # --- 今日明日ページ ---
    try:
        soup_today = _fetch(URL_TODAY)
        today = _parse_today(soup_today)
        result.update(today)
    except Exception as e:
        # ログだけ出して続行
        print(f"[warn] today ページ取得/解析失敗: {e}")

    # --- 1時間天気ページ ---
    try:
        soup_1h = _fetch(URL_1HOUR)
        hourly, humidity_now = _parse_hourly(soup_1h)
        result["hourly"] = hourly
        result["humidity"] = humidity_now
    except Exception as e:
        print(f"[warn] 1hour ページ取得/解析失敗: {e}")

    # --- 週間予報ページ ---
    result["weekly"] = fetch_weekly_tenki()

    return result


# -------------------- 動作テスト --------------------
if __name__ == "__main__":
    import json

    data = fetch_weather_tenki()

    print("=== 宇部市 天気予報（tenki.jp）===")
    print(f"取得時刻       : {data['fetched_at']}")
    print(f"天気概況       : {data['weather']}")
    print(f"アイコンURL    : {data['weather_icon_url']}")
    print(f"現在気温       : {data['temp_now']} ℃")
    print(f"前日差         : {data['temp_diff_yesterday']} ℃")
    print(f"最高気温       : {data['temp_max']} ℃")
    print(f"最低気温       : {data['temp_min']} ℃")
    print(f"湿度（現在近傍）: {data['humidity']} %")
    print(f"日の出 / 日の入: {data['sunrise']} / {data['sunset']}")
    print(f"時間別件数     : {len(data['hourly'])} 件")
    print("--- 時間別（先頭8件）---")
    for h in data["hourly"][:8]:
        print(
            f"  {h['hour']:>2}時  {h['weather']:<6}  "
            f"{h['temp']}℃  pop={h['pop']}  precip={h['precipitation']}mm"
        )
    print()
    print(f"--- 週間予報（{len(data['weekly'])} 日分）---")
    for w in data["weekly"]:
        print(
            f"  {w['date']} ({w['weekday']})  {w['weather']:<8}  "
            f"最高 {w['temp_max']}℃ / 最低 {w['temp_min']}℃  pop={w['pop']}%"
        )
    print()
    print("--- JSON 全体 ---")
    print(json.dumps(data, ensure_ascii=False, indent=2))
