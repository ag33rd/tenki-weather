# -*- coding: utf-8 -*-
"""
気象庁アメダス過去データ取得スクリプト
山口県宇部観測所（block_no=0778）の昨日の1時間ごとの気温24点を取得する。

取得元:
  https://www.data.jma.go.jp/stats/etrn/view/hourly_a1.php
    ?prec_no=81&block_no=0778&year=YYYY&month=M&day=D

テーブル構造（実HTML確認済み）:
  table id="tablefix1" class="data2_s"
  列順: 時 / 降水量 / 気温(℃) / 露点温度 / 蒸気圧 / 湿度 / 平均風速 / 風向 / 日照 / 降雪 / 積雪
  欠測値は "///" で表記される。
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import requests
from bs4 import BeautifulSoup

# 山口県の prec_no
PREC_NO = 81

# 観測所候補（宇部とその近隣）。最初に成功したものを採用。
# kind:
#   "a1" = アメダス4桁コード（hourly_a1.php / id=tablefix1 / 気温=cells[2]）
#   "s1" = 地上気象観測所5桁コード（hourly_s1.php / class=data2_s / 気温=cells[4]）
STATION_CANDIDATES: list[tuple[str, str, str]] = [
    ("a1", "0778", "宇部"),
    ("a1", "0775", "防府"),
    ("s1", "47762", "下関"),
    ("s1", "47784", "山口"),
]

# Chrome 系 User-Agent（気象庁サイトはデフォルト UA だと弾かれることがある）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

URL_A1 = "https://www.data.jma.go.jp/stats/etrn/view/hourly_a1.php"
URL_S1 = "https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php"


def _parse_temp(text: str) -> Optional[float]:
    """テーブルセル文字列から気温(float)を抽出。欠測は None を返す。"""
    s = text.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    # 欠測表記: "///", "--", "×" など
    if s in ("///", "--", "-", "×", ")"):
        return None
    # 「)」付き値（参考値）の括弧除去 例: "(15.3)" や "15.3 )"
    s = s.strip("()").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_one(
    kind: str, block_no: str, station_name: str, target_date: dt.date
) -> Optional[dict]:
    """1観測所分のHTMLを取得し、気温24点をパース。失敗時は None。

    kind: "a1" (アメダス4桁) または "s1" (地上観測所5桁)
    """
    if kind == "a1":
        base = URL_A1
        temp_idx = 2  # 列順: 時 / 降水量 / 気温 / ...
    else:
        base = URL_S1
        temp_idx = 4  # 列順: 時 / 気圧(現地) / 気圧(海面) / 降水量 / 気温 / ...

    params = {
        "prec_no": PREC_NO,
        "block_no": block_no,
        "year":     target_date.year,
        "month":    target_date.month,
        "day":      target_date.day,
    }
    headers = {"User-Agent": USER_AGENT}
    url = (
        f"{base}?prec_no={PREC_NO}&block_no={block_no}"
        f"&year={target_date.year}&month={target_date.month}&day={target_date.day}"
    )

    try:
        resp = requests.get(base, params=params, headers=headers, timeout=15)
    except requests.RequestException as e:
        print(f"[警告] {station_name}({block_no}) 取得失敗: {e}")
        return None

    if resp.status_code != 200:
        print(f"[警告] {station_name}({block_no}) HTTP {resp.status_code}")
        return None

    resp.encoding = resp.apparent_encoding or resp.encoding
    soup = BeautifulSoup(resp.text, "html.parser")

    # テーブル検出（a1: id="tablefix1" / s1: class="data2_s"）
    if kind == "a1":
        table = soup.find("table", id="tablefix1")
    else:
        table = soup.find("table", class_="data2_s")
        # フォールバック：id でも検索
        if table is None:
            table = soup.find("table", id="tablefix1")

    if table is None:
        print(f"[警告] {station_name}({block_no}) テーブル未検出")
        return None

    data_rows = [r for r in table.find_all("tr") if r.find_all("td")]
    if not data_rows:
        print(f"[警告] {station_name}({block_no}) データ行なし")
        return None

    hourly: list[dict] = []
    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) <= temp_idx:
            continue
        try:
            hour = int(cells[0].get_text(strip=True))
        except ValueError:
            continue
        temp = _parse_temp(cells[temp_idx].get_text())
        hourly.append({"hour": hour, "temp": temp})

    if not hourly:
        return None

    return {
        "date":    target_date.isoformat(),
        "station": station_name,
        "hourly":  hourly,
        "source":  "気象庁",
        "url":     url,
    }


def fetch_yesterday_amedas() -> Optional[dict]:
    """
    昨日の宇部アメダスの1時間ごとの気温（24点）を取得する。

    宇部(0778)で取得失敗した場合、近隣観測所（防府・下関・山口）を順に試行する。
    すべての観測所で「全て欠測」だった場合（公開遅延の可能性）は2日前の日付で再試行する。
    """
    target_dates = [
        dt.date.today() - dt.timedelta(days=1),
        dt.date.today() - dt.timedelta(days=2),  # 公開遅延フォールバック
    ]

    for d in target_dates:
        for kind, block_no, name in STATION_CANDIDATES:
            result = _fetch_one(kind, block_no, name, d)
            if result is not None:
                # 有効な気温データが1件以上あれば成功とみなす
                if any(item["temp"] is not None for item in result["hourly"]):
                    return result
                else:
                    print(f"[情報] {name}({block_no}) は全て欠測。次候補を試します。")
        if d == target_dates[0]:
            print(f"[情報] {d} は全観測所で欠測でした。前日({target_dates[1]})にフォールバックします。")

    print("[エラー] すべての観測所で取得に失敗しました。")
    return None


def _print_result(result: dict) -> None:
    """取得結果を整形して表示。"""
    print(f"取得日:   {result['date']}")
    print(f"観測所:   {result['station']}")
    print(f"出典:     {result['source']}")
    print(f"URL:      {result['url']}")
    print("-" * 40)
    print("時刻  気温(℃)")
    print("-" * 40)
    valid = [h["temp"] for h in result["hourly"] if h["temp"] is not None]
    for item in result["hourly"]:
        h = item["hour"]
        t = item["temp"]
        t_str = f"{t:5.1f}" if t is not None else "  --"
        print(f"{h:>2}時   {t_str}")
    if valid:
        print("-" * 40)
        print(f"最高: {max(valid):.1f}℃ / 最低: {min(valid):.1f}℃ "
              f"/ 平均: {sum(valid)/len(valid):.1f}℃ "
              f"(有効 {len(valid)}/{len(result['hourly'])} 点)")


if __name__ == "__main__":
    res = fetch_yesterday_amedas()
    if res is None:
        raise SystemExit(1)
    _print_result(res)
