import psycopg2

conn = psycopg2.connect("postgresql://postgres:password@localhost:5432/stocklens")
cur = conn.cursor()

cur.execute("""
    SELECT
        COUNT(*) FILTER (WHERE data_source = 'YAHOO') AS yahoo_count,
        COUNT(*) FILTER (WHERE data_source = 'MOCK' OR data_source IS NULL) AS mock_count,
        COUNT(*) AS total
    FROM realtime_quotes
""")
r = cur.fetchone()
print(f"Yahoo live prices: {r[0]}")
print(f"Mock/null prices:  {r[1]}")
print(f"Total quotes:      {r[2]}")
print()

cur.execute("""
    SELECT nse_symbol, ltp, data_source, last_updated
    FROM realtime_quotes
    WHERE nse_symbol IN (
        'AUROPHARMA','RELIANCE','TCS','INFY','HDFCBANK',
        'AAPL','MSFT','NVDA','ICICIBANK','SBIN'
    )
    ORDER BY nse_symbol
""")
rows = cur.fetchall()
print(f"{'Symbol':<15} {'LTP':>14} {'Source':<8} Last Updated")
print("-" * 65)
for r in rows:
    print(f"{r[0]:<15} {float(r[1]):>14,.2f} {r[2]:<8} {r[3]}")

conn.close()
