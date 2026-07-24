# Stock-Screener-Internship

A stock screening and analysis dashboard built with Python and Dash. Pulls live data from **Finviz Elite** and **Yahoo Finance** to give you a research tool focused on long-term investing.

---

## What It Does 

### Screener Table
- Pulls your Finviz Elite screener export (any filters you set on Finviz carry over)
- Displays all fundamental columns: Market Cap, P/E, Forward P/E, EPS, ROE, Debt/Equity, Beta, Dividend, Volume, and more
- Sortable, filterable, and paginated — click any ticker to open its full detail view

### Date Range % Change
- Pick any **Start Date** and **End Date** and click **Calculate Change**
- Calculates the % price return for each stock over that exact period (open on start date → close on end date)
- Populates the **Change** column and auto-sorts highest to lowest
- Example: set Jan 2020 → today to see which stocks returned the most over 5 years

### Filters
All filters are dropdowns, matching Finviz's style:

| Filter | Options |
|--------|---------|
| P/E | Profitable, Low, Under 20/30/40/50, High, Over 100 |
| Market Cap | Mega, Large, Mid, Small, Micro, Nano |
| Price | Under/Over $1, $5, $10, $20, $50, $100 |
| Avg Volume | Under/Over 50K, 100K, 500K, 1M, 2M |
| Relative Volume | Over 10x, 5x, 3x, 2x, 1.5x, Under 1x |
| Current Volume | Under/Over 50K, 100K, 500K, 1M, 5M, 10M |
| Sector | All 11 GICS sectors |
| Short Float | Under/Over 5%, 10%, 15%, 20%, 30% |
| Shares Outstanding | Under/Over 1M through 1B |
| Inst Ownership | Low, High, Under/Over 20%, 50%, 70%, 90% |
| Insider Ownership | Low, High, Under/Over 5%, 10%, 20%, 30% |
| Change (%) | Up/Down >1%, 2%, 3%, 5%, 10% |
| Earnings Date | Today, This Week, Next Week, This Month |
| Change from Open | Up/Down >1%, 2%, 3%, 5% |
| After Hours | Up/Down >1%, 2%, 5% |
| Option/Short Float | Optionable, Shortable, Short >10/20/30/40% |
| Signal | Top Gainers/Losers, New High/Low, Most Active, Unusual Volume, Overbought/Oversold |
| Dividend | None, Positive, High, Over 1/2/3/5% |
| News Sentiment | Bullish, Leaning Bull, Neutral, Leaning Bear, Bearish |
| Inst Transactions | Positive/Negative buying/selling |

### News Sentiment Score
- Click **Calculate Sentiment & 1M Perf** to score each ticker's recent news
- Fetches the last 20 headlines per ticker from Yahoo Finance
- Scores 0–100 based on bullish vs bearish keyword analysis
- Runs in **parallel** (10 tickers at a time) for speed
- Score appears in the **Sentiment Score** column — filter by Bullish/Bearish in the Sentiment filter

### Stock Detail View
Click any ticker row to open a full detail panel showing:

- **Candlestick chart** with SMA 50 and SMA 200 overlays
- Chart respects your chosen **Start/End Date** — zoom into any historical period
- Rich **hover tooltips** showing Open, High, Low, Close, and Volume for each candle
- **% change over period** annotation on the chart
- **Price change over last 30 days** shown below the chart
- **Key Metrics panel** — Market Cap, P/E, Forward P/E, PEG, P/S, P/B, Dividend, EPS rows, ROE, ROA, D/E, Insider/Inst Ownership, Target Price, 52W Range, Beta, Avg Volume
- **News-Based Outlook** — keyword sentiment verdict (Bullish → Bearish) with supporting headlines
- **Latest News** — last 15 headlines with dates, sources, and clickable links

---

## Setup

### 1. Install dependencies
```bash
pip install dash flask-caching pandas requests yfinance plotly numpy
```

### 2. Get your Finviz Elite export URL
1. Log into [elite.finviz.com](https://elite.finviz.com)
2. Set your screener filters (or clear all to get every stock)
3. Right-click the **Export** button → **Copy Link Address**
4. The URL looks like:
   ```
   https://elite.finviz.com/export?v=111&f=sec_technology&auth=YOUR-KEY
   ```

### 3. Configure the app
Open `app custom change.py` and replace line 16:
```python
finviz_url = "YOUR-FINVIZ-URL-HERE"
```
with your actual export URL.

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

### Screening workflow
1. Your Finviz data loads automatically on startup
2. Use the **Filters** section to narrow down stocks — click **Apply Filters**
3. Set a **Start Date** and **End Date**, pick **# Stocks**, click **Calculate Change** to populate the Change column
4. Table auto-sorts by Change (highest return first)
5. Click **Calculate Sentiment** to add a 0–100 news sentiment score for each ticker
6. Click any ticker row to open its full detail panel

### Tips
- The **# Stocks** slider controls how many tickers get their Change/Sentiment calculated — start with 50, increase if you want more coverage
- Change the date pickers to update the **chart** for whichever ticker is selected
- Use **Signal → Top Gainers** to quickly surface the best performers after calculating Change
- **Sentiment Score > 65** = net positive news coverage; **< 35** = net negative

---

## Tech Stack

| Component | Library |
|-----------|---------|
| Dashboard UI | [Dash](https://dash.plotly.com/) by Plotly |
| Charts | Plotly Graph Objects |
| Market Data | [yfinance](https://github.com/ranaroussi/yfinance) |
| Screener Data | [Finviz Elite](https://elite.finviz.com/) |
| Caching | Flask-Caching |
| Data Processing | pandas, numpy |
| Parallelism | concurrent.futures (ThreadPoolExecutor) |

---

## File Structure

```
Project-Stock/
├── app custom change.py   # Main application — run this
└── README.md              # This file
```

---

## Notes

- **Finviz Elite subscription required** for the screener data export
- The `Change` column starts at 0 and populates only after you click **Calculate Change**
- The `Sentiment Score` column populates only after you click **Calculate Sentiment**
- yfinance data is fetched live — speed depends on your internet connection and how many tickers you're calculating
- The app caches Finviz data for 10 minutes to reduce API calls

---

## License

MIT License — free to use and modify for personal or commercial projects.
