import streamlit as st
import streamlit_autorefresh
import json
import re
import urllib.request
from datetime import date

from tenki_ube import fetch_weather_tenki
from jma_amedas import fetch_yesterday_amedas

st.set_page_config(
    page_title="天気ダッシュボード - 宇部市",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Streamlit のヘッダー・フッター・各種バッジ・余白を非表示
st.markdown("""
<style>
  #MainMenu, header, footer { display: none !important; }
  .block-container { padding: 0 !important; max-width: 100% !important; }
  /* 右下・右上のStreamlitバッジ／ステータスウィジェット／ツールバーを非表示 */
  [data-testid="stStatusWidget"],
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  [data-testid="manage-app-button"],
  .stAppDeployButton,
  [class*="viewerBadge"],
  [class*="_profileContainer"],
  [class*="_viewerBadge"] {
    display: none !important;
    visibility: hidden !important;
  }
</style>
""", unsafe_allow_html=True)

# 30分ごとに自動リフレッシュ（Python側のキャッシュも更新される）
streamlit_autorefresh.st_autorefresh(interval=30 * 60 * 1000, key="weather_refresh")

# ─── 花粉取得ロジック ──────────────────────────────────────

YAHOO_POLLEN_URL = "https://weather.yahoo.co.jp/weather/pollen/8/35/35202/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LEVEL_RE = r'(極めて多い|非常に多い|やや多い|少ない|多い)'


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", errors="ignore")


def _extract_level_near(html, keyword, window=600):
    m = re.search(rf'{keyword}[\s\S]{{0,{window}}}?{LEVEL_RE}', html)
    if m:
        return m.group(1)
    m = re.search(rf'{LEVEL_RE}[\s\S]{{0,{window}}}?{keyword}', html)
    if m:
        return m.group(1)
    return None


# ─── データ取得（キャッシュ付き） ─────────────────────────

@st.cache_data(ttl=600)   # 10分キャッシュ
def get_weather():
    return fetch_weather_tenki()


@st.cache_data(ttl=3600)  # 1時間キャッシュ
def get_yesterday():
    return fetch_yesterday_amedas()


@st.cache_data(ttl=1800)  # 30分キャッシュ
def get_pollen():
    if date.today().month not in (2, 3, 4):
        return {"sugi": "飛散なし", "hinoki": None, "level": "飛散なし",
                "city": "宇部市", "source": "シーズン外", "off_season": True}
    try:
        html = _http_get(YAHOO_POLLEN_URL)
        sugi   = _extract_level_near(html, 'スギ')
        hinoki = _extract_level_near(html, 'ヒノキ')
        if not sugi and not hinoki:
            return {"error": "抽出失敗"}
        return {"sugi": sugi, "hinoki": hinoki, "level": sugi or hinoki,
                "city": "宇部市", "source": "Yahoo!天気", "url": YAHOO_POLLEN_URL}
    except Exception as e:
        return {"error": str(e)}


# ─── データ取得 ────────────────────────────────────────────

weather   = get_weather()
yesterday = get_yesterday()
pollen    = get_pollen()

# ─── HTML にデータを埋め込んで表示 ───────────────────────

with open("tenki_dashboard.html", "r", encoding="utf-8") as f:
    html = f.read()

# Python取得済みデータをJSグローバル変数として注入
inject = f"""<script>
window._WEATHER_DATA   = {json.dumps(weather,   ensure_ascii=False)};
window._YESTERDAY_DATA = {json.dumps(yesterday, ensure_ascii=False)};
window._POLLEN_DATA    = {json.dumps(pollen,    ensure_ascii=False)};
</script>"""

html = html.replace("</head>", inject + "\n</head>")

st.components.v1.html(html, height=820, scrolling=False)
