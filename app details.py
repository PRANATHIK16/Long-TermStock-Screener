import dash
from dash import dcc, html, Input, Output, dash_table
import pandas as pd
import requests
from io import StringIO
from flask_caching import Cache
import plotly.graph_objs as go
import yfinance as yf

app = dash.Dash(__name__, suppress_callback_exceptions=True)
cache = Cache(app.server, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 600})

finviz_url = "ENTER YOUR FINVIZ URL HERE" 
@cache.memoize(timeout=600)
def fetch_finviz_data():
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/91.0.4472.124 Safari/537.36")
    }
    response = requests.get(finviz_url, headers=headers)
    print(f"Fetching new data from Finviz... Status: {response.status_code}")  

    if response.status_code == 200:
        try:
            df = pd.read_csv(StringIO(response.text))
            df.columns = df.columns.map(str)
            if 'Change' in df.columns:
                df['Change'] = pd.to_numeric(df['Change'].replace('%', '', regex=True), errors='coerce')
                if df['Change'].max() < 1:
                    df['Change'] = df['Change'] * 100
                df['Change'] = df['Change'].round(2)
           
            # Convert other relevant columns to numeric
            numeric_columns = [
                'Market Cap', 'P/E', 'Forward P/E', 'EPS (ttm)', 'EPS (next Y)', 
                'EPS Growth', 'Revenue', 'Operating Margin', 'ROE', 'Debt/Equity', 'Beta',
                'Change 1m', 'Change 3m', 'Change 1d', 'Change 1w', 'Change 1h', 'Change 1mo', 'Change 1y'
            ]
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            print(df.head())  
            return df
        except Exception as e:
            print(f"Error parsing data from Finviz: {e}")
            return pd.DataFrame({"Error": ["Failed to parse data from Finviz"]})
    elif response.status_code == 429:
        print("Rate limit exceeded. Please try again later.")
        return pd.DataFrame({"Error": ["Rate limit exceeded. Please try again later."]})
    else:
        print(f"Failed to fetch data. Status code: {response.status_code}")
        return pd.DataFrame({"Error": [f"Failed to fetch data. Status code: {response.status_code}"]})

def fetch_detailed_stock_data(ticker_symbol):
    stock = yf.Ticker(ticker_symbol)
    data = {
        "Index": stock.info.get("index", "N/A"),
        "Market Cap": stock.info.get("marketCap", "N/A"),
        "P/E": stock.info.get("trailingPE", "N/A"),
        "Forward P/E": stock.info.get("forwardPE", "N/A"),
        "EPS (ttm)": stock.info.get("trailingEps", "N/A"),
        "EPS (next Y)": stock.info.get("forwardEps", "N/A"),
        "EPS Growth": stock.info.get("earningsGrowth", "N/A"),
        "Revenue": stock.info.get("totalRevenue", "N/A"),
        "Operating Margin": stock.info.get("operatingMargins", "N/A"),
        "ROE": stock.info.get("returnOnEquity", "N/A"),
        "Debt/Equity": stock.info.get("debtToEquity", "N/A"),
        "Beta": stock.info.get("beta", "N/A"),
        "Volume": stock.info.get("regularMarketVolume", "N/A"),
        "52 Week High": stock.info.get("fiftyTwoWeekHigh", "N/A"),
        "52 Week Low": stock.info.get("fiftyTwoWeekLow", "N/A"),
        "Target Price": stock.info.get("targetMeanPrice", "N/A")
    }
    return pd.DataFrame(data.items(), columns=["Metric", "Value"])

def fetch_historical_data(ticker_symbol, interval):
    interval_mapping = {
        '1m':  ('7d', '1m'),
        '1d':  ('1y', '1d'),
        '1w':  ('2y', '1wk'),
        '1h':  ('1y', '60m'),
        '1mo': ('2y', '1mo'),
        '1y':  ('10y', '1mo')
    }
    
    period, yf_interval = interval_mapping.get(interval, ('1y', '1d'))
    
    stock = yf.Ticker(ticker_symbol)
    df = stock.history(period=period, interval=yf_interval)
    if df.empty:
        df = stock.history(period='1y', interval='1d')

    df.reset_index(inplace=True)
    if 'Date' not in df.columns:
        df.rename(columns={df.columns[0]: 'Date'}, inplace=True)

    df['Change %'] = ((df['Close'] - df['Open'].iloc[0]) / df['Open'].iloc[0]) * 100
    
    if not df.empty:
        df['SMA20'] = df['Close'].rolling(window=20).mean()
        df['SMA50'] = df['Close'].rolling(window=50).mean()
        df['SMA200'] = df['Close'].rolling(window=200).mean()

    return df

def calculate_overall_change(ticker_symbol, interval):
    hist_df = fetch_historical_data(ticker_symbol, interval)
    if hist_df.empty or len(hist_df) < 1:
        return None
    overall_change = ((hist_df['Close'].iloc[-1] - hist_df['Open'].iloc[0]) /
                      hist_df['Open'].iloc[0]) * 100
    return round(overall_change, 2)

def main_page():
    df = fetch_finviz_data()
    if df.empty or "Error" in df.columns:
        return html.Div(
            "No data available. Please check your Finviz configuration.", 
            style={'textAlign': 'center', 'color': 'red'}
        )

    # Define the timeframes for overall change calculation
    timeframes = ['1m', '1d', '1w', '1h', '1mo', '1y']
    
 
    for idx, row in df.head(20).iterrows():
        ticker = row.get('Ticker')
        if pd.isna(ticker):
            continue
        for tf in timeframes:
            try:
                change_val = calculate_overall_change(ticker, tf)
                df.at[idx, f'Change {tf}'] = change_val
            except Exception as e:
                print(f"Error calculating overall change for {ticker} on {tf}: {e}")
                df.at[idx, f'Change {tf}'] = None

    numeric_columns = [
        'Market Cap', 'P/E', 'Forward P/E', 'EPS (ttm)', 'EPS (next Y)', 
        'EPS Growth', 'Revenue', 'Operating Margin', 'ROE', 'Debt/Equity', 'Beta', 
        'Change', 'Change 1m', 'Change 3m', 'Change 1d', 'Change 1w', 'Change 1h', 'Change 1mo', 'Change 1y'
    ]

    return html.Div([
        html.H1("Stock Screener - Main Page", style={'textAlign': 'center', 'color': '#007BFF'}),
        
        html.Div([
            html.Div([
                html.Label("Search:"),
                dcc.Input(
                    id='search-input',
                    type='text',
                    placeholder='Type a ticker or company name...',
                    style={'marginRight': '10px'}
                )
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Button("Refresh Data", id="refresh-button", n_clicks=0, 
                        style={'backgroundColor': '#007BFF', 'color': 'white', 
                               'padding': '10px 20px', 'borderRadius': '5px'}),
            dcc.RadioItems(
                id='refresh-interval-radio',
                options=[
                    {'label': '10s', 'value': 10},
                    {'label': '1min', 'value': 60},
                    {'label': 'off', 'value': 0},
                ],
                value=0,
                labelStyle={'marginRight': '20px'}
            )
        ], style={'marginBottom': '20px'}),
        dcc.Interval(id='refresh-interval', interval=0, n_intervals=0),

        html.Div([
            html.Label("Sort By:"),
            dcc.Dropdown(
                id='sort-by-dropdown',
                options=[{'label': col, 'value': col} for col in df.columns],
                value='Ticker',
                style={'width': '45%', 'display': 'inline-block', 'marginRight': '10px'}
            ),
            dcc.RadioItems(
                id='sort-order',
                options=[
                    {'label': 'Ascending', 'value': 'asc'},
                    {'label': 'Descending', 'value': 'desc'}
                ],
                value='asc',
                style={'display': 'inline-block'}
            )
        ], style={'marginBottom': '20px'}),

        dash_table.DataTable(
            id='main-table',
            columns=[{"name": col, "id": col, "type": "numeric" if col in numeric_columns else "text"} for col in df.columns],
            data=df.to_dict('records'),
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'padding': '5px'},
            style_header={'backgroundColor': '#f4f4f4', 'fontWeight': 'bold'},
            style_data_conditional=[
                {
                    'if': {'column_id': 'Ticker'},
                    'cursor': 'pointer',
                    'color': 'blue',
                    'textDecoration': 'underline',
                },
                {
                    'if': {'filter_query': '{Change} > 50'},
                    'backgroundColor': '#90EE90',
                },
                {
                    'if': {'filter_query': '{Change} < -50'},
                    'backgroundColor': '#FFB6C1',
                }
            ],
            page_size=10,
            sort_action='native',
        ),

        html.Div("Click on a ticker to view detailed analysis.",
                 style={'textAlign': 'center', 'marginTop': '10px'}),
    ])

def detail_page(ticker_symbol):
    details_df = fetch_detailed_stock_data(ticker_symbol)
    return html.Div([
        html.H1(f"Details for {ticker_symbol}", style={'textAlign': 'center', 'color': '#007BFF'}),
        html.Label("Timeframe:"),
        dcc.Dropdown(
            id='timeframe-dropdown',
            options=[
                {'label': '1 Minute', 'value': '1m'},
        
                {'label': '1 Hour', 'value': '1h'},
                {'label': '1 Day', 'value': '1d'},
                {'label': '1 Week', 'value': '1w'},
                {'label': '1 Month', 'value': '1mo'},
                {'label': '1 Year', 'value': '1y'}
            ],
            value='1m',
            style={'width': '50%', 'marginBottom': '20px'}
        ),
        html.Label("Select SMAs to display:"),
        dcc.Checklist(
            id='sma-options',
            options=[
                {'label': 'SMA20', 'value': 'SMA20'},
                {'label': 'SMA50', 'value': 'SMA50'},
                {'label': 'SMA200', 'value': 'SMA200'}
            ],
            value=['SMA20', 'SMA50'],
            style={'marginBottom': '20px'}
        ),
        dcc.Graph(id='candlestick-chart'),
        html.H3("Volume", style={'textAlign': 'center'}),
        dcc.Graph(id='volume-chart'),
        html.H3("Stock Metrics", style={'textAlign': 'center'}),
        dash_table.DataTable(
            columns=[{"name": col, "id": col} for col in details_df.columns],
            data=details_df.to_dict('records'),
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'padding': '5px'},
            style_header={'backgroundColor': '#f4f4f4', 'fontWeight': 'bold'},
        ),
        dcc.Link('Back to Main Page', href='/', 
                 style={'textAlign': 'center', 'fontSize': '20px', 'color': '#007BFF'})
    ])

@app.callback(
    [Output('main-table', 'data'),
     Output('refresh-interval', 'interval')],
    [Input('refresh-button', 'n_clicks'),
     Input('refresh-interval-radio', 'value'),
     Input('sort-by-dropdown', 'value'),
     Input('sort-order', 'value')],
    prevent_initial_call=True
)
def update_main_table(n_clicks, refresh_value, sort_by, sort_order):
    print(f"Refresh triggered! Button Clicks: {n_clicks}, Auto-refresh Interval: {refresh_value}s")
    
    interval = refresh_value * 1000 if refresh_value > 0 else 0
    df = fetch_finviz_data()

    # Calculate overall changes for only the first 2 rows for testing.
    timeframes = ['1m', '1d', '1w', '1h', '1mo', '1y']
    for idx, row in df.head(20).iterrows():
        ticker = row.get('Ticker')
        if pd.isna(ticker):
            continue
        for tf in timeframes:
            try:
                change_val = calculate_overall_change(ticker, tf)
                df.at[idx, f'Change {tf}'] = change_val
            except Exception as e:
                print(f"Error calculating overall change for {ticker} on {tf}: {e}")
                df.at[idx, f'Change {tf}'] = None

    ascending = (sort_order == 'asc')
    time_intervals = ['Change 1m', 'Change 3m', 'Change 1d', 'Change 1w', 'Change 1h', 'Change 1mo', 'Change 1y']

    if sort_by in time_intervals:
        df['Change_numeric'] = pd.to_numeric(df[sort_by].astype(str).str.replace('%', ''), errors='coerce')
        df = df.sort_values(by='Change_numeric', ascending=ascending)
        df.drop(columns=['Change_numeric'], inplace=True)
    else:
        df = df.sort_values(by=sort_by, ascending=ascending)

    return df.to_dict('records'), interval

@app.callback(
    Output('url', 'pathname'),
    [Input('main-table', 'active_cell')],
    [dash.dependencies.State('main-table', 'data')]
)
def navigate_to_ticker(active_cell, table_data):
    if active_cell:
        row = active_cell['row']
        ticker_symbol = table_data[row]['Ticker']
        return f'/ticker/{ticker_symbol}'
    return '/'

@app.callback(
    [Output('candlestick-chart', 'figure'), Output('volume-chart', 'figure')],
    [Input('timeframe-dropdown', 'value'), Input('sma-options', 'value')],
    Input('url', 'pathname')
)
def update_detail_page(timeframe, sma_options, pathname):
    if pathname.startswith('/ticker/'):
        ticker_symbol = pathname.split('/')[2]
        historical_data = fetch_historical_data(ticker_symbol, timeframe)

        if not historical_data.empty:
            historical_data['Candle Change %'] = (
                (historical_data['Close'] - historical_data['Open']) /
                historical_data['Open']
            ) * 100

        hover_text = []
        for _, row in historical_data.iterrows():
            hover_text.append(
                f"Date: {row['Date']}<br>"
                f"Open: {row['Open']:.2f}<br>"
                f"High: {row['High']:.2f}<br>"
                f"Low: {row['Low']:.2f}<br>"
                f"Close: {row['Close']:.2f}<br>"
                f"Volume: {row['Volume']}<br>"
                f"Change: {row['Candle Change %']:.2f}%"
            )

        candlestick_chart = go.Figure()
        candlestick_chart.add_trace(go.Candlestick(
            x=historical_data['Date'],
            open=historical_data['Open'],
            high=historical_data['High'],
            low=historical_data['Low'],
            close=historical_data['Close'],
            name='Candlestick',
            text=hover_text,
            hoverinfo='text'
        ))

        if not historical_data.empty and len(historical_data) > 1:
            overall_change = ((historical_data['Close'].iloc[-1] - historical_data['Open'].iloc[0]) /
                              historical_data['Open'].iloc[0]) * 100
            candlestick_chart.add_annotation(
                x=historical_data['Date'].iloc[-1],
                y=historical_data['High'].max(),
                text=f"Overall Change: {overall_change:.2f}%",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-40
            )

        for sma in sma_options:
            if sma in historical_data.columns:
                candlestick_chart.add_trace(go.Scatter(
                    x=historical_data['Date'],
                    y=historical_data[sma],
                    mode='lines',
                    name=sma
                ))

        volume_chart = go.Figure()
        volume_chart.add_trace(go.Bar(
            x=historical_data['Date'],
            y=historical_data['Volume'],
            name='Volume'
        ))
        return candlestick_chart, volume_chart
    return {}, {}

@app.callback(
    Output('page-content', 'children'),
    Input('url', 'pathname')
)
def display_page(pathname):
    if pathname == '/' or pathname is None:
        return main_page()
    elif pathname.startswith('/ticker/'):
        ticker_symbol = pathname.split('/')[2]
        return detail_page(ticker_symbol)
    else:
        return html.H1("404: Page Not Found", style={'textAlign': 'center', 'color': 'red'})

app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    html.Div(id='page-content', children=main_page())
])

if __name__ == '__main__':
    app.run_server(debug=True)
