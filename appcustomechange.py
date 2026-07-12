import re
import dash
from dash import dcc, html, Input, Output, dash_table, State
import pandas as pd
import requests
from io import StringIO
from flask_caching import Cache
import plotly.graph_objs as go
import yfinance as yf
import time
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

app = dash.Dash(__name__, suppress_callback_exceptions=True)
cache = Cache(app.server, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 600})

finviz_url = "ENTER YOUR FINVIZ URL HERE" 

custom_change_values = {}
date_range_change_values = {}
sentiment_score_cache = {}   # ticker → {"score": float, "label": str}
monthly_perf_cache = {}      # ticker → float (1-month % change)


def score_ticker_sentiment(ticker):
    """Return a 0-100 sentiment score and label from recent news headlines."""
    try:
        news_items = yf.Ticker(ticker).news[:20]
    except:
        return {"score": 50.0, "label": "Neutral"}
    bull, bear = 0, 0
    for item in news_items:
        content = item.get('content', {})
        title = content.get('title') or item.get('title', '')
        if not title:
            continue
        words = set(re.findall(r"[a-zA-Z]+", title.lower()))
        b = len(words & _BULLISH)
        r = len(words & _BEARISH)
        if b > r: bull += 1
        elif r > b: bear += 1
    total = bull + bear
    if total == 0:
        score = 50.0
    else:
        score = round((bull / total) * 100, 1)
    if score >= 70:   label = "🟢 Bullish"
    elif score >= 55: label = "🟡 Leaning Bull"
    elif score >= 45: label = "⚪ Neutral"
    elif score >= 30: label = "🟠 Leaning Bear"
    else:             label = "🔴 Bearish"
    return {"score": score, "label": label}


def get_monthly_perf(ticker):
    """Return 1-month % price change."""
    try:
        hist = yf.Ticker(ticker).history(period='1mo', interval='1d')
        if hist.empty or len(hist) < 2:
            return 0.0
        return round(((hist['Close'].iloc[-1] - hist['Open'].iloc[0]) / hist['Open'].iloc[0]) * 100, 2)
    except:
        return 0.0


def fmt_volume(val):
    """Format a volume number as 1.2M, 500K etc."""
    try:
        v = float(val)
        if v >= 1e9:  return f"{v/1e9:.2f}B"
        if v >= 1e6:  return f"{v/1e6:.2f}M"
        if v >= 1e3:  return f"{v/1e3:.0f}K"
        return str(int(v))
    except:
        return str(val)

ALL_SECTORS = [
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
]

# ─── Data fetching ────────────────────────────────────────────────────────────

@cache.memoize(timeout=600)
def fetch_finviz_data():
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/91.0.4472.124 Safari/537.36")
    }
    response = requests.get(finviz_url, headers=headers)
    print(f"Fetching Finviz data... Status: {response.status_code}")
    if response.status_code != 200:
        return pd.DataFrame({"Error": [f"HTTP {response.status_code}"]})
    if not response.text.strip().startswith("Ticker") and "<html" in response.text[:200].lower():
        return pd.DataFrame({"Error": ["Got HTML — check Finviz URL/auth"]})
    try:
        df = pd.read_csv(StringIO(response.text))
        df.columns = df.columns.map(str)
        # Drop today's intraday Change — we replace it with date-range change
        if 'Change' in df.columns:
            df = df.drop(columns=['Change'])
        for col in ['Market Cap', 'P/E', 'Forward P/E', 'EPS (ttm)', 'EPS (next Y)',
                    'EPS Growth', 'Revenue', 'Operating Margin', 'ROE', 'Debt/Equity', 'Beta',
                    'Price', 'Volume', 'Avg Volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        # Initialize Change column
        df['Change'] = 0.0
        # Add sentiment and monthly perf columns (pre-populated from cache if available)
        df['Sentiment Score'] = df['Ticker'].map(
            lambda t: sentiment_score_cache.get(t, {}).get('score', None))
        df = df.reset_index(drop=True)
        # Format Volume columns for readability
        for vcol in ['Volume', 'Avg Volume']:
            if vcol in df.columns:
                df[vcol] = df[vcol].apply(lambda x: fmt_volume(x) if pd.notna(x) else '')
        df = df.reset_index(drop=True)
        print(df.head())
        return df
    except Exception as e:
        print(f"Error parsing Finviz data: {e}")
        return pd.DataFrame({"Error": [str(e)]})


def calculate_date_range_change(ticker, start_date, end_date):
    try:
        hist = yf.Ticker(ticker).history(start=start_date, end=end_date, interval='1d')
        if hist.empty or len(hist) < 2:
            return 0.0
        start_price = hist['Open'].iloc[0]
        end_price = hist['Close'].iloc[-1]
        if start_price == 0:
            return 0.0
        return round(((end_price - start_price) / start_price) * 100, 2)
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return 0.0


def calculate_stock_change(ticker, interval='1d', period='1y'):
    try:
        hist = yf.Ticker(ticker).history(period=period, interval=interval)
        if hist.empty or len(hist) < 2:
            return 0.0
        return round(((hist['Close'].iloc[-1] - hist['Open'].iloc[0]) / hist['Open'].iloc[0]) * 100, 2)
    except:
        return 0.0


def update_stocks_with_timeframe_data(df, interval='1d', period='1y', max_stocks=None):
    global custom_change_values
    updated_df = df.copy()
    tickers = updated_df['Ticker'].dropna().head(max_stocks).tolist() if max_stocks else updated_df['Ticker'].dropna().tolist()
    print(f"Calculating custom change for {len(tickers)} tickers...")
    for i, ticker in enumerate(tickers):
        if i % 5 == 0:
            print(f"  {i+1}/{len(tickers)}: {ticker}")
        try:
            pct = calculate_stock_change(ticker, interval, period)
            custom_change_values[ticker] = pct
            idxs = updated_df.index[updated_df['Ticker'] == ticker].tolist()
            if idxs:
                updated_df.at[idxs[0], 'Custom Change'] = pct
        except Exception as e:
            print(f"  Error {ticker}: {e}")
    return updated_df


def update_date_range_changes(df, start_date, end_date, max_stocks=50):
    global date_range_change_values
    updated_df = df.copy()
    tickers = updated_df['Ticker'].dropna().head(max_stocks).tolist()
    print(f"Calculating date range change for {len(tickers)} tickers in parallel ({start_date} → {end_date})")

    def _calc(ticker):
        pct = calculate_date_range_change(ticker, start_date, end_date)
        date_range_change_values[ticker] = pct
        return ticker, pct

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_calc, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                ticker, pct = future.result()
                idxs = updated_df.index[updated_df['Ticker'] == ticker].tolist()
                if idxs:
                    updated_df.at[idxs[0], 'Change'] = pct
            except Exception as e:
                print(f"  Error: {e}")
    return updated_df


# ─── Sentiment ────────────────────────────────────────────────────────────────

_BULLISH = {"beat","beats","surpasses","record","upgrade","upgraded","raises","raised",
            "growth","strong","profit","profits","gains","bullish","buy","positive",
            "outperform","rally","surge","soars","soar","accelerating","acquisition",
            "partnership","innovation","breakthrough","dividend","upside","expands",
            "expansion","approval","approved","wins","win"}

_BEARISH = {"miss","misses","missed","disappoints","disappointing","downgrade","downgraded",
            "cut","cuts","loss","losses","bearish","sell","negative","underperform",
            "decline","declines","drops","falls","fell","slump","warning","warns",
            "risk","risks","lawsuit","fraud","investigation","layoffs","recall",
            "debt","default","bankruptcy","concern","concerns","worry","worries",
            "weak","weakness","plunge","plunges"}


def analyze_news_sentiment(news_items):
    if not news_items:
        return {"verdict":"No Data","color":"#888","bg":"#f0f0f0",
                "summary":"No recent news.","bull_headlines":[],"bear_headlines":[]}
    bull, bear, bull_h, bear_h = 0, 0, [], []
    for item in news_items:
        content = item.get('content', {})
        title = content.get('title') or item.get('title', '')
        if not title:
            continue
        words = set(re.findall(r"[a-zA-Z]+", title.lower()))
        b, r = len(words & _BULLISH), len(words & _BEARISH)
        if b > r:
            bull += 1; bull_h.append(title)
        elif r > b:
            bear += 1; bear_h.append(title)
    net = bull - bear
    total = len(news_items)
    if total == 0:
        verdict, color, bg = "No Data", "#888", "#f0f0f0"
    elif net >= 3:
        verdict, color, bg = "Bullish — Consider Buying", "#166534", "#dcfce7"
    elif net >= 1:
        verdict, color, bg = "Leaning Bullish", "#15803d", "#bbf7d0"
    elif net == 0:
        verdict, color, bg = "Neutral / Mixed Signals", "#92400e", "#fef3c7"
    elif net >= -2:
        verdict, color, bg = "Leaning Bearish", "#b45309", "#fed7aa"
    else:
        verdict, color, bg = "Bearish — Use Caution", "#991b1b", "#fee2e2"
    return {"verdict":verdict,"color":color,"bg":bg,
            "summary":f"Of {total} headlines: {bull} positive, {bear} negative.",
            "bull_headlines":bull_h[:3],"bear_headlines":bear_h[:3]}


def build_sentiment_summary(news_items):
    r = analyze_news_sentiment(news_items)
    lines = [html.P(r["summary"], style={"marginTop":"8px","color":"#444","fontSize":"13px"})]
    if r["bull_headlines"]:
        lines.append(html.Div([
            html.Span("Positive: ", style={"fontWeight":"600","fontSize":"12px","color":"#166534"}),
            html.Span(" · ".join(r["bull_headlines"]), style={"fontSize":"12px","color":"#444"}),
        ], style={"marginTop":"4px"}))
    if r["bear_headlines"]:
        lines.append(html.Div([
            html.Span("Negative: ", style={"fontWeight":"600","fontSize":"12px","color":"#991b1b"}),
            html.Span(" · ".join(r["bear_headlines"]), style={"fontSize":"12px","color":"#444"}),
        ], style={"marginTop":"4px"}))
    return html.Div([
        html.H4("News-Based Outlook", style={"color":"#333","marginBottom":"8px","marginTop":"20px"}),
        html.Span(r["verdict"], style={"backgroundColor":r["bg"],"color":r["color"],
                                        "padding":"4px 12px","borderRadius":"6px",
                                        "fontWeight":"700","fontSize":"14px"}),
        html.Div(lines),
        html.P("Keyword-based only — not financial advice.",
               style={"fontSize":"11px","color":"#999","marginTop":"8px","fontStyle":"italic"}),
    ], style={"backgroundColor":"#fafafa","border":"1px solid #e5e7eb",
               "borderRadius":"8px","padding":"16px","marginTop":"16px"})


# ─── Formatting ───────────────────────────────────────────────────────────────

def fmt(val):
    if val is None: return 'N/A'
    try:
        f = float(val)
        if abs(f) >= 1e9: return f"{f/1e9:.2f}B"
        if abs(f) >= 1e6: return f"{f/1e6:.2f}M"
        return f"{f:,.2f}"
    except:
        return str(val)


# ─── Detail panel ─────────────────────────────────────────────────────────────

def build_detail_panel(ticker, start_date=None, end_date=None):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
    except Exception:
        info = {}
        stock = None

    # Chart
    try:
        if start_date and end_date:
            hist = stock.history(start=start_date, end=end_date, interval='1d')
        else:
            hist = stock.history(period='1y', interval='1d')
        hist.reset_index(inplace=True)
        if 'Date' not in hist.columns and 'Datetime' in hist.columns:
            hist.rename(columns={'Datetime': 'Date'}, inplace=True)

        hover_text = []
        for _, row in hist.iterrows():
            hover_text.append(
                f"<b>{str(row['Date'])[:10]}</b><br>"
                f"Open:  <b>${row['Open']:.2f}</b><br>"
                f"High:  <b>${row['High']:.2f}</b><br>"
                f"Low:   <b>${row['Low']:.2f}</b><br>"
                f"Close: <b>${row['Close']:.2f}</b><br>"
                f"Volume: {int(row['Volume']):,}"
            )

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist['Date'], open=hist['Open'], high=hist['High'],
            low=hist['Low'], close=hist['Close'], name='Price',
            text=hover_text, hoverinfo='text',
            increasing_line_color='#22c55e',
            decreasing_line_color='#ef4444',
        ))
        if len(hist) >= 50:
            fig.add_trace(go.Scatter(
                x=hist['Date'], y=hist['Close'].rolling(50).mean(),
                mode='lines', name='SMA 50',
                line=dict(color='orange', width=1.5),
                hovertemplate='SMA 50: $%{y:.2f}<extra></extra>'
            ))
        if len(hist) >= 200:
            fig.add_trace(go.Scatter(
                x=hist['Date'], y=hist['Close'].rolling(200).mean(),
                mode='lines', name='SMA 200',
                line=dict(color='purple', width=1.5),
                hovertemplate='SMA 200: $%{y:.2f}<extra></extra>'
            ))
        if len(hist) >= 2:
            sp = hist['Open'].iloc[0]
            ep = hist['Close'].iloc[-1]
            pct = ((ep - sp) / sp) * 100 if sp != 0 else 0
            pct_color = '#22c55e' if pct >= 0 else '#ef4444'
            fig.add_annotation(
                x=hist['Date'].iloc[-1], y=hist['High'].max(),
                text=f"<b>{'+' if pct >= 0 else ''}{pct:.2f}% over period</b>",
                showarrow=False, font=dict(size=13, color=pct_color),
                bgcolor='white', bordercolor=pct_color, borderwidth=1, borderpad=4,
            )

        title_range = f"{start_date} → {end_date}" if start_date and end_date else "1 Year Daily"
        fig.update_layout(
            title=f"<b>{ticker}</b> — {title_range}",
            xaxis_rangeslider_visible=False,
            height=450, margin=dict(l=20, r=20, t=50, b=20),
            paper_bgcolor='white', plot_bgcolor='#fafafa',
            hovermode='x unified',
            hoverlabel=dict(bgcolor='white', font_size=13, font_family='monospace'),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            xaxis=dict(showgrid=True, gridcolor='#e5e7eb'),
            yaxis=dict(showgrid=True, gridcolor='#e5e7eb', tickprefix='$', tickformat=',.2f'),
        )
        chart = dcc.Graph(figure=fig, config={'displayModeBar': True, 'scrollZoom': True})

        # 30-day price change stat
        try:
            hist_30 = stock.history(period='1mo', interval='1d')
            if not hist_30.empty and len(hist_30) >= 2:
                p_start = hist_30['Open'].iloc[0]
                p_end   = hist_30['Close'].iloc[-1]
                chg_30  = ((p_end - p_start) / p_start) * 100 if p_start != 0 else 0
                chg_color = '#22c55e' if chg_30 >= 0 else '#ef4444'
                thirty_day_stat = html.Div([
                    html.Span("Price change over last 30 days:  ",
                              style={'fontSize':'14px','color':'#555'}),
                    html.Span(f"{'+' if chg_30 >= 0 else ''}{chg_30:.2f}%",
                              style={'fontSize':'16px','fontWeight':'700','color':chg_color}),
                    html.Span(f"  (${p_start:.2f} → ${p_end:.2f})",
                              style={'fontSize':'13px','color':'#888','marginLeft':'6px'}),
                ], style={'padding':'10px 14px','backgroundColor':'#f8fafc',
                           'border':'1px solid #e2e8f0','borderRadius':'6px','marginTop':'8px'})
            else:
                thirty_day_stat = html.Div()
        except:
            thirty_day_stat = html.Div()
    except Exception as e:
        chart = html.P(f"Chart unavailable: {e}", style={'color': 'red'})
        thirty_day_stat = html.Div()

    def g(key, pct=False):
        val = info.get(key)
        if val is None: return 'N/A'
        try:
            return f"{float(val)*100:.2f}%" if pct else fmt(val)
        except:
            return str(val)

    rows_left = [
        ("Market Cap",   fmt(info.get('marketCap'))),
        ("P/E",          g('trailingPE')),
        ("Forward P/E",  g('forwardPE')),
        ("PEG",          g('trailingPegRatio')),
        ("P/S",          g('priceToSalesTrailing12Months')),
        ("P/B",          g('priceToBook')),
        ("Dividend",     f"{info.get('dividendYield')*100:.2f}%" if info.get('dividendYield') else 'N/A'),
        ("Insider Own",  g('heldPercentInsiders', pct=True)),
        ("Short Float",  g('shortPercentOfFloat', pct=True)),
        ("Analyst Rec",  g('recommendationMean')),
        ("Avg Volume",   fmt(info.get('averageVolume'))),
        ("Beta",         g('beta')),
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
                html.Span(lbl, style={'color':'#666','fontSize':'12px','width':'110px','display':'inline-block'}),
                html.Span(val, style={'fontWeight':'600','fontSize':'13px'}),
            ], style={'padding':'5px 8px','borderBottom':'1px solid #f0f0f0'})
            for lbl, val in rows
        ], style={'flex':'1'})

    metrics = html.Div([metric_col(rows_left), metric_col(rows_right)],
                       style={'display':'flex','gap':'20px','marginTop':'16px'})

    header = html.Div([
        html.Div([
            html.Span(ticker, style={'fontSize':'28px','fontWeight':'800','marginRight':'12px'}),
            html.Span(f"[{info.get('exchange','')}]", style={'color':'#555','fontSize':'14px'}),
        ]),
        html.Div([html.Span("Company  ", style={'color':'#888','fontSize':'13px'}),
                  html.Span(info.get('shortName', ticker), style={'fontWeight':'600','color':'#0066cc','fontSize':'13px'})]),
        html.Div([html.Span("Country  ", style={'color':'#888','fontSize':'13px'}),
                  html.Span(info.get('country','N/A'), style={'fontWeight':'600','color':'#0066cc','fontSize':'13px'})]),
        html.Div([html.Span("Industry  ", style={'color':'#888','fontSize':'13px'}),
                  html.Span(info.get('industry','N/A'), style={'fontWeight':'600','color':'#0066cc','fontSize':'13px'})]),
    ], style={'marginBottom':'12px'})

    try:
        news_items = stock.news[:15] if stock else []
    except:
        news_items = []

    news_rows = []
    for item in news_items:
        content = item.get('content', {})
        title  = content.get('title') or item.get('title', 'No title')
        url    = content.get('canonicalUrl', {}).get('url') or item.get('link', '#')
        source = content.get('provider', {}).get('displayName') or item.get('publisher', '')
        pub_date = content.get('pubDate', '')
        if pub_date:
            try:
                dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                pub_date = dt.strftime('%b %d %I:%M%p')
            except:
                pass
        news_rows.append(html.Div([
            html.Span(pub_date, style={'color':'#888','fontSize':'12px','minWidth':'140px','display':'inline-block'}),
            html.A(title, href=url, target='_blank',
                   style={'color':'#0066cc','fontSize':'13px','textDecoration':'none','marginRight':'8px'}),
            html.Span(f"({source})", style={'color':'#888','fontSize':'12px'}),
        ], style={'padding':'6px 0','borderBottom':'1px solid #f0f0f0'}))

    news_section = html.Div([
        html.H4("Latest News", style={'color':'#007BFF','marginBottom':'10px','marginTop':'20px'}),
        html.Div(news_rows) if news_rows else html.P("No news available.", style={'color':'gray'})
    ])

    return html.Div([
        header, chart, thirty_day_stat,
        html.H4("Key Metrics", style={'color':'#333','marginTop':'20px','marginBottom':'4px'}),
        metrics,
        build_sentiment_summary(news_items),
        news_section,
    ], style={'backgroundColor':'white','border':'1px solid #e0e0e0','borderRadius':'8px',
               'padding':'20px','marginTop':'20px','boxShadow':'0 2px 6px rgba(0,0,0,0.06)'})


# ─── Main page ────────────────────────────────────────────────────────────────

def main_page():
    df = fetch_finviz_data()
    if df.empty or "Error" in df.columns:
        err = df["Error"].iloc[0] if "Error" in df.columns else "No data"
        return html.Div(f"⚠ {err}", style={'textAlign':'center','color':'red','marginTop':'40px'})

    numeric_columns = ['Market Cap','P/E','Forward P/E','EPS (ttm)','EPS (next Y)',
                       'EPS Growth','Revenue','Operating Margin','ROE','Debt/Equity',
                       'Beta','Change','Price','Volume','Avg Volume']
    display_columns = list(df.columns)

    data_sectors = sorted(df['Sector'].dropna().unique().tolist()) if 'Sector' in df.columns else []
    sector_options = [{'label':s,'value':s} for s in sorted(set(ALL_SECTORS + data_sectors))]
    industry_options = ([{'label':s,'value':s} for s in sorted(df['Industry'].dropna().unique())]
                        if 'Industry' in df.columns else [])

    return html.Div([
        html.H1("Stock Screener", style={'textAlign':'center','color':'#007BFF'}),

        # ── Top controls ──────────────────────────────────────────────────────
        html.Div([
            html.Button("Refresh Data", id="refresh-button", n_clicks=0,
                        style={'backgroundColor':'#007BFF','color':'white',
                               'padding':'10px 20px','borderRadius':'5px','marginRight':'20px'}),
            dcc.RadioItems(id='refresh-interval-radio',
                           options=[{'label':'10s','value':10},{'label':'1min','value':60},{'label':'off','value':0}],
                           value=0, labelStyle={'marginRight':'20px','display':'inline-block'}),
        ], style={'marginBottom':'20px'}),
        dcc.Interval(id='refresh-interval', interval=0, n_intervals=0),

        # ── Date Range + Change column ────────────────────────────────────────
        html.Div([
            html.H3("Date Range % Change", style={'marginBottom':'10px'}),
            html.P("Pick a start and end date, then click Calculate to populate the Change column with long-term % return for each stock, sorted highest to lowest.",
                   style={'fontSize':'13px','color':'#555','marginBottom':'12px'}),
            html.Div([
                html.Div([
                    html.Label("Start Date:"),
                    dcc.DatePickerSingle(id='chart-start-date', placeholder='Start',
                                        display_format='YYYY-MM-DD',
                                        date=str(date(date.today().year - 1, date.today().month, date.today().day))),
                ], style={'display':'inline-block','marginRight':'24px'}),
                html.Div([
                    html.Label("End Date:"),
                    dcc.DatePickerSingle(id='chart-end-date', placeholder='End',
                                        display_format='YYYY-MM-DD',
                                        date=str(date.today())),
                ], style={'display':'inline-block','marginRight':'24px'}),
                html.Div([
                    html.Label("# Stocks:"),
                    dcc.Slider(id='date-range-stocks-slider', min=5, max=200, step=5, value=50,
                               marks={5:'5',50:'50',100:'100',200:'All'}),
                ], style={'display':'inline-block','width':'220px','verticalAlign':'bottom','marginRight':'24px'}),
                html.Div([
                    html.Br(),
                    html.Button("Calculate Change", id="calc-range-change-button", n_clicks=0,
                                style={'backgroundColor':'#7c3aed','color':'white',
                                       'padding':'10px 18px','borderRadius':'5px','fontWeight':'700'}),
                ], style={'display':'inline-block','verticalAlign':'bottom'}),
            ]),
            html.Div(id='range-change-status',
                     style={'color':'#7c3aed','fontWeight':'500','fontSize':'13px','marginTop':'8px'}),
        ], style={'marginBottom':'20px','backgroundColor':'#f8f9fa','padding':'15px','borderRadius':'5px'}),

        # ── Filters ───────────────────────────────────────────────────────────
        html.Div([
            html.H3("Filters", style={'marginBottom':'14px'}),

            # Row 1
            html.Div([
                html.Div([html.Label("P/E"),
                          dcc.Dropdown(id='filter-pe', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Profitable (>0)','value':'pos'},
                                                {'label':'Low (<15)','value':'low'},
                                                {'label':'Under 20','value':'u20'},
                                                {'label':'Under 30','value':'u30'},
                                                {'label':'Under 40','value':'u40'},
                                                {'label':'Under 50','value':'u50'},
                                                {'label':'High (>50)','value':'high'},
                                                {'label':'Over 100','value':'o100'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Market Cap"),
                          dcc.Dropdown(id='filter-mktcap', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Mega (>200B)','value':'mega'},
                                                {'label':'Large (10B-200B)','value':'large'},
                                                {'label':'Mid (2B-10B)','value':'mid'},
                                                {'label':'Small (300M-2B)','value':'small'},
                                                {'label':'Micro (50M-300M)','value':'micro'},
                                                {'label':'Nano (<50M)','value':'nano'},
                                                {'label':'+Large (>10B)','value':'largeover'},
                                                {'label':'+Mid (>2B)','value':'midover'},
                                                {'label':'+Small (>300M)','value':'smallover'}])],
                         style={'width':'14%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Price ($)"),
                          dcc.Dropdown(id='filter-price', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Under $1','value':'u1'},
                                                {'label':'Under $5','value':'u5'},
                                                {'label':'Under $10','value':'u10'},
                                                {'label':'Under $20','value':'u20'},
                                                {'label':'Under $50','value':'u50'},
                                                {'label':'Under $100','value':'u100'},
                                                {'label':'Over $5','value':'o5'},
                                                {'label':'Over $10','value':'o10'},
                                                {'label':'Over $20','value':'o20'},
                                                {'label':'Over $50','value':'o50'},
                                                {'label':'Over $100','value':'o100'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Avg Volume"),
                          dcc.Dropdown(id='filter-avgvol', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Under 50K','value':'u50k'},
                                                {'label':'Under 100K','value':'u100k'},
                                                {'label':'Under 500K','value':'u500k'},
                                                {'label':'Over 50K','value':'o50k'},
                                                {'label':'Over 100K','value':'o100k'},
                                                {'label':'Over 500K','value':'o500k'},
                                                {'label':'Over 1M','value':'o1m'},
                                                {'label':'Over 2M','value':'o2m'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Relative Volume"),
                          dcc.Dropdown(id='filter-relvol', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Over 10x','value':'o10'},
                                                {'label':'Over 5x','value':'o5'},
                                                {'label':'Over 3x','value':'o3'},
                                                {'label':'Over 2x','value':'o2'},
                                                {'label':'Over 1.5x','value':'o1.5'},
                                                {'label':'Over 1x','value':'o1'},
                                                {'label':'Under 1x','value':'u1'},
                                                {'label':'Under 0.5x','value':'u0.5'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Current Volume"),
                          dcc.Dropdown(id='filter-curvol', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Under 50K','value':'u50k'},
                                                {'label':'Under 100K','value':'u100k'},
                                                {'label':'Over 50K','value':'o50k'},
                                                {'label':'Over 100K','value':'o100k'},
                                                {'label':'Over 500K','value':'o500k'},
                                                {'label':'Over 1M','value':'o1m'},
                                                {'label':'Over 5M','value':'o5m'},
                                                {'label':'Over 10M','value':'o10m'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Sector"),
                          dcc.Dropdown(id='filter-sector', placeholder='Any', style={'width':'100%'},
                                       options=sector_options)],
                         style={'width':'22%','display':'inline-block'}),
            ], style={'marginBottom':'10px'}),

            # Row 2
            html.Div([
                html.Div([html.Label("Short Float"),
                          dcc.Dropdown(id='filter-shortfloat', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Low (<5%)','value':'low'},
                                                {'label':'High (>20%)','value':'high'},
                                                {'label':'Under 5%','value':'u5'},
                                                {'label':'Under 10%','value':'u10'},
                                                {'label':'Under 15%','value':'u15'},
                                                {'label':'Over 10%','value':'o10'},
                                                {'label':'Over 20%','value':'o20'},
                                                {'label':'Over 30%','value':'o30'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Shares Outstanding"),
                          dcc.Dropdown(id='filter-sharesout', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Under 1M','value':'u1m'},
                                                {'label':'Under 5M','value':'u5m'},
                                                {'label':'Under 10M','value':'u10m'},
                                                {'label':'Under 50M','value':'u50m'},
                                                {'label':'Under 100M','value':'u100m'},
                                                {'label':'Over 1M','value':'o1m'},
                                                {'label':'Over 10M','value':'o10m'},
                                                {'label':'Over 50M','value':'o50m'},
                                                {'label':'Over 100M','value':'o100m'},
                                                {'label':'Over 500M','value':'o500m'},
                                                {'label':'Over 1B','value':'o1b'}])],
                         style={'width':'14%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Inst Ownership"),
                          dcc.Dropdown(id='filter-instowner', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Low (<5%)','value':'low'},
                                                {'label':'High (>90%)','value':'high'},
                                                {'label':'Under 20%','value':'u20'},
                                                {'label':'Under 50%','value':'u50'},
                                                {'label':'Over 50%','value':'o50'},
                                                {'label':'Over 70%','value':'o70'},
                                                {'label':'Over 90%','value':'o90'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Insider Ownership"),
                          dcc.Dropdown(id='filter-insiderown', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Low (<5%)','value':'low'},
                                                {'label':'High (>30%)','value':'high'},
                                                {'label':'Under 5%','value':'u5'},
                                                {'label':'Under 10%','value':'u10'},
                                                {'label':'Over 5%','value':'o5'},
                                                {'label':'Over 10%','value':'o10'},
                                                {'label':'Over 20%','value':'o20'},
                                                {'label':'Over 30%','value':'o30'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Change (%)"),
                          dcc.Dropdown(id='filter-change', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Up >1%','value':'u1'},
                                                {'label':'Up >2%','value':'u2'},
                                                {'label':'Up >3%','value':'u3'},
                                                {'label':'Up >5%','value':'u5'},
                                                {'label':'Up >10%','value':'u10'},
                                                {'label':'Down >1%','value':'d1'},
                                                {'label':'Down >2%','value':'d2'},
                                                {'label':'Down >5%','value':'d5'},
                                                {'label':'Down >10%','value':'d10'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Signal"),
                          dcc.Dropdown(id='filter-signal', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Top Gainers','value':'topgainers'},
                                                {'label':'Top Losers','value':'toplosers'},
                                                {'label':'New High','value':'newhigh'},
                                                {'label':'New Low','value':'newlow'},
                                                {'label':'Most Volatile','value':'mostvolatile'},
                                                {'label':'Most Active','value':'mostactive'},
                                                {'label':'Overbought','value':'overbought'},
                                                {'label':'Oversold','value':'oversold'},
                                                {'label':'Unusual Volume','value':'unusualvol'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Dividend"),
                          dcc.Dropdown(id='filter-div-dropdown', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'None (0%)','value':'none'},
                                                {'label':'Positive (>0%)','value':'pos'},
                                                {'label':'High (>5%)','value':'high'},
                                                {'label':'Over 1%','value':'o1'},
                                                {'label':'Over 2%','value':'o2'},
                                                {'label':'Over 3%','value':'o3'},
                                                {'label':'Over 5%','value':'o5'}])],
                         style={'width':'12%','display':'inline-block'}),
            ], style={'marginBottom':'12px'}),

            # Row 3 — remaining filters
            html.Div([
                html.Div([html.Label("Earnings Date"),
                          dcc.Dropdown(id='filter-earnings', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Today','value':'today'},
                                                {'label':'Tomorrow','value':'tomorrow'},
                                                {'label':'This Week','value':'thisweek'},
                                                {'label':'Next Week','value':'nextweek'},
                                                {'label':'This Month','value':'thismonth'},
                                                {'label':'Next Month','value':'nextmonth'},
                                                {'label':'Last Week','value':'lastweek'}])],
                         style={'width':'13%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Change from Open"),
                          dcc.Dropdown(id='filter-changefromopen', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Up >1%','value':'u1'},
                                                {'label':'Up >2%','value':'u2'},
                                                {'label':'Up >3%','value':'u3'},
                                                {'label':'Up >5%','value':'u5'},
                                                {'label':'Down >1%','value':'d1'},
                                                {'label':'Down >2%','value':'d2'},
                                                {'label':'Down >3%','value':'d3'},
                                                {'label':'Down >5%','value':'d5'}])],
                         style={'width':'13%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("After Hours"),
                          dcc.Dropdown(id='filter-afterhours', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Up (AH)','value':'up'},
                                                {'label':'Down (AH)','value':'down'},
                                                {'label':'Up >1%','value':'u1'},
                                                {'label':'Up >2%','value':'u2'},
                                                {'label':'Up >5%','value':'u5'},
                                                {'label':'Down >1%','value':'d1'},
                                                {'label':'Down >2%','value':'d2'},
                                                {'label':'Down >5%','value':'d5'}])],
                         style={'width':'12%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Option/Short Float"),
                          dcc.Dropdown(id='filter-optshort', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Optionable','value':'optionable'},
                                                {'label':'Shortable','value':'shortable'},
                                                {'label':'Both','value':'both'},
                                                {'label':'Short >10%','value':'s10'},
                                                {'label':'Short >20%','value':'s20'},
                                                {'label':'Short >30%','value':'s30'},
                                                {'label':'Short >40%','value':'s40'}])],
                         style={'width':'13%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("News Sentiment"),
                          dcc.Dropdown(id='filter-sentiment', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'🟢 Bullish (≥70)','value':'bullish'},
                                                {'label':'🟡 Leaning Bull (55-70)','value':'leanbull'},
                                                {'label':'⚪ Neutral (45-55)','value':'neutral'},
                                                {'label':'🟠 Leaning Bear (30-45)','value':'leanbear'},
                                                {'label':'🔴 Bearish (<30)','value':'bearish'},
                                                {'label':'Positive (≥55)','value':'pos'},
                                                {'label':'Negative (<45)','value':'neg'}])],
                         style={'width':'14%','display':'inline-block','paddingRight':'8px'}),

                html.Div([html.Label("Inst Transactions"),
                          dcc.Dropdown(id='filter-insttrans', placeholder='Any', style={'width':'100%'},
                                       options=[{'label':'Positive (buying)','value':'pos'},
                                                {'label':'Negative (selling)','value':'neg'},
                                                {'label':'Very Positive (>5%)','value':'vpos'},
                                                {'label':'Very Negative (<-5%)','value':'vneg'}])],
                         style={'width':'13%','display':'inline-block'}),
            ], style={'marginBottom':'12px'}),

            # Row 4 — Calculate Sentiment/Monthly button
            html.Div([
                html.Button("Calculate Sentiment & 1M Perf", id="calc-sentiment-button", n_clicks=0,
                            style={'backgroundColor':'#7c3aed','color':'white',
                                   'padding':'8px 16px','borderRadius':'5px','fontWeight':'600',
                                   'marginRight':'10px'}),
                html.Span("(Fetches news + 1-month price data for each ticker — takes ~30s per 20 stocks)",
                          style={'fontSize':'12px','color':'#666'}),
            ], style={'marginBottom':'12px'}),
            html.Div(id='sentiment-calc-status',
                     style={'color':'#7c3aed','fontSize':'13px','fontWeight':'500','marginBottom':'12px'}),

            html.Div([
                html.Button("Apply Filters", id="apply-filters-button", n_clicks=0,
                            style={'backgroundColor':'#0ea5e9','color':'white',
                                   'padding':'8px 18px','borderRadius':'5px','fontWeight':'600','marginRight':'10px'}),
                html.Button("Clear Filters", id="clear-filters-button", n_clicks=0,
                            style={'backgroundColor':'#94a3b8','color':'white',
                                   'padding':'8px 18px','borderRadius':'5px','fontWeight':'600'}),
                html.Span(id='filter-results-count',
                          style={'marginLeft':'14px','color':'#475569','fontSize':'13px'}),
            ]),
        ], style={'marginBottom':'20px','backgroundColor':'#f8f9fa','padding':'15px','borderRadius':'5px'}),

        # ── Sort ──────────────────────────────────────────────────────────────
        html.Div([
            html.Label("Sort By:"),
            dcc.Dropdown(id='sort-by-dropdown',
                         options=[{'label':col,'value':col} for col in display_columns],
                         value='Change',
                         style={'width':'45%','display':'inline-block','marginRight':'10px'}),
            dcc.RadioItems(id='sort-order',
                           options=[{'label':'Ascending','value':'asc'},{'label':'Descending','value':'desc'}],
                           value='desc', style={'display':'inline-block'}),
        ], style={'marginBottom':'20px'}),

        html.Div(id='processing-status',
                 style={'marginBottom':'10px','color':'#007BFF','fontWeight':'bold'}),

        dash_table.DataTable(
            id='main-table',
            columns=[{"name":col,"id":col,
                      "type":"numeric" if col in numeric_columns else "text",
                      "format":{"specifier":".2f"} if col in ['Change'] else None}
                     for col in display_columns],
            data=df.to_dict('records'),
            style_table={'overflowX':'auto'},
            style_cell={'textAlign':'left','padding':'5px'},
            style_header={'backgroundColor':'#f4f4f4','fontWeight':'bold'},
            style_data_conditional=[
                {'if':{'column_id':'Ticker'},'cursor':'pointer','color':'blue','textDecoration':'underline'},
                {'if':{'filter_query':'{Change} > 0','column_id':'Change'},'backgroundColor':'#E8F5E9','color':'green'},
                {'if':{'filter_query':'{Change} < 0','column_id':'Change'},'backgroundColor':'#FFEBEE','color':'red'},
                {'if':{'filter_query':'{Change} > 50','column_id':'Change'},'backgroundColor':'#15803d','color':'white'},
                {'if':{'filter_query':'{Change} < -20','column_id':'Change'},'backgroundColor':'#991b1b','color':'white'},
            ],
            page_size=15,
            page_action='native',
            sort_action='native',
            filter_action='native',
        ),

        html.Div(id='detail-panel', style={'marginTop':'10px'}),
        dcc.Store(id='full-data-store', data=df.to_dict('records')),
    ])


# ─── Callbacks ────────────────────────────────────────────────────────────────

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
    [State('filter-pe', 'value'),
     State('filter-mktcap', 'value'),
     State('filter-price', 'value'),
     State('filter-avgvol', 'value'),
     State('filter-relvol', 'value'),
     State('filter-curvol', 'value'),
     State('filter-sector', 'value'),
     State('filter-shortfloat', 'value'),
     State('filter-sharesout', 'value'),
     State('filter-instowner', 'value'),
     State('filter-insiderown', 'value'),
     State('filter-change', 'value'),
     State('filter-signal', 'value'),
     State('filter-div-dropdown', 'value'),
     State('filter-earnings', 'value'),
     State('filter-changefromopen', 'value'),
     State('filter-afterhours', 'value'),
     State('filter-optshort', 'value'),
     State('filter-sentiment', 'value'),
     State('filter-insttrans', 'value'),
     State('chart-start-date', 'date'),
     State('chart-end-date', 'date'),
     State('date-range-stocks-slider', 'value')]
)
def update_main_table(n_clicks, refresh_value, sort_by, sort_order,
                      n_intervals, apply_filters_clicks,
                      clear_filters_clicks, calc_range_clicks, calc_sentiment_clicks,
                      f_pe, f_mktcap, f_price, f_avgvol, f_relvol, f_curvol,
                      f_sector, f_shortfloat, f_sharesout, f_instowner,
                      f_insiderown, f_change, f_signal, f_div,
                      f_earnings, f_changefromopen, f_afterhours,
                      f_optshort, f_sentiment, f_insttrans,
                      start_date, end_date, date_range_stocks):

    df = fetch_finviz_data()
    if df.empty or "Error" in df.columns:
        return [], (refresh_value*1000 if refresh_value > 0 else 0), "No data", [], "", ""

    df['Change'] = df['Ticker'].map(date_range_change_values).fillna(0.0)

    ctx = dash.callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else ''

    range_status = ""

    if trigger_id == 'calc-range-change-button' and start_date and end_date:
        df = update_date_range_changes(df, start_date, end_date, date_range_stocks)
        df['Change'] = df['Ticker'].map(date_range_change_values).fillna(0.0)
        range_status = (f"✅ Change updated for {date_range_stocks} stocks: "
                        f"{start_date} → {end_date}, sorted highest to lowest.")
        sort_by = 'Change'
        sort_order = 'desc'

    # Calculate news sentiment in parallel
    if trigger_id == 'calc-sentiment-button':
        tickers = df['Ticker'].dropna().head(date_range_stocks).tolist()
        print(f"Scoring sentiment for {len(tickers)} tickers in parallel...")

        def _score(ticker):
            sentiment_score_cache[ticker] = score_ticker_sentiment(ticker)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_score, t): t for t in tickers}
            for future in as_completed(futures):
                pass  # results go straight into cache

        df['Sentiment Score'] = df['Ticker'].map(
            lambda t: sentiment_score_cache.get(t, {}).get('score', None))

    total_before = len(df)

    if trigger_id != 'clear-filters-button':
        def num_col(col):
            return pd.to_numeric(df[col], errors='coerce') if col in df.columns else None

        # P/E filter
        if f_pe and 'P/E' in df.columns:
            pe = num_col('P/E')
            if f_pe == 'pos':   df = df[pe > 0]
            elif f_pe == 'low': df = df[(pe > 0) & (pe < 15)]
            elif f_pe == 'u20': df = df[(pe > 0) & (pe < 20)]
            elif f_pe == 'u30': df = df[(pe > 0) & (pe < 30)]
            elif f_pe == 'u40': df = df[(pe > 0) & (pe < 40)]
            elif f_pe == 'u50': df = df[(pe > 0) & (pe < 50)]
            elif f_pe == 'high': df = df[pe > 50]
            elif f_pe == 'o100': df = df[pe > 100]

        # Market Cap filter (values in raw numbers from Finviz)
        if f_mktcap and 'Market Cap' in df.columns:
            mc = num_col('Market Cap')
            if f_mktcap == 'mega':      df = df[mc >= 200e9]
            elif f_mktcap == 'large':   df = df[(mc >= 10e9) & (mc < 200e9)]
            elif f_mktcap == 'mid':     df = df[(mc >= 2e9) & (mc < 10e9)]
            elif f_mktcap == 'small':   df = df[(mc >= 300e6) & (mc < 2e9)]
            elif f_mktcap == 'micro':   df = df[(mc >= 50e6) & (mc < 300e6)]
            elif f_mktcap == 'nano':    df = df[mc < 50e6]
            elif f_mktcap == 'largeover': df = df[mc >= 10e9]
            elif f_mktcap == 'midover':   df = df[mc >= 2e9]
            elif f_mktcap == 'smallover': df = df[mc >= 300e6]

        # Price filter
        if f_price and 'Price' in df.columns:
            pr = num_col('Price')
            if f_price == 'u1':   df = df[pr < 1]
            elif f_price == 'u5':  df = df[pr < 5]
            elif f_price == 'u10': df = df[pr < 10]
            elif f_price == 'u20': df = df[pr < 20]
            elif f_price == 'u50': df = df[pr < 50]
            elif f_price == 'u100': df = df[pr < 100]
            elif f_price == 'o5':   df = df[pr > 5]
            elif f_price == 'o10':  df = df[pr > 10]
            elif f_price == 'o20':  df = df[pr > 20]
            elif f_price == 'o50':  df = df[pr > 50]
            elif f_price == 'o100': df = df[pr > 100]

        # Avg Volume filter
        avg_vol_col = 'Avg Volume' if 'Avg Volume' in df.columns else None
        if f_avgvol and avg_vol_col:
            av = num_col(avg_vol_col)
            if f_avgvol == 'u50k':   df = df[av < 50e3]
            elif f_avgvol == 'u100k': df = df[av < 100e3]
            elif f_avgvol == 'u500k': df = df[av < 500e3]
            elif f_avgvol == 'o50k':  df = df[av > 50e3]
            elif f_avgvol == 'o100k': df = df[av > 100e3]
            elif f_avgvol == 'o500k': df = df[av > 500e3]
            elif f_avgvol == 'o1m':   df = df[av > 1e6]
            elif f_avgvol == 'o2m':   df = df[av > 2e6]

        # Current Volume filter
        vol_col = 'Volume' if 'Volume' in df.columns else None
        if f_curvol and vol_col:
            cv = num_col(vol_col)
            if f_curvol == 'u50k':   df = df[cv < 50e3]
            elif f_curvol == 'u100k': df = df[cv < 100e3]
            elif f_curvol == 'o50k':  df = df[cv > 50e3]
            elif f_curvol == 'o100k': df = df[cv > 100e3]
            elif f_curvol == 'o500k': df = df[cv > 500e3]
            elif f_curvol == 'o1m':   df = df[cv > 1e6]
            elif f_curvol == 'o5m':   df = df[cv > 5e6]
            elif f_curvol == 'o10m':  df = df[cv > 10e6]

        # Relative Volume — compare Volume to Avg Volume
        if f_relvol and 'Volume' in df.columns and avg_vol_col:
            rv = num_col('Volume') / num_col(avg_vol_col).replace(0, float('nan'))
            if f_relvol == 'o10':   df = df[rv > 10]
            elif f_relvol == 'o5':  df = df[rv > 5]
            elif f_relvol == 'o3':  df = df[rv > 3]
            elif f_relvol == 'o2':  df = df[rv > 2]
            elif f_relvol == 'o1.5': df = df[rv > 1.5]
            elif f_relvol == 'o1':  df = df[rv > 1]
            elif f_relvol == 'u1':  df = df[rv < 1]
            elif f_relvol == 'u0.5': df = df[rv < 0.5]

        # Sector
        if f_sector and 'Sector' in df.columns:
            df = df[df['Sector'] == f_sector]

        # Short Float
        if f_shortfloat and 'Short Float' in df.columns:
            sf = pd.to_numeric(df['Short Float'].astype(str).str.replace('%','',regex=False), errors='coerce')
            if f_shortfloat == 'low':  df = df[sf < 5]
            elif f_shortfloat == 'high': df = df[sf > 20]
            elif f_shortfloat == 'u5':  df = df[sf < 5]
            elif f_shortfloat == 'u10': df = df[sf < 10]
            elif f_shortfloat == 'u15': df = df[sf < 15]
            elif f_shortfloat == 'o10': df = df[sf > 10]
            elif f_shortfloat == 'o20': df = df[sf > 20]
            elif f_shortfloat == 'o30': df = df[sf > 30]

        # Shares Outstanding
        if f_sharesout and 'Shares Outstanding' in df.columns:
            so_raw = df['Shares Outstanding'].astype(str)
            def parse_shares(s):
                s = s.strip().upper()
                try:
                    if s.endswith('B'): return float(s[:-1]) * 1e9
                    if s.endswith('M'): return float(s[:-1]) * 1e6
                    if s.endswith('K'): return float(s[:-1]) * 1e3
                    return float(s)
                except: return float('nan')
            so = so_raw.apply(parse_shares)
            if f_sharesout == 'u1m':   df = df[so < 1e6]
            elif f_sharesout == 'u5m':   df = df[so < 5e6]
            elif f_sharesout == 'u10m':  df = df[so < 10e6]
            elif f_sharesout == 'u50m':  df = df[so < 50e6]
            elif f_sharesout == 'u100m': df = df[so < 100e6]
            elif f_sharesout == 'o1m':   df = df[so > 1e6]
            elif f_sharesout == 'o10m':  df = df[so > 10e6]
            elif f_sharesout == 'o50m':  df = df[so > 50e6]
            elif f_sharesout == 'o100m': df = df[so > 100e6]
            elif f_sharesout == 'o500m': df = df[so > 500e6]
            elif f_sharesout == 'o1b':   df = df[so > 1e9]

        # Institutional Ownership
        if f_instowner and 'Inst Own' in df.columns:
            io = pd.to_numeric(df['Inst Own'].astype(str).str.replace('%','',regex=False), errors='coerce')
            if f_instowner == 'low':  df = df[io < 5]
            elif f_instowner == 'high': df = df[io > 90]
            elif f_instowner == 'u20':  df = df[io < 20]
            elif f_instowner == 'u50':  df = df[io < 50]
            elif f_instowner == 'o50':  df = df[io > 50]
            elif f_instowner == 'o70':  df = df[io > 70]
            elif f_instowner == 'o90':  df = df[io > 90]

        # Insider Ownership
        if f_insiderown and 'Insider Own' in df.columns:
            ii = pd.to_numeric(df['Insider Own'].astype(str).str.replace('%','',regex=False), errors='coerce')
            if f_insiderown == 'low':  df = df[ii < 5]
            elif f_insiderown == 'high': df = df[ii > 30]
            elif f_insiderown == 'u5':   df = df[ii < 5]
            elif f_insiderown == 'u10':  df = df[ii < 10]
            elif f_insiderown == 'o5':   df = df[ii > 5]
            elif f_insiderown == 'o10':  df = df[ii > 10]
            elif f_insiderown == 'o20':  df = df[ii > 20]
            elif f_insiderown == 'o30':  df = df[ii > 30]

        # Change filter (on the Change column which is date-range %)
        if f_change and 'Change' in df.columns:
            ch = num_col('Change')
            if f_change == 'u1':   df = df[ch > 1]
            elif f_change == 'u2': df = df[ch > 2]
            elif f_change == 'u3': df = df[ch > 3]
            elif f_change == 'u5': df = df[ch > 5]
            elif f_change == 'u10': df = df[ch > 10]
            elif f_change == 'd1': df = df[ch < -1]
            elif f_change == 'd2': df = df[ch < -2]
            elif f_change == 'd5': df = df[ch < -5]
            elif f_change == 'd10': df = df[ch < -10]

        # Signal filter — derived metrics
        if f_signal and 'Change' in df.columns:
            ch = num_col('Change')
            av2 = num_col(avg_vol_col) if avg_vol_col else None
            cv2 = num_col(vol_col) if vol_col else None
            if f_signal == 'topgainers':    df = df.nlargest(50, 'Change')
            elif f_signal == 'toplosers':   df = df.nsmallest(50, 'Change')
            elif f_signal == 'mostactive' and cv2 is not None:   df = df.nlargest(50, vol_col)
            elif f_signal == 'unusualvol' and av2 is not None and cv2 is not None:
                rv2 = cv2 / av2.replace(0, float('nan'))
                df = df[rv2 > 2]
            elif f_signal == 'mostvolatile' and 'Beta' in df.columns:
                df = df.nlargest(50, 'Beta')
            elif f_signal == 'overbought':  df = df[ch > 10]
            elif f_signal == 'oversold':    df = df[ch < -10]
            elif f_signal == 'newhigh' and 'Price' in df.columns and '52W High' in df.columns:
                pr2 = num_col('Price')
                hi2 = num_col('52W High')
                df = df[pr2 >= hi2 * 0.99]
            elif f_signal == 'newlow' and 'Price' in df.columns and '52W Low' in df.columns:
                pr2 = num_col('Price')
                lo2 = num_col('52W Low')
                df = df[pr2 <= lo2 * 1.01]

        # Dividend filter
        if f_div and 'Dividend' in df.columns:
            dv = pd.to_numeric(df['Dividend'].astype(str).str.replace('%','',regex=False), errors='coerce').fillna(0)
            if f_div == 'none':  df = df[dv == 0]
            elif f_div == 'pos': df = df[dv > 0]
            elif f_div == 'high': df = df[dv > 5]
            elif f_div == 'o1':  df = df[dv > 1]
            elif f_div == 'o2':  df = df[dv > 2]
            elif f_div == 'o3':  df = df[dv > 3]
            elif f_div == 'o5':  df = df[dv > 5]

        # Change from Open filter
        if f_changefromopen and 'Change from Open' in df.columns:
            cfo = pd.to_numeric(df['Change from Open'].astype(str).str.replace('%','',regex=False), errors='coerce')
            if f_changefromopen == 'u1':   df = df[cfo > 1]
            elif f_changefromopen == 'u2': df = df[cfo > 2]
            elif f_changefromopen == 'u3': df = df[cfo > 3]
            elif f_changefromopen == 'u5': df = df[cfo > 5]
            elif f_changefromopen == 'd1': df = df[cfo < -1]
            elif f_changefromopen == 'd2': df = df[cfo < -2]
            elif f_changefromopen == 'd3': df = df[cfo < -3]
            elif f_changefromopen == 'd5': df = df[cfo < -5]

        # After Hours filter
        if f_afterhours and 'After-Hours Close' in df.columns and 'Price' in df.columns:
            pr = pd.to_numeric(df['Price'], errors='coerce')
            ah = pd.to_numeric(df['After-Hours Close'].astype(str).str.replace('%','',regex=False), errors='coerce')
            ah_chg = ((ah - pr) / pr * 100).where(pr != 0)
            if f_afterhours == 'up':   df = df[ah_chg > 0]
            elif f_afterhours == 'down': df = df[ah_chg < 0]
            elif f_afterhours == 'u1':  df = df[ah_chg > 1]
            elif f_afterhours == 'u2':  df = df[ah_chg > 2]
            elif f_afterhours == 'u5':  df = df[ah_chg > 5]
            elif f_afterhours == 'd1':  df = df[ah_chg < -1]
            elif f_afterhours == 'd2':  df = df[ah_chg < -2]
            elif f_afterhours == 'd5':  df = df[ah_chg < -5]

        # Option / Short Float filter
        if f_optshort and 'Optionable' in df.columns:
            if f_optshort == 'optionable':
                df = df[df['Optionable'].astype(str).str.lower() == 'yes']
            elif f_optshort == 'shortable' and 'Shortable' in df.columns:
                df = df[df['Shortable'].astype(str).str.lower() == 'yes']
            elif f_optshort == 'both' and 'Shortable' in df.columns:
                df = df[(df['Optionable'].astype(str).str.lower() == 'yes') &
                        (df['Shortable'].astype(str).str.lower() == 'yes')]
        if f_optshort in ('s10','s20','s30','s40') and 'Short Float' in df.columns:
            sf2 = pd.to_numeric(df['Short Float'].astype(str).str.replace('%','',regex=False), errors='coerce')
            thresh = {'s10':10,'s20':20,'s30':30,'s40':40}[f_optshort]
            df = df[sf2 > thresh]

        # News Sentiment filter (requires calc-sentiment-button to have been run)
        if f_sentiment and 'Sentiment Score' in df.columns:
            ss = pd.to_numeric(df['Sentiment Score'], errors='coerce')
            if f_sentiment == 'bullish':   df = df[ss >= 70]
            elif f_sentiment == 'leanbull': df = df[(ss >= 55) & (ss < 70)]
            elif f_sentiment == 'neutral':  df = df[(ss >= 45) & (ss < 55)]
            elif f_sentiment == 'leanbear': df = df[(ss >= 30) & (ss < 45)]
            elif f_sentiment == 'bearish':  df = df[ss < 30]
            elif f_sentiment == 'pos':      df = df[ss >= 55]
            elif f_sentiment == 'neg':      df = df[ss < 45]

        # Institutional Transactions filter
        if f_insttrans and 'Inst Trans' in df.columns:
            it = pd.to_numeric(df['Inst Trans'].astype(str).str.replace('%','',regex=False), errors='coerce')
            if f_insttrans == 'pos':   df = df[it > 0]
            elif f_insttrans == 'neg': df = df[it < 0]
            elif f_insttrans == 'vpos': df = df[it > 5]
            elif f_insttrans == 'vneg': df = df[it < -5]

    ascending = (sort_order == 'asc')
    if sort_by and sort_by in df.columns:
        df = df.sort_values(by=sort_by, ascending=ascending)

    count_msg = f"Showing {len(df)} of {total_before} stocks" if len(df) != total_before else ""
    records = df.to_dict('records')
    return records, (refresh_value*1000 if refresh_value > 0 else 0), "", records, count_msg, range_status


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
                      style={'color':'gray','marginTop':'20px'})
    page_current = page_current or 0
    page_size = page_size or 15
    row = active_cell['row'] + (page_current * page_size)
    if row >= len(virtual_data):
        return html.P("Row out of range.", style={'color':'gray'})
    ticker = virtual_data[row].get('Ticker', '')
    if not ticker:
        return html.P("No ticker selected.", style={'color':'gray'})
    return build_detail_panel(ticker, start_date=start_date, end_date=end_date)


@app.callback(
    [Output('filter-pe','value'),
     Output('filter-mktcap','value'),
     Output('filter-price','value'),
     Output('filter-avgvol','value'),
     Output('filter-relvol','value'),
     Output('filter-curvol','value'),
     Output('filter-sector','value'),
     Output('filter-shortfloat','value'),
     Output('filter-sharesout','value'),
     Output('filter-instowner','value'),
     Output('filter-insiderown','value'),
     Output('filter-change','value'),
     Output('filter-signal','value'),
     Output('filter-div-dropdown','value'),
     Output('filter-earnings','value'),
     Output('filter-changefromopen','value'),
     Output('filter-afterhours','value'),
     Output('filter-optshort','value'),
     Output('filter-sentiment','value'),
     Output('filter-insttrans','value')],
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
    return f"⏳ Calculating sentiment & 1M perf for up to {n_stocks} stocks… check the Sentiment and 1M Perf % columns when done."


# ─── Layout ───────────────────────────────────────────────────────────────────

app.layout = html.Div([
    html.Div(id='page-content', children=main_page())
])

if __name__ == '__main__':
    app.run(debug=False)
