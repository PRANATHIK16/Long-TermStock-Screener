"""
Long-Term Stock Screener Dashboard
------------------------------------
Pulls live screener data from Finviz Elite, calculates date-range % returns,
scores news sentiment (short-term) and price/analyst momentum (long-term),
and shows a full detail view with chart and metrics for each ticker.

Setup:
  1. Set your Finviz Elite export URL on line 29 (finviz_url).
  2. pip install dash flask-caching pandas requests yfinance plotly numpy
  3. python "app custom change.py"
  4. Open http://127.0.0.1:8050
"""

import os
import re
import dash
from dash import dcc, html, Input, Output, dash_table, State
import pandas as pd
import requests
from io import StringIO
from flask_caching import Cache
import plotly.graph_objs as go
import yfinance as yf
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Configuration ─────────────────────────────────────────────────────────────

import os

finviz_url = os.environ.get("FINVIZ_URL")
# Replace with your Finviz Elite export URL, e.g.:
# https://elite.finviz.com/export/screener?v=111&f=sec_technology&auth=YOUR-KEY

app = dash.Dash(__name__, suppress_callback_exceptions=True)
cache = Cache(app.server, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 600})

# In-memory caches for calculated values — persist across callbacks within a session
date_range_change_values = {}   # ticker → float (% change over chosen date range)
sentiment_score_cache    = {}   # ticker → {"score": float, "label": str}
longterm_score_cache     = {}   # ticker → {"score": float, "label": str}

ALL_SECTORS = [
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
]

# ── Sentiment keywords ────────────────────────────────────────────────────────
# Only headlines from TRUSTED_SOURCES are scored.
# Bullish keywords → positive signal; bearish → negative signal.

TRUSTED_SOURCES = {
    "pr newswire", "prnewswire", "globe newswire", "globenewswire",
    "accesswire", "mt newswires", "mt wire", "mtnewswires",
    "dow jones", "dowjones", "benzinga", "reuters", "tradingview",
    "sec.gov", "sec", "fda", "u.s. food and drug administration",
}

_BULLISH = {
    "acquisition", "buyback", "buy-back", "contract", "listing",
    "ipo", "merger", "partnership", "earning", "earnings",
    "beat", "beats", "approval", "approved", "fda",
}

_BEARISH = {
    "delisting", "compliance", "miss", "misses", "missed",
    "investigation", "probe", "sec", "bankruptcy", "bankrupcy",
}


# ── Scoring functions ─────────────────────────────────────────────────────────

def score_ticker_sentiment(ticker):
    """
    Short-term (0-100): keyword ratio from recent trusted-source headlines.
    50 = neutral, 100 = all bullish signals, 0 = all bearish signals.
    """
    try:
        news_items = yf.Ticker(ticker).news[:30]
    except:
        return {"score": 50.0, "label": "Neutral", "matched": 0}

    bull, bear, matched = 0, 0, 0
    for item in news_items:
        content = item.get('content', {})
        title  = content.get('title') or item.get('title', '')
        source = (content.get('provider', {}).get('displayName', '') or
                  item.get('publisher', '')).lower()
        if not any(s in source for s in TRUSTED_SOURCES):
            continue
        if not title:
            continue
        words = set(re.findall(r"[a-zA-Z]+", title.lower()))
        b, r = len(words & _BULLISH), len(words & _BEARISH)
        if b > r:   bull += 1; matched += 1
        elif r > b: bear += 1; matched += 1

    total = bull + bear
    score = round((bull / total) * 100, 1) if total > 0 else 50.0

    if score >= 70:   label = "🟢 Bullish"
    elif score >= 55: label = "🟡 Leaning Bull"
    elif score >= 45: label = "⚪ Neutral"
    elif score >= 30: label = "🟠 Leaning Bear"
    else:             label = "🔴 Bearish"
    return {"score": score, "label": label, "matched": matched}


def score_ticker_longterm(ticker):
    """
    Long-term (0-100): composite of price position within 52W range (40%),
    analyst recommendation (30%), and 52W return proxy (30%).
    Uses yfinance fast_info for speed — no full history download needed.
    """
    try:
        stock = yf.Ticker(ticker)
        fi    = stock.fast_info
        scores = []

        try:
            low, high, cur = fi.fifty_two_week_low, fi.fifty_two_week_high, fi.last_price
            if high and low and high != low and cur:
                scores.append(((cur - low) / (high - low)) * 100 * 0.40)
        except:
            scores.append(50 * 0.40)

        try:
            rec = (stock.info or {}).get('recommendationMean')
            scores.append((min(100, max(0, (5 - rec) / 4 * 100)) if rec else 50) * 0.30)
        except:
            scores.append(50 * 0.30)

        try:
            pc, yh, yl = fi.previous_close, fi.fifty_two_week_high, fi.fifty_two_week_low
            if yh and yl and yh != yl and pc:
                scores.append(((pc - yl) / (yh - yl)) * 100 * 0.30)
            else:
                scores.append(50 * 0.30)
        except:
            scores.append(50 * 0.30)

        final = round(sum(scores), 1)
        if final >= 70:   label = "🟢 Strong"
        elif final >= 55: label = "🟡 Positive"
        elif final >= 45: label = "⚪ Neutral"
        elif final >= 30: label = "🟠 Weak"
        else:             label = "🔴 Poor"
        return {"score": final, "label": label}
    except:
        return {"score": 50.0, "label": "Neutral"}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fmt_volume(val):
    """Format raw volume numbers as human-readable strings (1.2M, 500K, etc.)."""
    try:
        v = float(val)
        if v >= 1e9: return f"{v/1e9:.2f}B"
        if v >= 1e6: return f"{v/1e6:.2f}M"
        if v >= 1e3: return f"{v/1e3:.0f}K"
        return str(int(v))
    except:
        return str(val)


@cache.memoize(timeout=600)
def fetch_finviz_data():
    """Fetch the Finviz Elite CSV export and return a clean DataFrame."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://elite.finviz.com/screener.ashx",
    }
    response = requests.get(finviz_url, headers=headers)
    print(f"Finviz fetch status: {response.status_code}")
    print(f"First 300 chars: {response.text[:300]}")
    if response.status_code != 200:
        return pd.DataFrame({"Error": [f"HTTP {response.status_code} — check your Finviz URL"]})
    first_line = response.text.strip().split("\n", 1)[0]
    if "Ticker" not in first_line or first_line.lstrip().startswith("<"):
        return pd.DataFrame({"Error": ["Got HTML instead of CSV — your Finviz auth token has expired. Get a new export URL from elite.finviz.com"]})
    try:
        df = pd.read_csv(StringIO(response.text))
        df.columns = df.columns.map(str)
        if 'Change' in df.columns:
            df = df.drop(columns=['Change'])  # replaced by date-range Change below
        # Numeric columns for filtering and sorting
        for col in ['Market Cap', 'P/E', 'Forward P/E', 'EPS (ttm)', 'EPS (next Y)',
                    'EPS Growth', 'Revenue', 'Operating Margin', 'ROE', 'Debt/Equity',
                    'Beta', 'Price']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        # Keep Volume numeric for filters, format separately for display later
        for col in ['Volume', 'Avg Volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        # Add computed columns (empty until user triggers calculation)
        df['Change']           = 0.0
        df['Sentiment Score']  = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('score', None))
        df['Sentiment']        = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('label', ''))
        df['LT Sentiment Score'] = df['Ticker'].map(lambda t: longterm_score_cache.get(t, {}).get('score', None))
        df = df.reset_index(drop=True)
        print(df.head())
        return df
    except Exception as e:
        return pd.DataFrame({"Error": [str(e)]})


def calculate_date_range_change(ticker, start_date, end_date):
    """% price change from open on start_date to close on end_date."""
    try:
        hist = yf.Ticker(ticker).history(start=start_date, end=end_date, interval='1d')
        if hist.empty or len(hist) < 2:
            return 0.0
        sp, ep = hist['Open'].iloc[0], hist['Close'].iloc[-1]
        return round(((ep - sp) / sp) * 100, 2) if sp != 0 else 0.0
    except:
        return 0.0


def update_date_range_changes(df, start_date, end_date, max_stocks):
    """Calculate date-range % change for up to max_stocks tickers in parallel."""
    global date_range_change_values
    updated_df = df.copy()
    tickers = updated_df['Ticker'].dropna().head(max_stocks).tolist()
    print(f"Calculating date-range change for {len(tickers)} tickers ({start_date} → {end_date})")

    def _calc(ticker):
        pct = calculate_date_range_change(ticker, start_date, end_date)
        date_range_change_values[ticker] = pct
        return ticker, pct

    with ThreadPoolExecutor(max_workers=10) as executor:
        for future in as_completed({executor.submit(_calc, t): t for t in tickers}):
            try:
                ticker, pct = future.result()
                idxs = updated_df.index[updated_df['Ticker'] == ticker].tolist()
                if idxs:
                    updated_df.at[idxs[0], 'Change'] = pct
            except Exception as e:
                print(f"  Error: {e}")
    return updated_df


# ── News sentiment summary (used in detail panel) ─────────────────────────────

def analyze_news_sentiment(news_items):
    """Score a list of news items using trusted sources and keyword matching."""
    bull, bear, bull_h, bear_h = 0, 0, [], []
    for item in news_items:
        content = item.get('content', {})
        title  = content.get('title') or item.get('title', '')
        source = (content.get('provider', {}).get('displayName', '') or
                  item.get('publisher', '')).lower()
        if not any(s in source for s in TRUSTED_SOURCES) or not title:
            continue
        words = set(re.findall(r"[a-zA-Z]+", title.lower()))
        b, r = len(words & _BULLISH), len(words & _BEARISH)
        if b > r:   bull += 1; bull_h.append(title)
        elif r > b: bear += 1; bear_h.append(title)
    net = bull - bear
    total = bull + bear
    if total == 0:
        verdict, color, bg = "No Signals from Trusted Sources", "#888", "#f0f0f0"
    elif net >= 3:
        verdict, color, bg = "Bullish", "#166534", "#dcfce7"
    elif net >= 1:
        verdict, color, bg = "Leaning Bullish", "#15803d", "#bbf7d0"
    elif net == 0:
        verdict, color, bg = "Neutral / Mixed", "#92400e", "#fef3c7"
    elif net >= -2:
        verdict, color, bg = "Leaning Bearish", "#b45309", "#fed7aa"
    else:
        verdict, color, bg = "Bearish", "#991b1b", "#fee2e2"
    return {"verdict": verdict, "color": color, "bg": bg,
            "summary": f"{bull} positive signal(s), {bear} negative signal(s) from trusted sources.",
            "bull_headlines": bull_h[:3], "bear_headlines": bear_h[:3]}


def build_sentiment_summary(news_items):
    """Render the sentiment verdict card shown in the detail panel."""
    r = analyze_news_sentiment(news_items)
    lines = [html.P(r["summary"], style={"marginTop": "8px", "color": "#444", "fontSize": "13px"})]
    if r["bull_headlines"]:
        lines.append(html.Div([
            html.Span("Positive: ", style={"fontWeight": "600", "fontSize": "12px", "color": "#166534"}),
            html.Span(" · ".join(r["bull_headlines"]), style={"fontSize": "12px", "color": "#444"}),
        ], style={"marginTop": "4px"}))
    if r["bear_headlines"]:
        lines.append(html.Div([
            html.Span("Negative: ", style={"fontWeight": "600", "fontSize": "12px", "color": "#991b1b"}),
            html.Span(" · ".join(r["bear_headlines"]), style={"fontSize": "12px", "color": "#444"}),
        ], style={"marginTop": "4px"}))
    return html.Div([
        html.H4("News Sentiment (Trusted Sources)", style={"color": "#333", "marginBottom": "8px", "marginTop": "20px"}),
        html.Span(r["verdict"], style={"backgroundColor": r["bg"], "color": r["color"],
                                        "padding": "4px 12px", "borderRadius": "6px",
                                        "fontWeight": "700", "fontSize": "14px"}),
        html.Div(lines),
        html.P("Keyword-based signal only — not financial advice.",
               style={"fontSize": "11px", "color": "#999", "marginTop": "8px", "fontStyle": "italic"}),
    ], style={"backgroundColor": "#fafafa", "border": "1px solid #e5e7eb",
               "borderRadius": "8px", "padding": "16px", "marginTop": "16px"})


# ── Formatting helper ─────────────────────────────────────────────────────────

def fmt(val):
    if val is None: return 'N/A'
    try:
        f = float(val)
        if abs(f) >= 1e9: return f"{f/1e9:.2f}B"
        if abs(f) >= 1e6: return f"{f/1e6:.2f}M"
        return f"{f:,.2f}"
    except:
        return str(val)


# ── Detail panel (rendered when a ticker row is clicked) ──────────────────────

def build_detail_panel(ticker, start_date=None, end_date=None):
    try:
        stock = yf.Ticker(ticker)
        info  = stock.info
    except:
        info, stock = {}, None

    # Candlestick chart — uses chosen date range or defaults to 1 year
    try:
        hist = (stock.history(start=start_date, end=end_date, interval='1d')
                if start_date and end_date else stock.history(period='1y', interval='1d'))
        hist.reset_index(inplace=True)
        if 'Date' not in hist.columns and 'Datetime' in hist.columns:
            hist.rename(columns={'Datetime': 'Date'}, inplace=True)

        hover_text = [
            f"<b>{str(r['Date'])[:10]}</b><br>"
            f"Open: <b>${r['Open']:.2f}</b><br>High: <b>${r['High']:.2f}</b><br>"
            f"Low: <b>${r['Low']:.2f}</b><br>Close: <b>${r['Close']:.2f}</b><br>"
            f"Volume: {int(r['Volume']):,}"
            for _, r in hist.iterrows()
        ]
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist['Date'], open=hist['Open'], high=hist['High'],
            low=hist['Low'], close=hist['Close'], name='Price',
            text=hover_text, hoverinfo='text',
            increasing_line_color='#22c55e', decreasing_line_color='#ef4444',
        ))
        for window, color, name in [(50, 'orange', 'SMA 50'), (200, 'purple', 'SMA 200')]:
            if len(hist) >= window:
                fig.add_trace(go.Scatter(
                    x=hist['Date'], y=hist['Close'].rolling(window).mean(),
                    mode='lines', name=name, line=dict(color=color, width=1.5),
                    hovertemplate=f'{name}: $%{{y:.2f}}<extra></extra>'
                ))
        if len(hist) >= 2:
            sp, ep = hist['Open'].iloc[0], hist['Close'].iloc[-1]
            pct = ((ep - sp) / sp * 100) if sp != 0 else 0
            col = '#22c55e' if pct >= 0 else '#ef4444'
            fig.add_annotation(x=hist['Date'].iloc[-1], y=hist['High'].max(),
                                text=f"<b>{'+' if pct >= 0 else ''}{pct:.2f}% over period</b>",
                                showarrow=False, font=dict(size=13, color=col),
                                bgcolor='white', bordercolor=col, borderwidth=1, borderpad=4)
        title_range = f"{start_date} → {end_date}" if start_date and end_date else "1 Year Daily"
        fig.update_layout(
            title=f"<b>{ticker}</b> — {title_range}", xaxis_rangeslider_visible=False,
            height=450, margin=dict(l=20, r=20, t=50, b=20),
            paper_bgcolor='white', plot_bgcolor='#fafafa', hovermode='x unified',
            hoverlabel=dict(bgcolor='white', font_size=13, font_family='monospace'),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            xaxis=dict(showgrid=True, gridcolor='#e5e7eb'),
            yaxis=dict(showgrid=True, gridcolor='#e5e7eb', tickprefix='$', tickformat=',.2f'),
        )
        chart = dcc.Graph(figure=fig, config={'displayModeBar': True, 'scrollZoom': True})

        # 30-day price change shown below chart
        try:
            h30 = stock.history(period='1mo', interval='1d')
            if not h30.empty and len(h30) >= 2:
                ps, pe = h30['Open'].iloc[0], h30['Close'].iloc[-1]
                c30 = ((pe - ps) / ps * 100) if ps != 0 else 0
                cc = '#22c55e' if c30 >= 0 else '#ef4444'
                thirty_day = html.Div([
                    html.Span("Price change over last 30 days:  ", style={'fontSize': '14px', 'color': '#555'}),
                    html.Span(f"{'+' if c30 >= 0 else ''}{c30:.2f}%",
                              style={'fontSize': '16px', 'fontWeight': '700', 'color': cc}),
                    html.Span(f"  (${ps:.2f} → ${pe:.2f})",
                              style={'fontSize': '13px', 'color': '#888', 'marginLeft': '6px'}),
                ], style={'padding': '10px 14px', 'backgroundColor': '#f8fafc',
                           'border': '1px solid #e2e8f0', 'borderRadius': '6px', 'marginTop': '8px'})
            else:
                thirty_day = html.Div()
        except:
            thirty_day = html.Div()
    except Exception as e:
        chart = html.P(f"Chart unavailable: {e}", style={'color': 'red'})
        thirty_day = html.Div()

    def g(key, pct=False):
        val = info.get(key)
        if val is None: return 'N/A'
        try: return f"{float(val)*100:.2f}%" if pct else fmt(val)
        except: return str(val)

    rows_left = [
        ("Market Cap",  fmt(info.get('marketCap'))),
        ("P/E",         g('trailingPE')),
        ("Forward P/E", g('forwardPE')),
        ("PEG",         g('trailingPegRatio')),
        ("P/S",         g('priceToSalesTrailing12Months')),
        ("P/B",         g('priceToBook')),
        ("Dividend",    f"{info.get('dividendYield')*100:.2f}%" if info.get('dividendYield') else 'N/A'),
        ("Insider Own", g('heldPercentInsiders', pct=True)),
        ("Short Float", g('shortPercentOfFloat', pct=True)),
        ("Analyst Rec", g('recommendationMean')),
        ("Avg Volume",  fmt(info.get('averageVolume'))),
        ("Beta",        g('beta')),
    ]
    rows_right = [
        ("EPS (TTM)",    g('trailingEps')),
        ("EPS this Y",   g('earningsGrowth', pct=True)),
        ("EPS next Y",   g('forwardEps')),
        ("Sales Q/Q",    g('revenueGrowth', pct=True)),
        ("Gross Margin", g('grossMargins', pct=True)),
        ("Op Margin",    g('operatingMargins', pct=True)),
        ("ROE",          g('returnOnEquity', pct=True)),
        ("ROA",          g('returnOnAssets', pct=True)),
        ("D/E",          g('debtToEquity')),
        ("Inst Own",     g('heldPercentInstitutions', pct=True)),
        ("Target Price", g('targetMeanPrice')),
        ("52W Range",    f"{fmt(info.get('fiftyTwoWeekLow'))} – {fmt(info.get('fiftyTwoWeekHigh'))}"),
    ]

    def metric_col(rows):
        return html.Div([
            html.Div([
                html.Span(lbl, style={'color': '#666', 'fontSize': '12px', 'width': '110px', 'display': 'inline-block'}),
                html.Span(val, style={'fontWeight': '600', 'fontSize': '13px'}),
            ], style={'padding': '5px 8px', 'borderBottom': '1px solid #f0f0f0'})
            for lbl, val in rows
        ], style={'flex': '1'})

    header = html.Div([
        html.Div([
            html.Span(ticker, style={'fontSize': '28px', 'fontWeight': '800', 'marginRight': '12px'}),
            html.Span(f"[{info.get('exchange', '')}]", style={'color': '#555', 'fontSize': '14px'}),
        ]),
        html.Div([html.Span("Company  ", style={'color': '#888', 'fontSize': '13px'}),
                  html.Span(info.get('shortName', ticker), style={'fontWeight': '600', 'color': '#0066cc', 'fontSize': '13px'})]),
        html.Div([html.Span("Country  ", style={'color': '#888', 'fontSize': '13px'}),
                  html.Span(info.get('country', 'N/A'), style={'fontWeight': '600', 'color': '#0066cc', 'fontSize': '13px'})]),
        html.Div([html.Span("Industry  ", style={'color': '#888', 'fontSize': '13px'}),
                  html.Span(info.get('industry', 'N/A'), style={'fontWeight': '600', 'color': '#0066cc', 'fontSize': '13px'})]),
    ], style={'marginBottom': '12px'})

    try:
        news_items = stock.news[:15] if stock else []
    except:
        news_items = []

    news_rows = []
    for item in news_items:
        content  = item.get('content', {})
        title    = content.get('title') or item.get('title', 'No title')
        url      = content.get('canonicalUrl', {}).get('url') or item.get('link', '#')
        source   = content.get('provider', {}).get('displayName') or item.get('publisher', '')
        pub_date = content.get('pubDate', '')
        if pub_date:
            try:
                pub_date = datetime.fromisoformat(pub_date.replace('Z', '+00:00')).strftime('%b %d %I:%M%p')
            except:
                pass
        news_rows.append(html.Div([
            html.Span(pub_date, style={'color': '#888', 'fontSize': '12px', 'minWidth': '140px', 'display': 'inline-block'}),
            html.A(title, href=url, target='_blank',
                   style={'color': '#0066cc', 'fontSize': '13px', 'textDecoration': 'none', 'marginRight': '8px'}),
            html.Span(f"({source})", style={'color': '#888', 'fontSize': '12px'}),
        ], style={'padding': '6px 0', 'borderBottom': '1px solid #f0f0f0'}))

    return html.Div([
        header, chart, thirty_day,
        html.H4("Key Metrics", style={'color': '#333', 'marginTop': '20px', 'marginBottom': '4px'}),
        html.Div([metric_col(rows_left), metric_col(rows_right)], style={'display': 'flex', 'gap': '20px', 'marginTop': '16px'}),
        build_sentiment_summary(news_items),
        html.Div([
            html.H4("Latest News", style={'color': '#007BFF', 'marginBottom': '10px', 'marginTop': '20px'}),
            html.Div(news_rows) if news_rows else html.P("No news available.", style={'color': 'gray'})
        ]),
    ], style={'backgroundColor': 'white', 'border': '1px solid #e0e0e0', 'borderRadius': '8px',
               'padding': '20px', 'marginTop': '20px', 'boxShadow': '0 2px 6px rgba(0,0,0,0.06)'})


# ── Main page layout ──────────────────────────────────────────────────────────

def main_page():
    df = fetch_finviz_data()
    if df.empty or "Error" in df.columns:
        err = df["Error"].iloc[0] if "Error" in df.columns else "No data"
        return html.Div(f"⚠ {err}", style={'textAlign': 'center', 'color': 'red', 'marginTop': '40px'})

    numeric_cols = ['Market Cap', 'P/E', 'Forward P/E', 'EPS (ttm)', 'EPS (next Y)',
                    'EPS Growth', 'Revenue', 'Operating Margin', 'ROE', 'Debt/Equity',
                    'Beta', 'Change', 'Price', 'Sentiment Score', 'LT Sentiment Score']
    display_columns = list(df.columns)

    # Format Volume for display (done here rather than in fetch so numeric filters still work)
    df_display = df.copy()
    for vcol in ['Volume', 'Avg Volume']:
        if vcol in df_display.columns:
            df_display[vcol] = df_display[vcol].apply(lambda x: fmt_volume(x) if pd.notna(x) else '')

    data_sectors  = sorted(df['Sector'].dropna().unique().tolist()) if 'Sector' in df.columns else []
    sector_options = [{'label': s, 'value': s} for s in sorted(set(ALL_SECTORS + data_sectors))]

    def dd(id_, label, options):
        return html.Div([
            html.Label(label),
            dcc.Dropdown(id=id_, placeholder='Any', options=options, style={'width': '100%'})
        ], style={'display': 'inline-block', 'paddingRight': '8px'})

    return html.Div([
        html.H1("Stock Screener", style={'textAlign': 'center', 'color': '#007BFF'}),

        # Top controls
        html.Div([
            html.Button("Refresh Data", id="refresh-button", n_clicks=0,
                        style={'backgroundColor': '#007BFF', 'color': 'white',
                               'padding': '10px 20px', 'borderRadius': '5px', 'marginRight': '20px'}),
            dcc.RadioItems(id='refresh-interval-radio',
                           options=[{'label': '10s', 'value': 10}, {'label': '1min', 'value': 60}, {'label': 'off', 'value': 0}],
                           value=0, labelStyle={'marginRight': '20px', 'display': 'inline-block'}),
        ], style={'marginBottom': '20px'}),
        dcc.Interval(id='refresh-interval', interval=0, n_intervals=0),

        # Date Range % Change
        html.Div([
            html.H3("Date Range % Change", style={'marginBottom': '8px'}),
            html.P("Pick start/end dates, set # stocks, then click Calculate Change. "
                   "The Change column shows % return from open on start date to close on end date.",
                   style={'fontSize': '13px', 'color': '#555', 'marginBottom': '12px'}),
            html.Div([
                html.Div([html.Label("Start Date:"),
                          dcc.DatePickerSingle(id='chart-start-date', display_format='YYYY-MM-DD',
                                               date=str(date(date.today().year - 1, date.today().month, date.today().day)))],
                         style={'display': 'inline-block', 'marginRight': '24px'}),
                html.Div([html.Label("End Date:"),
                          dcc.DatePickerSingle(id='chart-end-date', display_format='YYYY-MM-DD',
                                               date=str(date.today()))],
                         style={'display': 'inline-block', 'marginRight': '24px'}),
                html.Div([html.Label("# Stocks:"),
                          dcc.Slider(id='date-range-stocks-slider', min=5, max=200, step=5, value=50,
                                     marks={5: '5', 50: '50', 100: '100', 200: 'All'})],
                         style={'display': 'inline-block', 'width': '220px', 'verticalAlign': 'bottom', 'marginRight': '24px'}),
                html.Div([html.Br(),
                          html.Button("Calculate Change", id="calc-range-change-button", n_clicks=0,
                                      style={'backgroundColor': '#7c3aed', 'color': 'white',
                                             'padding': '10px 18px', 'borderRadius': '5px', 'fontWeight': '700'})],
                         style={'display': 'inline-block', 'verticalAlign': 'bottom'}),
            ]),
            html.Div(id='range-change-status', style={'color': '#7c3aed', 'fontSize': '13px', 'marginTop': '8px'}),
        ], style={'marginBottom': '20px', 'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '5px'}),

        # Filters
        html.Div([
            html.H3("Filters", style={'marginBottom': '14px'}),
            # Row 1
            html.Div([
                html.Div(dd('filter-pe', 'P/E', [
                    {'label': 'Profitable (>0)', 'value': 'pos'}, {'label': 'Low (<15)', 'value': 'low'},
                    {'label': 'Under 20', 'value': 'u20'}, {'label': 'Under 30', 'value': 'u30'},
                    {'label': 'Under 50', 'value': 'u50'}, {'label': 'High (>50)', 'value': 'high'},
                    {'label': 'Over 100', 'value': 'o100'}]), style={'width': '13%'}),
                html.Div(dd('filter-mktcap', 'Market Cap', [
                    {'label': 'Mega (>200B)', 'value': 'mega'}, {'label': 'Large (10B-200B)', 'value': 'large'},
                    {'label': 'Mid (2B-10B)', 'value': 'mid'}, {'label': 'Small (300M-2B)', 'value': 'small'},
                    {'label': 'Micro (50M-300M)', 'value': 'micro'}, {'label': 'Nano (<50M)', 'value': 'nano'},
                    {'label': '+Large (>10B)', 'value': 'largeover'}, {'label': '+Mid (>2B)', 'value': 'midover'}]),
                         style={'width': '15%'}),
                html.Div(dd('filter-price', 'Price ($)', [
                    {'label': 'Under $5', 'value': 'u5'}, {'label': 'Under $10', 'value': 'u10'},
                    {'label': 'Under $20', 'value': 'u20'}, {'label': 'Under $50', 'value': 'u50'},
                    {'label': 'Over $5', 'value': 'o5'}, {'label': 'Over $10', 'value': 'o10'},
                    {'label': 'Over $20', 'value': 'o20'}, {'label': 'Over $50', 'value': 'o50'},
                    {'label': 'Over $100', 'value': 'o100'}]), style={'width': '12%'}),
                html.Div(dd('filter-avgvol', 'Avg Volume', [
                    {'label': 'Under 100K', 'value': 'u100k'}, {'label': 'Under 500K', 'value': 'u500k'},
                    {'label': 'Over 100K', 'value': 'o100k'}, {'label': 'Over 500K', 'value': 'o500k'},
                    {'label': 'Over 1M', 'value': 'o1m'}, {'label': 'Over 2M', 'value': 'o2m'}]),
                         style={'width': '12%'}),
                html.Div(dd('filter-relvol', 'Relative Volume', [
                    {'label': 'Over 10x', 'value': 'o10'}, {'label': 'Over 5x', 'value': 'o5'},
                    {'label': 'Over 3x', 'value': 'o3'}, {'label': 'Over 2x', 'value': 'o2'},
                    {'label': 'Over 1x', 'value': 'o1'}, {'label': 'Under 1x', 'value': 'u1'}]),
                         style={'width': '12%'}),
                html.Div(dd('filter-curvol', 'Current Volume', [
                    {'label': 'Over 100K', 'value': 'o100k'}, {'label': 'Over 500K', 'value': 'o500k'},
                    {'label': 'Over 1M', 'value': 'o1m'}, {'label': 'Over 5M', 'value': 'o5m'},
                    {'label': 'Over 10M', 'value': 'o10m'}]), style={'width': '12%'}),
                html.Div(dd('filter-sector', 'Sector', sector_options), style={'width': '22%'}),
            ], style={'display': 'flex', 'marginBottom': '10px'}),
            # Row 2
            html.Div([
                html.Div(dd('filter-shortfloat', 'Short Float', [
                    {'label': 'Low (<5%)', 'value': 'low'}, {'label': 'Over 10%', 'value': 'o10'},
                    {'label': 'Over 20%', 'value': 'o20'}, {'label': 'Over 30%', 'value': 'o30'}]),
                         style={'width': '12%'}),
                html.Div(dd('filter-sharesout', 'Shares Outstanding', [
                    {'label': 'Under 10M', 'value': 'u10m'}, {'label': 'Under 50M', 'value': 'u50m'},
                    {'label': 'Under 100M', 'value': 'u100m'}, {'label': 'Over 10M', 'value': 'o10m'},
                    {'label': 'Over 100M', 'value': 'o100m'}, {'label': 'Over 500M', 'value': 'o500m'},
                    {'label': 'Over 1B', 'value': 'o1b'}]), style={'width': '14%'}),
                html.Div(dd('filter-instowner', 'Inst Ownership', [
                    {'label': 'Low (<5%)', 'value': 'low'}, {'label': 'High (>90%)', 'value': 'high'},
                    {'label': 'Over 50%', 'value': 'o50'}, {'label': 'Over 70%', 'value': 'o70'},
                    {'label': 'Over 90%', 'value': 'o90'}]), style={'width': '13%'}),
                html.Div(dd('filter-insiderown', 'Insider Ownership', [
                    {'label': 'Low (<5%)', 'value': 'low'}, {'label': 'High (>30%)', 'value': 'high'},
                    {'label': 'Over 10%', 'value': 'o10'}, {'label': 'Over 20%', 'value': 'o20'},
                    {'label': 'Over 30%', 'value': 'o30'}]), style={'width': '13%'}),
                html.Div(dd('filter-change', 'Change (%)', [
                    {'label': 'Up >5%', 'value': 'u5'}, {'label': 'Up >10%', 'value': 'u10'},
                    {'label': 'Up >20%', 'value': 'u20'}, {'label': 'Down >5%', 'value': 'd5'},
                    {'label': 'Down >10%', 'value': 'd10'}, {'label': 'Down >20%', 'value': 'd20'}]),
                         style={'width': '12%'}),
                html.Div(dd('filter-changefromopen', 'Change from Open', [
                    {'label': 'Up >1%', 'value': 'u1'}, {'label': 'Up >3%', 'value': 'u3'},
                    {'label': 'Up >5%', 'value': 'u5'}, {'label': 'Down >1%', 'value': 'd1'},
                    {'label': 'Down >3%', 'value': 'd3'}, {'label': 'Down >5%', 'value': 'd5'}]),
                         style={'width': '13%'}),
                html.Div(dd('filter-afterhours', 'After Hours', [
                    {'label': 'Up', 'value': 'up'}, {'label': 'Down', 'value': 'down'},
                    {'label': 'Up >2%', 'value': 'u2'}, {'label': 'Up >5%', 'value': 'u5'},
                    {'label': 'Down >2%', 'value': 'd2'}, {'label': 'Down >5%', 'value': 'd5'}]),
                         style={'width': '12%'}),
            ], style={'display': 'flex', 'marginBottom': '10px'}),
            # Row 3
            html.Div([
                html.Div(dd('filter-optshort', 'Option/Short', [
                    {'label': 'Optionable', 'value': 'optionable'}, {'label': 'Shortable', 'value': 'shortable'},
                    {'label': 'Short >10%', 'value': 's10'}, {'label': 'Short >20%', 'value': 's20'},
                    {'label': 'Short >30%', 'value': 's30'}]), style={'width': '13%'}),
                html.Div(dd('filter-earnings', 'Earnings Date', [
                    {'label': 'Today', 'value': 'today'}, {'label': 'This Week', 'value': 'thisweek'},
                    {'label': 'Next Week', 'value': 'nextweek'}, {'label': 'This Month', 'value': 'thismonth'}]),
                         style={'width': '13%'}),
                html.Div(dd('filter-div-dropdown', 'Dividend', [
                    {'label': 'None (0%)', 'value': 'none'}, {'label': 'Positive', 'value': 'pos'},
                    {'label': 'Over 1%', 'value': 'o1'}, {'label': 'Over 2%', 'value': 'o2'},
                    {'label': 'Over 3%', 'value': 'o3'}, {'label': 'Over 5%', 'value': 'o5'},
                    {'label': 'High (>5%)', 'value': 'high'}]), style={'width': '12%'}),
                html.Div(dd('filter-signal', 'Signal', [
                    {'label': 'Top Gainers', 'value': 'topgainers'}, {'label': 'Top Losers', 'value': 'toplosers'},
                    {'label': 'New High', 'value': 'newhigh'}, {'label': 'New Low', 'value': 'newlow'},
                    {'label': 'Most Active', 'value': 'mostactive'}, {'label': 'Unusual Volume', 'value': 'unusualvol'},
                    {'label': 'Most Volatile', 'value': 'mostvolatile'},
                    {'label': 'Overbought', 'value': 'overbought'}, {'label': 'Oversold', 'value': 'oversold'}]),
                         style={'width': '14%'}),
                html.Div(dd('filter-sentiment', 'News Sentiment', [
                    {'label': '🟢 Bullish (≥70)', 'value': 'bullish'}, {'label': '🟡 Leaning Bull (55-70)', 'value': 'leanbull'},
                    {'label': '⚪ Neutral (45-55)', 'value': 'neutral'}, {'label': '🟠 Leaning Bear (30-45)', 'value': 'leanbear'},
                    {'label': '🔴 Bearish (<30)', 'value': 'bearish'},
                    {'label': 'Positive (≥55)', 'value': 'pos'}, {'label': 'Negative (<45)', 'value': 'neg'}]),
                         style={'width': '15%'}),
                html.Div(dd('filter-insttrans', 'Inst Transactions', [
                    {'label': 'Buying (>0%)', 'value': 'pos'}, {'label': 'Selling (<0%)', 'value': 'neg'},
                    {'label': 'Strong Buying (>5%)', 'value': 'vpos'}, {'label': 'Strong Selling (<-5%)', 'value': 'vneg'}]),
                         style={'width': '13%'}),
            ], style={'display': 'flex', 'marginBottom': '12px'}),

            # Sentiment score button
            html.Div([
                html.Button("Calculate Sentiment Score", id="calc-sentiment-button", n_clicks=0,
                            style={'backgroundColor': '#0ea5e9', 'color': 'white',
                                   'padding': '8px 16px', 'borderRadius': '5px', 'fontWeight': '600', 'marginRight': '10px'}),
                html.Span("Scores news sentiment (0-100) and long-term momentum for each ticker. Runs in parallel.",
                          style={'fontSize': '12px', 'color': '#666'}),
            ], style={'marginBottom': '10px'}),
            html.Div(id='sentiment-calc-status',
                     style={'color': '#0ea5e9', 'fontSize': '13px', 'fontWeight': '500', 'marginBottom': '12px'}),

            html.Div([
                html.Button("Apply Filters", id="apply-filters-button", n_clicks=0,
                            style={'backgroundColor': '#28a745', 'color': 'white',
                                   'padding': '8px 18px', 'borderRadius': '5px', 'fontWeight': '600', 'marginRight': '10px'}),
                html.Button("Clear Filters", id="clear-filters-button", n_clicks=0,
                            style={'backgroundColor': '#94a3b8', 'color': 'white',
                                   'padding': '8px 18px', 'borderRadius': '5px', 'fontWeight': '600'}),
                html.Span(id='filter-results-count', style={'marginLeft': '14px', 'color': '#475569', 'fontSize': '13px'}),
            ]),
        ], style={'marginBottom': '20px', 'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '5px'}),

        # Sort
        html.Div([
            html.Label("Sort By:"),
            dcc.Dropdown(id='sort-by-dropdown',
                         options=[{'label': col, 'value': col} for col in display_columns],
                         value='Change',
                         style={'width': '45%', 'display': 'inline-block', 'marginRight': '10px'}),
            dcc.RadioItems(id='sort-order',
                           options=[{'label': 'Ascending', 'value': 'asc'}, {'label': 'Descending', 'value': 'desc'}],
                           value='desc', style={'display': 'inline-block'}),
        ], style={'marginBottom': '20px'}),

        html.Div(id='processing-status', style={'marginBottom': '10px', 'color': '#007BFF', 'fontWeight': 'bold'}),

        dash_table.DataTable(
            id='main-table',
            columns=[{"name": col, "id": col,
                      "type": "numeric" if col in numeric_cols else "text",
                      "format": {"specifier": ".2f"} if col in ['Change', 'Sentiment Score', 'LT Sentiment Score'] else None}
                     for col in display_columns],
            data=df_display.to_dict('records'),
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'padding': '5px'},
            style_header={'backgroundColor': '#f4f4f4', 'fontWeight': 'bold'},
            style_data_conditional=[
                {'if': {'column_id': 'Ticker'}, 'cursor': 'pointer', 'color': 'blue', 'textDecoration': 'underline'},
                {'if': {'filter_query': '{Change} > 0', 'column_id': 'Change'}, 'backgroundColor': '#E8F5E9', 'color': 'green'},
                {'if': {'filter_query': '{Change} < 0', 'column_id': 'Change'}, 'backgroundColor': '#FFEBEE', 'color': 'red'},
                {'if': {'filter_query': '{Change} > 50', 'column_id': 'Change'}, 'backgroundColor': '#15803d', 'color': 'white'},
                {'if': {'filter_query': '{Change} < -20', 'column_id': 'Change'}, 'backgroundColor': '#991b1b', 'color': 'white'},
            ],
            page_size=15,
            page_action='native',
            sort_action='native',
            filter_action='native',
        ),

        html.Div(id='detail-panel', style={'marginTop': '10px'}),
        dcc.Store(id='full-data-store', data=df.to_dict('records')),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    [Output('main-table', 'data'),
     Output('refresh-interval', 'interval'),
     Output('processing-status', 'children'),
     Output('full-data-store', 'data'),
     Output('filter-results-count', 'children'),
     Output('range-change-status', 'children')],
    [Input('refresh-button', 'n_clicks'),
     Input('refresh-interval-radio', 'value'),
     Input('sort-by-dropdown', 'value'),
     Input('sort-order', 'value'),
     Input('refresh-interval', 'n_intervals'),
     Input('apply-filters-button', 'n_clicks'),
     Input('clear-filters-button', 'n_clicks'),
     Input('calc-range-change-button', 'n_clicks'),
     Input('calc-sentiment-button', 'n_clicks')],
    [State('filter-pe', 'value'), State('filter-mktcap', 'value'),
     State('filter-price', 'value'), State('filter-avgvol', 'value'),
     State('filter-relvol', 'value'), State('filter-curvol', 'value'),
     State('filter-sector', 'value'), State('filter-shortfloat', 'value'),
     State('filter-sharesout', 'value'), State('filter-instowner', 'value'),
     State('filter-insiderown', 'value'), State('filter-change', 'value'),
     State('filter-signal', 'value'), State('filter-div-dropdown', 'value'),
     State('filter-earnings', 'value'), State('filter-changefromopen', 'value'),
     State('filter-afterhours', 'value'), State('filter-optshort', 'value'),
     State('filter-sentiment', 'value'), State('filter-insttrans', 'value'),
     State('chart-start-date', 'date'), State('chart-end-date', 'date'),
     State('date-range-stocks-slider', 'value')]
)
def update_main_table(n_clicks, refresh_value, sort_by, sort_order,
                      n_intervals, apply_filters_clicks, clear_filters_clicks,
                      calc_range_clicks, calc_sentiment_clicks,
                      f_pe, f_mktcap, f_price, f_avgvol, f_relvol, f_curvol,
                      f_sector, f_shortfloat, f_sharesout, f_instowner,
                      f_insiderown, f_change, f_signal, f_div,
                      f_earnings, f_changefromopen, f_afterhours,
                      f_optshort, f_sentiment, f_insttrans,
                      start_date, end_date, date_range_stocks):

    df = fetch_finviz_data()
    if df.empty or "Error" in df.columns:
        return [], (refresh_value * 1000 if refresh_value > 0 else 0), "No data", [], "", ""

    # Re-inject cached computed values on every update
    df['Change'] = df['Ticker'].map(date_range_change_values).fillna(0.0)
    if sentiment_score_cache:
        df['Sentiment Score'] = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('score', None))
        df['Sentiment'] = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('label', ''))
    if longterm_score_cache:
        df['LT Sentiment Score'] = df['Ticker'].map(lambda t: longterm_score_cache.get(t, {}).get('score', None))

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else ''
    range_status = ""

    # Date-range % change calculation
    if trigger_id == 'calc-range-change-button' and start_date and end_date:
        df = update_date_range_changes(df, start_date, end_date, date_range_stocks)
        df['Change'] = df['Ticker'].map(date_range_change_values).fillna(0.0)
        range_status = f"✅ Change column updated for {date_range_stocks} stocks: {start_date} → {end_date}"
        sort_by, sort_order = 'Change', 'desc'

    # Sentiment scoring (parallel)
    if trigger_id == 'calc-sentiment-button':
        tickers = df['Ticker'].dropna().head(date_range_stocks).tolist()
        print(f"Scoring sentiment for {len(tickers)} tickers...")

        def _score(t):
            sentiment_score_cache[t] = score_ticker_sentiment(t)
            longterm_score_cache[t]  = score_ticker_longterm(t)

        with ThreadPoolExecutor(max_workers=20) as ex:
            for fut in as_completed({ex.submit(_score, t): t for t in tickers}):
                try: fut.result()
                except Exception as e: print(f"  Error: {e}")

        df['Sentiment Score']    = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('score', None))
        df['Sentiment']          = df['Ticker'].map(lambda t: sentiment_score_cache.get(t, {}).get('label', ''))
        df['LT Sentiment Score'] = df['Ticker'].map(lambda t: longterm_score_cache.get(t, {}).get('score', None))

        # Rank: #1 = best sentiment
        for rank_col, score_col in [('Sentiment Rank', 'Sentiment Score'), ('LT Sentiment Rank', 'LT Sentiment Score')]:
            scored = df[df[score_col].notna()].copy()
            if not scored.empty:
                scored[rank_col] = scored[score_col].rank(ascending=False, method='min').astype(int)
                df = df.merge(scored[['Ticker', rank_col]], on='Ticker', how='left')

        sort_by, sort_order = 'Sentiment Score', 'desc'

    total_before = len(df)

    # Apply all filters (skipped if Clear was clicked)
    if trigger_id != 'clear-filters-button':
        def nc(col):
            return pd.to_numeric(df[col], errors='coerce') if col in df.columns else None

        if f_pe and 'P/E' in df.columns:
            pe = nc('P/E')
            if f_pe == 'pos':   df = df[pe > 0]
            elif f_pe == 'low': df = df[(pe > 0) & (pe < 15)]
            elif f_pe == 'u20': df = df[(pe > 0) & (pe < 20)]
            elif f_pe == 'u30': df = df[(pe > 0) & (pe < 30)]
            elif f_pe == 'u50': df = df[(pe > 0) & (pe < 50)]
            elif f_pe == 'high': df = df[pe > 50]
            elif f_pe == 'o100': df = df[pe > 100]

        if f_mktcap and 'Market Cap' in df.columns:
            mc = nc('Market Cap')
            if f_mktcap == 'mega':       df = df[mc >= 200e9]
            elif f_mktcap == 'large':    df = df[(mc >= 10e9) & (mc < 200e9)]
            elif f_mktcap == 'mid':      df = df[(mc >= 2e9) & (mc < 10e9)]
            elif f_mktcap == 'small':    df = df[(mc >= 300e6) & (mc < 2e9)]
            elif f_mktcap == 'micro':    df = df[(mc >= 50e6) & (mc < 300e6)]
            elif f_mktcap == 'nano':     df = df[mc < 50e6]
            elif f_mktcap == 'largeover': df = df[mc >= 10e9]
            elif f_mktcap == 'midover':   df = df[mc >= 2e9]

        if f_price and 'Price' in df.columns:
            pr = nc('Price')
            thresholds = {'u5': ('<', 5), 'u10': ('<', 10), 'u20': ('<', 20), 'u50': ('<', 50),
                          'o5': ('>', 5), 'o10': ('>', 10), 'o20': ('>', 20), 'o50': ('>', 50), 'o100': ('>', 100)}
            if f_price in thresholds:
                op, val = thresholds[f_price]
                df = df[pr < val] if op == '<' else df[pr > val]

        avg_vol_col = 'Avg Volume' if 'Avg Volume' in df.columns else None
        vol_col     = 'Volume' if 'Volume' in df.columns else None

        if f_avgvol and avg_vol_col:
            av = nc(avg_vol_col)
            thresholds = {'u100k': ('<', 100e3), 'u500k': ('<', 500e3), 'o100k': ('>', 100e3),
                          'o500k': ('>', 500e3), 'o1m': ('>', 1e6), 'o2m': ('>', 2e6)}
            if f_avgvol in thresholds:
                op, val = thresholds[f_avgvol]
                df = df[av < val] if op == '<' else df[av > val]

        if f_curvol and vol_col:
            cv = nc(vol_col)
            thresholds = {'o100k': 100e3, 'o500k': 500e3, 'o1m': 1e6, 'o5m': 5e6, 'o10m': 10e6}
            if f_curvol in thresholds:
                df = df[cv > thresholds[f_curvol]]

        if f_relvol and avg_vol_col and vol_col:
            rv = nc(vol_col) / nc(avg_vol_col).replace(0, float('nan'))
            thresholds = {'o10': 10, 'o5': 5, 'o3': 3, 'o2': 2, 'o1': 1, 'u1': None}
            if f_relvol == 'u1':  df = df[rv < 1]
            elif f_relvol in thresholds and thresholds[f_relvol]: df = df[rv > thresholds[f_relvol]]

        if f_sector and 'Sector' in df.columns:
            df = df[df['Sector'] == f_sector]

        if f_shortfloat and 'Short Float' in df.columns:
            sf = pd.to_numeric(df['Short Float'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            opts = {'low': ('<', 5), 'u10': ('<', 10), 'o10': ('>', 10), 'o20': ('>', 20), 'o30': ('>', 30)}
            if f_shortfloat in opts:
                op, val = opts[f_shortfloat]
                df = df[sf < val] if op == '<' else df[sf > val]

        if f_sharesout and 'Shares Outstanding' in df.columns:
            def parse_shares(s):
                s = str(s).strip().upper()
                try:
                    if s.endswith('B'): return float(s[:-1]) * 1e9
                    if s.endswith('M'): return float(s[:-1]) * 1e6
                    if s.endswith('K'): return float(s[:-1]) * 1e3
                    return float(s)
                except: return float('nan')
            so = df['Shares Outstanding'].apply(parse_shares)
            thresholds = {'u10m': ('<', 10e6), 'u50m': ('<', 50e6), 'u100m': ('<', 100e6),
                          'o10m': ('>', 10e6), 'o100m': ('>', 100e6), 'o500m': ('>', 500e6), 'o1b': ('>', 1e9)}
            if f_sharesout in thresholds:
                op, val = thresholds[f_sharesout]
                df = df[so < val] if op == '<' else df[so > val]

        if f_instowner and 'Inst Own' in df.columns:
            io = pd.to_numeric(df['Inst Own'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            opts = {'low': ('<', 5), 'high': ('>', 90), 'o50': ('>', 50), 'o70': ('>', 70), 'o90': ('>', 90)}
            if f_instowner in opts:
                op, val = opts[f_instowner]
                df = df[io < val] if op == '<' else df[io > val]

        if f_insiderown and 'Insider Own' in df.columns:
            ii = pd.to_numeric(df['Insider Own'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            opts = {'low': ('<', 5), 'high': ('>', 30), 'o10': ('>', 10), 'o20': ('>', 20), 'o30': ('>', 30)}
            if f_insiderown in opts:
                op, val = opts[f_insiderown]
                df = df[ii < val] if op == '<' else df[ii > val]

        if f_change and 'Change' in df.columns:
            ch = nc('Change')
            opts = {'u5': ('>', 5), 'u10': ('>', 10), 'u20': ('>', 20),
                    'd5': ('<', -5), 'd10': ('<', -10), 'd20': ('<', -20)}
            if f_change in opts:
                op, val = opts[f_change]
                df = df[ch < val] if op == '<' else df[ch > val]

        if f_changefromopen and 'Change from Open' in df.columns:
            cfo = pd.to_numeric(df['Change from Open'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            opts = {'u1': ('>', 1), 'u3': ('>', 3), 'u5': ('>', 5), 'd1': ('<', -1), 'd3': ('<', -3), 'd5': ('<', -5)}
            if f_changefromopen in opts:
                op, val = opts[f_changefromopen]
                df = df[cfo < val] if op == '<' else df[cfo > val]

        if f_afterhours and 'After-Hours Close' in df.columns and 'Price' in df.columns:
            pr = pd.to_numeric(df['Price'], errors='coerce')
            ah = pd.to_numeric(df['After-Hours Close'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            ah_chg = ((ah - pr) / pr * 100).where(pr != 0)
            opts = {'up': ('>', 0), 'down': ('<', 0), 'u2': ('>', 2), 'u5': ('>', 5), 'd2': ('<', -2), 'd5': ('<', -5)}
            if f_afterhours in opts:
                op, val = opts[f_afterhours]
                df = df[ah_chg < val] if op == '<' else df[ah_chg > val]

        if f_optshort and 'Optionable' in df.columns:
            if f_optshort == 'optionable': df = df[df['Optionable'].astype(str).str.lower() == 'yes']
            elif f_optshort == 'shortable' and 'Shortable' in df.columns:
                df = df[df['Shortable'].astype(str).str.lower() == 'yes']
        if f_optshort in ('s10', 's20', 's30') and 'Short Float' in df.columns:
            sf2 = pd.to_numeric(df['Short Float'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            df  = df[sf2 > {'s10': 10, 's20': 20, 's30': 30}[f_optshort]]

        if f_div and 'Dividend' in df.columns:
            dv = pd.to_numeric(df['Dividend'].astype(str).str.replace('%', '', regex=False), errors='coerce').fillna(0)
            opts = {'none': ('==', 0), 'pos': ('>', 0), 'o1': ('>', 1), 'o2': ('>', 2), 'o3': ('>', 3), 'o5': ('>', 5), 'high': ('>', 5)}
            if f_div in opts:
                op, val = opts[f_div]
                df = df[dv == val] if op == '==' else df[dv > val]

        if f_signal and 'Change' in df.columns:
            ch = nc('Change')
            if f_signal == 'topgainers':  df = df.nlargest(50, 'Change')
            elif f_signal == 'toplosers': df = df.nsmallest(50, 'Change')
            elif f_signal == 'mostactive' and vol_col:    df = df.nlargest(50, vol_col)
            elif f_signal == 'mostvolatile' and 'Beta' in df.columns: df = df.nlargest(50, 'Beta')
            elif f_signal == 'overbought': df = df[ch > 10]
            elif f_signal == 'oversold':   df = df[ch < -10]
            elif f_signal == 'unusualvol' and avg_vol_col and vol_col:
                df = df[nc(vol_col) / nc(avg_vol_col).replace(0, float('nan')) > 2]
            elif f_signal == 'newhigh' and 'Price' in df.columns and '52W High' in df.columns:
                df = df[nc('Price') >= nc('52W High') * 0.99]
            elif f_signal == 'newlow' and 'Price' in df.columns and '52W Low' in df.columns:
                df = df[nc('Price') <= nc('52W Low') * 1.01]

        if f_sentiment and 'Sentiment Score' in df.columns:
            ss = nc('Sentiment Score')
            opts = {'bullish': ('>=', 70), 'leanbull': ('range', 55, 70), 'neutral': ('range', 45, 55),
                    'leanbear': ('range', 30, 45), 'bearish': ('<', 30), 'pos': ('>=', 55), 'neg': ('<', 45)}
            if f_sentiment in opts:
                v = opts[f_sentiment]
                if v[0] == 'range': df = df[(ss >= v[1]) & (ss < v[2])]
                elif v[0] == '<':   df = df[ss < v[1]]
                else:               df = df[ss >= v[1]]

        if f_insttrans and 'Inst Trans' in df.columns:
            it = pd.to_numeric(df['Inst Trans'].astype(str).str.replace('%', '', regex=False), errors='coerce')
            if f_insttrans == 'pos':   df = df[it > 0]
            elif f_insttrans == 'neg': df = df[it < 0]
            elif f_insttrans == 'vpos': df = df[it > 5]
            elif f_insttrans == 'vneg': df = df[it < -5]

    ascending = (sort_order == 'asc')
    if sort_by and sort_by in df.columns:
        df = df.sort_values(by=sort_by, ascending=ascending)

    # Format volume for display before sending to table
    df_out = df.copy()
    for vcol in ['Volume', 'Avg Volume']:
        if vcol in df_out.columns:
            df_out[vcol] = df_out[vcol].apply(lambda x: fmt_volume(x) if pd.notna(x) else '')

    count_msg = f"Showing {len(df)} of {total_before} stocks" if len(df) != total_before else ""
    records = df_out.to_dict('records')
    return records, (refresh_value * 1000 if refresh_value > 0 else 0), "", records, count_msg, range_status


@app.callback(
    Output('detail-panel', 'children'),
    [Input('main-table', 'active_cell'),
     Input('chart-start-date', 'date'),
     Input('chart-end-date', 'date')],
    [State('main-table', 'derived_virtual_data'),
     State('main-table', 'page_current'),
     State('main-table', 'page_size')]
)
def show_detail(active_cell, start_date, end_date, virtual_data, page_current, page_size):
    if not active_cell or not virtual_data:
        return html.P("Click any ticker row to see chart, metrics, and news.",
                      style={'color': 'gray', 'marginTop': '20px'})
    row = active_cell['row'] + ((page_current or 0) * (page_size or 15))
    if row >= len(virtual_data):
        return html.P("Row out of range.", style={'color': 'gray'})
    ticker = virtual_data[row].get('Ticker', '')
    if not ticker:
        return html.P("No ticker selected.", style={'color': 'gray'})
    return build_detail_panel(ticker, start_date=start_date, end_date=end_date)


@app.callback(
    [Output('filter-pe', 'value'), Output('filter-mktcap', 'value'),
     Output('filter-price', 'value'), Output('filter-avgvol', 'value'),
     Output('filter-relvol', 'value'), Output('filter-curvol', 'value'),
     Output('filter-sector', 'value'), Output('filter-shortfloat', 'value'),
     Output('filter-sharesout', 'value'), Output('filter-instowner', 'value'),
     Output('filter-insiderown', 'value'), Output('filter-change', 'value'),
     Output('filter-signal', 'value'), Output('filter-div-dropdown', 'value'),
     Output('filter-earnings', 'value'), Output('filter-changefromopen', 'value'),
     Output('filter-afterhours', 'value'), Output('filter-optshort', 'value'),
     Output('filter-sentiment', 'value'), Output('filter-insttrans', 'value')],
    Input('clear-filters-button', 'n_clicks'),
    prevent_initial_call=True
)
def clear_filters(_):
    return [None] * 20


@app.callback(
    Output('sentiment-calc-status', 'children'),
    Input('calc-sentiment-button', 'n_clicks'),
    State('date-range-stocks-slider', 'value'),
    prevent_initial_call=True
)
def sentiment_status(n_clicks, n_stocks):
    return f"⏳ Scoring sentiment for up to {n_stocks} stocks in parallel — table updates when done."


# ── App entry point ───────────────────────────────────────────────────────────

app.layout = html.Div([html.Div(id='page-content', children=main_page())])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))

    try:
        app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
        )
    except AttributeError:
        app.run_server(
            host="0.0.0.0",
            port=port,
            debug=False,
        )
