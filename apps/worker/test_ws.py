import os
import requests
from dotenv import load_dotenv
import websocket
import logging

logging.basicConfig(level=logging.DEBUG)

load_dotenv(r"c:\New folder (2)\stocklens\.env")
token = os.environ.get("UPSTOX_ACCESS_TOKEN")

headers = {'Authorization': f'Bearer {token}', "Accept": "application/json"}
resp = requests.get("https://api.upstox.com/v3/feed/market-data-feed/authorize", headers=headers)
print("Auth resp:", resp.status_code, resp.text)
data = resp.json()
ws_url = data.get("data", {}).get("authorized_redirect_uri")
print("WS URL:", ws_url)

if ws_url:
    def on_open(ws):
        print("WS OPEN")
        ws.close()
    def on_error(ws, error):
        print("WS ERROR:", error)
    def on_close(ws, close_status_code, close_msg):
        print("WS CLOSE", close_status_code, close_msg)
    
    # Try connecting without custom headers first
    print("\nConnecting without headers...")
    ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_error=on_error, on_close=on_close)
    ws.run_forever()
    
    print("\nConnecting WITH Authorization header...")
    ws2 = websocket.WebSocketApp(ws_url, header=[f'Authorization: Bearer {token}'], on_open=on_open, on_error=on_error, on_close=on_close)
    ws2.run_forever()
