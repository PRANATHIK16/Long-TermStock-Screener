# Stock-Screener-Internship

A long-term stock screening and analysis dashboard built with Python and Dash. Pulls live data from **Finviz Elite** and **Yahoo Finance** to give you a research tool focused on long-term investing, sentiment analysis, and fundamental screening.

---

## What It Does

### Screener Table
- Pulls your Finviz Elite screener export via the official API (auth token in URL)
- Displays fundamental columns: Market Cap, P/E, Forward P/E, EPS, ROE, Debt/Equity, Beta, Dividend, Volume, and more
- Volume columns formatted as human-readable numbers (1.2M, 500K, 2.1B)
- Sortable, filterable, and paginated — click any ticker to open its full detail view
- Defaults to sorting by Change (highest return first)

### Date Range % Change
- Pick any **Start Date** and **End Date** and click **Calculate Change**
- Calculates % price return from open on start date to close on end date for each stock
- Populates the **Change** column and auto-sorts highest to lowest
- Runs in parallel (10 workers) for speed
- Example: set Jan 2020 → today to rank which stocks returned the most over 5 years

### Sentiment Scoring
Two scores calculated per ticker when you click **Calculate Sentiment Score**:

**Short-Term Sentiment Score (0–100)**
- Fetches last 30 news headlines per ticker from Yahoo Finance
- Only scores headlines from trusted sources: PR Newswire, Globe Newswire, AccessWire, MT Wire, Dow Jones Wire, Benzinga, Reuters/Tradingview, SEC, FDA
- Scores based on specific corporate event keywords:
  - Bullish: Acquisition, Buyback, Contract, Listing, IPO, Merger, Partnership, Earnings, Beat, FDA Approval
  - Bearish: Delisting, Compliance, Miss, Investigation, Probe, SEC, Bankruptcy
- 100 = all bullish signals, 0 = all bearish, 50 = neutral

**Long-Term Sentiment Score (0–100)**
- Based on price/fundamental momentum using fast_info (no full history download):
  - Price position within 52-week range (40%)
  - Analyst recommendation rating (30%)
  - 52-week return proxy (30%)
- Reflects how the stock has been performing over the past year

Both scores run in **parallel (20 workers)** and include:
- **Sentiment Rank** and **LT Sentiment Rank** columns (#1 = best)
- Auto-sorts by Sentiment Score after calculation
- Scores persist across refreshes within the same session

 **Sentiment scores are all "Neutral" / 50**
— this is usually expected behavior, not a bug. The sentiment scorer only counts headlines from a specific list of trusted wire services (PR Newswire, GlobeNewswire, Benzinga, Reuters, etc). Yahoo Finance's news feed pulls from a lot of other publishers too (Motley Fool, Zacks, Yahoo itself), and if none of a ticker's recent headlines happen to be from a trusted source, it just defaults to neutral. The app prints exactly what sources it found for each ticker to your terminal while it's scoring, so you can check that directly if scores look off.

**Sentiment/momentum scoring is slow**
— each ticker needs several separate calls to Yahoo Finance (news, price history, analyst data), and Yahoo will throttle you if you hit it too hard. Scoring runs in parallel already, but for a big list of stocks it's still going to take a bit. Lower the "# Stocks" slider if you just want a quick test.

### Filters
All filters are dropdowns matching Finviz's style. Click **Apply Filters** to filter, **Clear Filters** to reset.

| Filter | Options |
|--------|---------|
| P/E | Profitable, Low (<15), Under 20/30/50, High (>50), Over 100 |
| Market Cap | Mega (>200B), Large, Mid, Small, Micro, Nano |
| Price | Under/Over $5, $10, $20, $50, $100 |
| Avg Volume | Under/Over 100K, 500K, 1M, 2M |
| Relative Volume | Over 10x, 5x, 3x, 2x, 1x, Under 1x |
| Current Volume | Over 100K, 500K, 1M, 5M, 10M |
| Sector | All 11 GICS sectors |
| Short Float | Low (<5%), Over 10%, 20%, 30% |
| Shares Outstanding | Under/Over 10M through 1B |
| Inst Ownership | Low, High, Over 50%, 70%, 90% |
| Insider Ownership | Low, High, Over 10%, 20%, 30% |
| Change (%) | Up/Down >5%, 10%, 20% |
| Change from Open | Up/Down >1%, 3%, 5% |
| After Hours | Up/Down, >2%, >5% |
| Option/Short Float | Optionable, Shortable, Short >10/20/30% |
| Earnings Date | Today, This Week, Next Week, This Month |
| Dividend | None, Positive, Over 1/2/3/5%, High (>5%) |
| Signal | Top Gainers/Losers, New High/Low, Most Active, Unusual Volume, Most Volatile, Overbought/Oversold |
| News Sentiment | Bullish (≥70), Leaning Bull, Neutral, Leaning Bear, Bearish (<30) |
| Inst Transactions | Buying, Selling, Strong Buying (>5%), Strong Selling (<-5%) |

### Stock Detail View
Click any ticker row to open a full detail panel:

- **Candlestick chart** with SMA 50 and SMA 200 overlays
- Chart respects your chosen Start/End Date range
- Rich hover tooltips showing Open, High, Low, Close, and Volume per candle
- % change over period annotation on the chart
- **Price change over last 30 days** shown directly below the chart
- **Key Metrics panel** — 24 metrics including Market Cap, P/E, PEG, P/S, P/B, Dividend, EPS, ROE, ROA, D/E, Gross/Op Margins, Insider/Inst Ownership, Target Price, 52W Range, Beta, Avg Volume
- **News Sentiment verdict** — Bullish/Bearish badge with supporting headlines from trusted sources only
- **Latest 15 news headlines** with dates, sources, and clickable links

---

## Setup

### 1. Install dependencies
```bash
pip install dash flask-caching pandas requests yfinance plotly numpy
```

### 2. Get your Finviz Elite API URL
1. Log into [elite.finviz.com](https://elite.finviz.com)
2. Go to `elite.finviz.com/api_explanation`
3. Set your screener filters, copy the **Example Export API URL** — it looks like:
   ```
   https://elite.finviz.com/export/screener?v=111&f=your_filters&auth=YOUR-TOKEN
   ```

### 3. Configure the app
Open `app custom change.py` and update line 16:
```python
finviz_url = "https://elite.finviz.com/export/screener?v=111&f=your_filters&auth=YOUR-TOKEN"
```

### 4. Run the app
```bash
python "app custom change.py"
```

### 5. Open in browser
```
http://127.0.0.1:8050
```

---

## How to Use

### Basic screening workflow
1. Finviz data loads automatically on startup
2. Use **Filters** to narrow down stocks → click **Apply Filters**
3. Set **Start Date** and **End Date**, pick **# Stocks**, click **Calculate Change**
4. Table auto-sorts by Change (best performers first)
5. Click **Calculate Sentiment Score** to score news sentiment and long-term momentum for each ticker
6. Filter by **News Sentiment → Bullish** to find stocks with positive news coverage
7. Click any ticker row to open the full detail panel

### Tips
- Start with **# Stocks = 50** for both Change and Sentiment calculations — increase for more coverage
- Changing the date pickers also updates the chart for whichever ticker is currently open
- **Sentiment Score > 65** = net positive news from trusted sources; **< 35** = net negative
- **LT Sentiment Score > 65** = stock trending above its 52W range with strong analyst support
- Use **Signal → Top Gainers** after calculating Change to surface best performers quickly
- If the app shows an auth error, get a fresh API URL from `elite.finviz.com/api_explanation`

---

## Tech Stack

| Component | Library |
|-----------|---------|
| Dashboard UI | [Dash](https://dash.plotly.com/) by Plotly |
| Charts | Plotly Graph Objects |
| Market Data | [yfinance](https://github.com/ranaroussi/yfinance) |
| Screener Data | [Finviz Elite API](https://elite.finviz.com/api_explanation) |
| Data Processing | pandas |
| Parallelism | concurrent.futures (ThreadPoolExecutor, 20 workers) |

---

## File Structure

```
Project-Stock/
├── MAIN.py
├── README.md              # This file
└── app custom change.py   # Main application — run this``` 
```

---

## Notes

- **Finviz Elite subscription required** — get your API token at `elite.finviz.com/api_explanation`
- The `Change` column starts at 0 and populates only after clicking **Calculate Change**
- `Sentiment Score` and `LT Sentiment Score` populate only after clicking **Calculate Sentiment Score**
- Sentiment only counts headlines from the 9 trusted news sources listed above — general blogs are ignored
- All computed values (Change, Sentiment scores) persist in memory for the session but reset on restart
- yfinance data is fetched live — speed depends on internet connection and number of tickers

---
