import requests
import json
import yfinance as yf

# Test 1: v8 Chart API (multi-symbol quote is often blocked, but v8 chart is extremely reliable)
print("--- Test 1: v8 Chart API ---")
url_chart = "https://query2.finance.yahoo.com/v8/finance/chart/AAPL?region=US&lang=en-US&includePrePost=false&interval=2m&useYfid=true&range=1d"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
try:
    r = requests.get(url_chart, headers=headers, timeout=5)
    print("Chart Status:", r.status_code)
    if r.status_code == 200:
        meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        print("LTP:", meta.get("regularMarketPrice"))
        print("Prev Close:", meta.get("previousClose"))
except Exception as e:
    print("Chart Error:", e)

# Test 2: yfinance with a custom requests session and no thread pool (sometimes threads bypass session headers)
print("\n--- Test 2: yfinance with Custom Session (Single Thread) ---")
try:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    # Download single ticker to check
    df = yf.download("AAPL", period="1d", interval="1m", progress=False, session=session, threads=False)
    print("Download Status: Success" if not df.empty else "Download Status: Empty DataFrame")
    if not df.empty:
        print("LTP from df:", df['Close'].iloc[-1])
except Exception as e:
    print("yfinance error:", e)

# Test 3: v10 quoteSummary API
print("\n--- Test 3: v10 quoteSummary API ---")
url_summary = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL?modules=price"
try:
    r = requests.get(url_summary, headers=headers, timeout=5)
    print("Summary Status:", r.status_code)
    if r.status_code == 200:
        price = r.json().get("quoteSummary", {}).get("result", [{}])[0].get("price", {})
        print("LTP:", price.get("regularMarketPrice", {}).get("raw"))
except Exception as e:
    print("Summary Error:", e)
