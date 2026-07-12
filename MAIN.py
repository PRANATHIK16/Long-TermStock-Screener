import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
from finvizfinance.quote import finvizfinance
from datetime import datetime, timedelta
import time

class StockScreener:
    def __init__(self, symbol):
        self.symbol = symbol
        self.timeframe = '1h'
        self.data = None

    def fetch_stock_data_finviz(self):
        try:
            stock = finvizfinance(self.symbol)
            self.data = stock.ticker_fundament()
        except Exception as e:
            print(f"{datetime.now()} - Error fetching data from Finviz API for {self.symbol}: {e}")
            self.data = None

    def get_data_by_timeframe(self, timeframe='1h'):
        """
        Filters stock data based on the timeframe (e.g., hourly, daily, weekly).
        """
        self.timeframe = timeframe
        # Simulate data filtering by timeframe
        if self.data:
            df = pd.DataFrame(self.data.items(), columns=['Metric', 'Value'])
            df['Timeframe'] = self.timeframe
            self.data = df

    def display_data(self):
        if self.data is not None:
            print(f"{self.symbol} Stock Fundamentals ({self.timeframe} timeframe):")
            print(self.data)
        else:
            print("No data available to display.")

    def get_data_manual(self):
        try:
            url = f"https://finviz.com/quote.ashx?t={self.symbol}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                data = {}
                table = soup.find_all('table', class_='snapshot-table2')
                if table:
                    rows = table[0].find_all('tr')
                    for row in rows:
                        cols = row.find_all('td')
                        if len(cols) == 2:
                            key = cols[0].text.strip()
                            value = cols[1].text.strip()
                            data[key] = value
                return data
            else:
                print(f"{datetime.now()} - HTTP Error {response.status_code} for {self.symbol}")
                return None
        except Exception as e:
            print(f"{datetime.now()} - Error scraping data for {self.symbol}: {e}")
            return None

def log_data_for_tickers(tickers):
    start_time = datetime.now()
    print(f"\n{start_time} - Fetching data...")
    total_runtime = 0
    
    all_data = []

    for ticker in tickers:
        iteration_start = datetime.now()
        
        print(f"\nFetching data for {ticker}...")
        
        stock_data = StockScreener(ticker)
        
        stock_data.fetch_stock_data_finviz()
        if stock_data.data is not None:
            stock_data.get_data_by_timeframe('1M')
            stock_data.display_data()
            all_data.append((ticker, 'finvizfinance', stock_data.data))
        else:
            print(f"No data available using finvizfinance for {ticker}.")
        
        stock_data_manual = stock_data.get_data_manual()
        if stock_data_manual:
            df_manual = pd.DataFrame(stock_data_manual.items(), columns=['Metric', 'Value'])
            print(f"All data using manual scraping for {ticker}:")
            print(df_manual)
            all_data.append((ticker, 'manual_scraping', df_manual))
        else:
            print(f"No data available for {ticker} using manual scraping.")
        
        iteration_end = datetime.now()
        iteration_runtime = (iteration_end - iteration_start).total_seconds()
        total_runtime += iteration_runtime
        print(f"Iteration runtime: {iteration_runtime:.2f} seconds")

    for ticker, method, data in all_data:
        filename = f"{ticker}_{method}_data_{start_time.strftime('%Y%m%d_%H%M%S')}.csv"
        data.to_csv(filename, index=False)
        print(f"Data saved to {filename}")

    end_time = datetime.now()
    total_runtime = (end_time - start_time).total_seconds()
    print(f"\nTotal runtime: {total_runtime:.2f} seconds")


def fetch_data_at_interval(tickers, interval_minutes):
    while True:
        log_data_for_tickers(tickers)
        sleep_time = interval_minutes * 60
        print(f"Sleeping for {sleep_time} seconds...")
        time.sleep(sleep_time)

# Example usage
if __name__ == "__main__":
    tickers = ['AAPL', 'MSFT', 'GOOGL']  
    print("Tickers:", tickers)
    interval_minutes = 30
    fetch_data_at_interval(tickers, interval_minutes)
