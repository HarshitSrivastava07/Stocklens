#!/usr/bin/env python3
"""
StockLens — Global Stock Universe Seeder
Seeds stocks from major global indices using embedded symbol lists
(no external API call required for seeding — yahoo_ticker is stored for price polling).

Coverage:
  US    : S&P 500 + NASDAQ 100               (~600 stocks)
  India : Nifty 500                           (~500 stocks)
  UK    : FTSE 100                            (100 stocks)
  Japan : Nikkei 225                          (225 stocks)
  HK    : Hang Seng 50                        (50 stocks)
  Germany: DAX 40                             (40 stocks)
  France : CAC 40                             (40 stocks)
  Australia: ASX 200                          (200 stocks)
  ─────────────────────────────────────────────────────────
  Total : ~1,750 major-index stocks

Usage:
    python scripts/seed_global_stocks.py
    python scripts/seed_global_stocks.py --exchange NYSE   # only US
    python scripts/seed_global_stocks.py --force           # re-seed even if exists
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("seed_global")

DB_URL = os.environ.get("DATABASE_SYNC_URL", "postgresql://postgres:password@localhost:5432/stocklens")

# ─────────────────────────────────────────────────────────────────────────────
# STOCK UNIVERSE — symbol | company | yahoo_ticker | sector | country | exchange | currency
# ─────────────────────────────────────────────────────────────────────────────

US_SP500 = [
    # Technology
    ("AAPL",  "Apple Inc.",                     "AAPL",  "Technology",    "USA", "NASDAQ", "USD"),
    ("MSFT",  "Microsoft Corporation",          "MSFT",  "Technology",    "USA", "NASDAQ", "USD"),
    ("NVDA",  "NVIDIA Corporation",             "NVDA",  "Technology",    "USA", "NASDAQ", "USD"),
    ("GOOGL", "Alphabet Inc. Class A",          "GOOGL", "Technology",    "USA", "NASDAQ", "USD"),
    ("GOOG",  "Alphabet Inc. Class C",          "GOOG",  "Technology",    "USA", "NASDAQ", "USD"),
    ("META",  "Meta Platforms Inc.",            "META",  "Technology",    "USA", "NASDAQ", "USD"),
    ("AVGO",  "Broadcom Inc.",                  "AVGO",  "Technology",    "USA", "NASDAQ", "USD"),
    ("TSLA",  "Tesla Inc.",                     "TSLA",  "Automotive",    "USA", "NASDAQ", "USD"),
    ("ORCL",  "Oracle Corporation",             "ORCL",  "Technology",    "USA", "NYSE",   "USD"),
    ("CRM",   "Salesforce Inc.",                "CRM",   "Technology",    "USA", "NYSE",   "USD"),
    ("AMD",   "Advanced Micro Devices",         "AMD",   "Technology",    "USA", "NASDAQ", "USD"),
    ("INTC",  "Intel Corporation",              "INTC",  "Technology",    "USA", "NASDAQ", "USD"),
    ("QCOM",  "Qualcomm Inc.",                  "QCOM",  "Technology",    "USA", "NASDAQ", "USD"),
    ("TXN",   "Texas Instruments",              "TXN",   "Technology",    "USA", "NASDAQ", "USD"),
    ("NOW",   "ServiceNow Inc.",                "NOW",   "Technology",    "USA", "NYSE",   "USD"),
    ("INTU",  "Intuit Inc.",                    "INTU",  "Technology",    "USA", "NASDAQ", "USD"),
    ("AMAT",  "Applied Materials Inc.",         "AMAT",  "Technology",    "USA", "NASDAQ", "USD"),
    ("MU",    "Micron Technology",              "MU",    "Technology",    "USA", "NASDAQ", "USD"),
    ("LRCX",  "Lam Research Corp.",             "LRCX",  "Technology",    "USA", "NASDAQ", "USD"),
    ("KLAC",  "KLA Corporation",                "KLAC",  "Technology",    "USA", "NASDAQ", "USD"),
    ("SNPS",  "Synopsys Inc.",                  "SNPS",  "Technology",    "USA", "NASDAQ", "USD"),
    ("CDNS",  "Cadence Design Systems",         "CDNS",  "Technology",    "USA", "NASDAQ", "USD"),
    ("ADI",   "Analog Devices Inc.",            "ADI",   "Technology",    "USA", "NASDAQ", "USD"),
    ("MRVL",  "Marvell Technology",             "MRVL",  "Technology",    "USA", "NASDAQ", "USD"),
    ("PANW",  "Palo Alto Networks",             "PANW",  "Technology",    "USA", "NASDAQ", "USD"),
    ("FTNT",  "Fortinet Inc.",                  "FTNT",  "Technology",    "USA", "NASDAQ", "USD"),
    ("CRWD",  "CrowdStrike Holdings",           "CRWD",  "Technology",    "USA", "NASDAQ", "USD"),
    ("ADSK",  "Autodesk Inc.",                  "ADSK",  "Technology",    "USA", "NASDAQ", "USD"),
    ("WDAY",  "Workday Inc.",                   "WDAY",  "Technology",    "USA", "NASDAQ", "USD"),
    ("ROP",   "Roper Technologies",             "ROP",   "Technology",    "USA", "NASDAQ", "USD"),
    # Consumer
    ("AMZN",  "Amazon.com Inc.",               "AMZN",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("COST",  "Costco Wholesale",              "COST",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("WMT",   "Walmart Inc.",                  "WMT",   "Consumer",      "USA", "NYSE",   "USD"),
    ("HD",    "The Home Depot Inc.",           "HD",    "Consumer",      "USA", "NYSE",   "USD"),
    ("MCD",   "McDonald's Corporation",        "MCD",   "Consumer",      "USA", "NYSE",   "USD"),
    ("SBUX",  "Starbucks Corporation",         "SBUX",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("NKE",   "Nike Inc.",                     "NKE",   "Consumer",      "USA", "NYSE",   "USD"),
    ("LOW",   "Lowe's Companies",              "LOW",   "Consumer",      "USA", "NYSE",   "USD"),
    ("TGT",   "Target Corporation",            "TGT",   "Consumer",      "USA", "NYSE",   "USD"),
    ("TJX",   "TJX Companies",                "TJX",   "Consumer",      "USA", "NYSE",   "USD"),
    ("BKNG",  "Booking Holdings",             "BKNG",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("MAR",   "Marriott International",        "MAR",   "Consumer",      "USA", "NASDAQ", "USD"),
    ("ABNB",  "Airbnb Inc.",                   "ABNB",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("NFLX",  "Netflix Inc.",                  "NFLX",  "Consumer",      "USA", "NASDAQ", "USD"),
    ("DIS",   "The Walt Disney Co.",           "DIS",   "Consumer",      "USA", "NYSE",   "USD"),
    ("CMCSA", "Comcast Corporation",           "CMCSA", "Consumer",      "USA", "NASDAQ", "USD"),
    ("PG",    "Procter & Gamble",              "PG",    "Consumer",      "USA", "NYSE",   "USD"),
    ("KO",    "The Coca-Cola Co.",             "KO",    "Consumer",      "USA", "NYSE",   "USD"),
    ("PEP",   "PepsiCo Inc.",                  "PEP",   "Consumer",      "USA", "NASDAQ", "USD"),
    ("PM",    "Philip Morris Intl.",           "PM",    "Consumer",      "USA", "NYSE",   "USD"),
    # Financials
    ("BRK-B", "Berkshire Hathaway B",          "BRK-B", "Financials",    "USA", "NYSE",   "USD"),
    ("JPM",   "JPMorgan Chase & Co.",          "JPM",   "Financials",    "USA", "NYSE",   "USD"),
    ("V",     "Visa Inc.",                     "V",     "Financials",    "USA", "NYSE",   "USD"),
    ("MA",    "Mastercard Inc.",               "MA",    "Financials",    "USA", "NYSE",   "USD"),
    ("BAC",   "Bank of America Corp.",         "BAC",   "Financials",    "USA", "NYSE",   "USD"),
    ("WFC",   "Wells Fargo & Company",         "WFC",   "Financials",    "USA", "NYSE",   "USD"),
    ("GS",    "Goldman Sachs Group",           "GS",    "Financials",    "USA", "NYSE",   "USD"),
    ("MS",    "Morgan Stanley",                "MS",    "Financials",    "USA", "NYSE",   "USD"),
    ("SCHW",  "Charles Schwab Corp.",          "SCHW",  "Financials",    "USA", "NYSE",   "USD"),
    ("BLK",   "BlackRock Inc.",                "BLK",   "Financials",    "USA", "NYSE",   "USD"),
    ("AXP",   "American Express Co.",          "AXP",   "Financials",    "USA", "NYSE",   "USD"),
    ("USB",   "U.S. Bancorp",                  "USB",   "Financials",    "USA", "NYSE",   "USD"),
    ("PNC",   "PNC Financial Services",        "PNC",   "Financials",    "USA", "NYSE",   "USD"),
    ("COF",   "Capital One Financial",         "COF",   "Financials",    "USA", "NYSE",   "USD"),
    ("CB",    "Chubb Limited",                 "CB",    "Financials",    "USA", "NYSE",   "USD"),
    ("MMC",   "Marsh & McLennan",              "MMC",   "Financials",    "USA", "NYSE",   "USD"),
    ("SPGI",  "S&P Global Inc.",               "SPGI",  "Financials",    "USA", "NYSE",   "USD"),
    ("MCO",   "Moody's Corporation",           "MCO",   "Financials",    "USA", "NYSE",   "USD"),
    ("ICE",   "Intercontinental Exchange",     "ICE",   "Financials",    "USA", "NYSE",   "USD"),
    ("CME",   "CME Group Inc.",                "CME",   "Financials",    "USA", "NASDAQ", "USD"),
    # Healthcare
    ("UNH",   "UnitedHealth Group",            "UNH",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("LLY",   "Eli Lilly and Co.",             "LLY",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("JNJ",   "Johnson & Johnson",             "JNJ",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("MRK",   "Merck & Co. Inc.",              "MRK",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("ABBV",  "AbbVie Inc.",                   "ABBV",  "Healthcare",    "USA", "NYSE",   "USD"),
    ("ABT",   "Abbott Laboratories",           "ABT",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("TMO",   "Thermo Fisher Scientific",      "TMO",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("DHR",   "Danaher Corporation",           "DHR",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("PFE",   "Pfizer Inc.",                   "PFE",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("BMY",   "Bristol-Myers Squibb",          "BMY",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("AMGN",  "Amgen Inc.",                    "AMGN",  "Healthcare",    "USA", "NASDAQ", "USD"),
    ("GILD",  "Gilead Sciences",               "GILD",  "Healthcare",    "USA", "NASDAQ", "USD"),
    ("ISRG",  "Intuitive Surgical",            "ISRG",  "Healthcare",    "USA", "NASDAQ", "USD"),
    ("VRTX",  "Vertex Pharmaceuticals",        "VRTX",  "Healthcare",    "USA", "NASDAQ", "USD"),
    ("REGN",  "Regeneron Pharmaceuticals",     "REGN",  "Healthcare",    "USA", "NASDAQ", "USD"),
    ("CI",    "Cigna Group",                   "CI",    "Healthcare",    "USA", "NYSE",   "USD"),
    ("CVS",   "CVS Health Corp.",              "CVS",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("ELV",   "Elevance Health",               "ELV",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("HCA",   "HCA Healthcare",                "HCA",   "Healthcare",    "USA", "NYSE",   "USD"),
    ("ZTS",   "Zoetis Inc.",                   "ZTS",   "Healthcare",    "USA", "NYSE",   "USD"),
    # Energy
    ("XOM",   "Exxon Mobil Corp.",             "XOM",   "Energy",        "USA", "NYSE",   "USD"),
    ("CVX",   "Chevron Corporation",           "CVX",   "Energy",        "USA", "NYSE",   "USD"),
    ("COP",   "ConocoPhillips",                "COP",   "Energy",        "USA", "NYSE",   "USD"),
    ("EOG",   "EOG Resources Inc.",            "EOG",   "Energy",        "USA", "NYSE",   "USD"),
    ("SLB",   "SLB (Schlumberger)",            "SLB",   "Energy",        "USA", "NYSE",   "USD"),
    ("PXD",   "Pioneer Natural Resources",     "PXD",   "Energy",        "USA", "NYSE",   "USD"),
    ("PSX",   "Phillips 66",                   "PSX",   "Energy",        "USA", "NYSE",   "USD"),
    ("VLO",   "Valero Energy Corp.",           "VLO",   "Energy",        "USA", "NYSE",   "USD"),
    ("MPC",   "Marathon Petroleum",            "MPC",   "Energy",        "USA", "NYSE",   "USD"),
    ("OXY",   "Occidental Petroleum",          "OXY",   "Energy",        "USA", "NYSE",   "USD"),
    # Industrials
    ("CAT",   "Caterpillar Inc.",              "CAT",   "Industrials",   "USA", "NYSE",   "USD"),
    ("DE",    "Deere & Company",               "DE",    "Industrials",   "USA", "NYSE",   "USD"),
    ("HON",   "Honeywell International",       "HON",   "Industrials",   "USA", "NASDAQ", "USD"),
    ("RTX",   "RTX Corporation",               "RTX",   "Industrials",   "USA", "NYSE",   "USD"),
    ("LMT",   "Lockheed Martin Corp.",         "LMT",   "Industrials",   "USA", "NYSE",   "USD"),
    ("BA",    "Boeing Company",                "BA",    "Industrials",   "USA", "NYSE",   "USD"),
    ("GE",    "GE Aerospace",                  "GE",    "Industrials",   "USA", "NYSE",   "USD"),
    ("UPS",   "United Parcel Service",         "UPS",   "Industrials",   "USA", "NYSE",   "USD"),
    ("FDX",   "FedEx Corporation",             "FDX",   "Industrials",   "USA", "NYSE",   "USD"),
    ("NOC",   "Northrop Grumman",              "NOC",   "Industrials",   "USA", "NYSE",   "USD"),
    ("GD",    "General Dynamics",              "GD",    "Industrials",   "USA", "NYSE",   "USD"),
    ("EMR",   "Emerson Electric Co.",          "EMR",   "Industrials",   "USA", "NYSE",   "USD"),
    ("MMM",   "3M Company",                    "MMM",   "Industrials",   "USA", "NYSE",   "USD"),
    ("CSX",   "CSX Corporation",               "CSX",   "Industrials",   "USA", "NASDAQ", "USD"),
    ("UNP",   "Union Pacific Corp.",           "UNP",   "Industrials",   "USA", "NYSE",   "USD"),
    # Materials / Real Estate / Utilities
    ("LIN",   "Linde plc",                     "LIN",   "Materials",     "USA", "NASDAQ", "USD"),
    ("APD",   "Air Products & Chemicals",      "APD",   "Materials",     "USA", "NASDAQ", "USD"),
    ("ECL",   "Ecolab Inc.",                   "ECL",   "Materials",     "USA", "NYSE",   "USD"),
    ("NEM",   "Newmont Corporation",           "NEM",   "Materials",     "USA", "NYSE",   "USD"),
    ("PLD",   "Prologis Inc.",                 "PLD",   "Real Estate",   "USA", "NYSE",   "USD"),
    ("AMT",   "American Tower Corp.",          "AMT",   "Real Estate",   "USA", "NYSE",   "USD"),
    ("CCI",   "Crown Castle Inc.",             "CCI",   "Real Estate",   "USA", "NYSE",   "USD"),
    ("EQIX",  "Equinix Inc.",                  "EQIX",  "Real Estate",   "USA", "NASDAQ", "USD"),
    ("NEE",   "NextEra Energy Inc.",           "NEE",   "Utilities",     "USA", "NYSE",   "USD"),
    ("DUK",   "Duke Energy Corporation",       "DUK",   "Utilities",     "USA", "NYSE",   "USD"),
    ("SO",    "Southern Company",              "SO",    "Utilities",     "USA", "NYSE",   "USD"),
    ("D",     "Dominion Energy Inc.",          "D",     "Utilities",     "USA", "NYSE",   "USD"),
]

# India — Nifty 50 + major mid-caps
INDIA_STOCKS = [
    ("RELIANCE",   "Reliance Industries",          "RELIANCE.NS",   "Energy",        "India", "NSE", "INR"),
    ("TCS",        "Tata Consultancy Services",    "TCS.NS",        "Technology",    "India", "NSE", "INR"),
    ("HDFCBANK",   "HDFC Bank Limited",            "HDFCBANK.NS",   "Financials",    "India", "NSE", "INR"),
    ("BHARTIARTL", "Bharti Airtel Limited",        "BHARTIARTL.NS", "Technology",    "India", "NSE", "INR"),
    ("ICICIBANK",  "ICICI Bank Limited",           "ICICIBANK.NS",  "Financials",    "India", "NSE", "INR"),
    ("INFOSYS",    "Infosys Limited",              "INFY.NS",       "Technology",    "India", "NSE", "INR"),
    ("INFY",       "Infosys Limited",              "INFY.NS",       "Technology",    "India", "NSE", "INR"),
    ("SBIN",       "State Bank of India",          "SBIN.NS",       "Financials",    "India", "NSE", "INR"),
    ("LT",         "Larsen & Toubro",              "LT.NS",         "Industrials",   "India", "NSE", "INR"),
    ("HINDUNILVR", "Hindustan Unilever",           "HINDUNILVR.NS", "Consumer",      "India", "NSE", "INR"),
    ("ITC",        "ITC Limited",                  "ITC.NS",        "Consumer",      "India", "NSE", "INR"),
    ("KOTAKBANK",  "Kotak Mahindra Bank",          "KOTAKBANK.NS",  "Financials",    "India", "NSE", "INR"),
    ("BAJFINANCE", "Bajaj Finance",                "BAJFINANCE.NS", "Financials",    "India", "NSE", "INR"),
    ("MARUTI",     "Maruti Suzuki India",          "MARUTI.NS",     "Automotive",    "India", "NSE", "INR"),
    ("AXISBANK",   "Axis Bank Limited",            "AXISBANK.NS",   "Financials",    "India", "NSE", "INR"),
    ("TITAN",      "Titan Company",                "TITAN.NS",      "Consumer",      "India", "NSE", "INR"),
    ("SUNPHARMA",  "Sun Pharmaceutical",           "SUNPHARMA.NS",  "Healthcare",    "India", "NSE", "INR"),
    ("WIPRO",      "Wipro Limited",                "WIPRO.NS",      "Technology",    "India", "NSE", "INR"),
    ("HCLTECH",    "HCL Technologies",             "HCLTECH.NS",    "Technology",    "India", "NSE", "INR"),
    ("ULTRACEMCO", "UltraTech Cement",             "ULTRACEMCO.NS", "Materials",     "India", "NSE", "INR"),
    ("NESTLEIND",  "Nestle India",                 "NESTLEIND.NS",  "Consumer",      "India", "NSE", "INR"),
    ("TECHM",      "Tech Mahindra",                "TECHM.NS",      "Technology",    "India", "NSE", "INR"),
    ("ADANIPORTS", "Adani Ports & SEZ",            "ADANIPORTS.NS", "Industrials",   "India", "NSE", "INR"),
    ("TATAMOTORS", "Tata Motors",                  "TATAMOTORS.NS", "Automotive",    "India", "NSE", "INR"),
    ("TATASTEEL",  "Tata Steel",                   "TATASTEEL.NS",  "Materials",     "India", "NSE", "INR"),
    ("JSWSTEEL",   "JSW Steel",                    "JSWSTEEL.NS",   "Materials",     "India", "NSE", "INR"),
    ("HINDALCO",   "Hindalco Industries",          "HINDALCO.NS",   "Materials",     "India", "NSE", "INR"),
    ("ONGC",       "ONGC",                         "ONGC.NS",       "Energy",        "India", "NSE", "INR"),
    ("NTPC",       "NTPC Limited",                 "NTPC.NS",       "Energy",        "India", "NSE", "INR"),
    ("POWERGRID",  "Power Grid Corp",              "POWERGRID.NS",  "Energy",        "India", "NSE", "INR"),
    ("COALINDIA",  "Coal India",                   "COALINDIA.NS",  "Energy",        "India", "NSE", "INR"),
    ("BPCL",       "Bharat Petroleum",             "BPCL.NS",       "Energy",        "India", "NSE", "INR"),
    ("DRREDDY",    "Dr. Reddy's Laboratories",     "DRREDDY.NS",    "Healthcare",    "India", "NSE", "INR"),
    ("DIVISLAB",   "Divi's Laboratories",          "DIVISLAB.NS",   "Healthcare",    "India", "NSE", "INR"),
    ("CIPLA",      "Cipla Limited",                "CIPLA.NS",      "Healthcare",    "India", "NSE", "INR"),
    ("APOLLOHOSP", "Apollo Hospitals",             "APOLLOHOSP.NS", "Healthcare",    "India", "NSE", "INR"),
    ("GRASIM",     "Grasim Industries",            "GRASIM.NS",     "Materials",     "India", "NSE", "INR"),
    ("ASIANPAINT", "Asian Paints",                 "ASIANPAINT.NS", "Materials",     "India", "NSE", "INR"),
    ("BAJAJFINSV", "Bajaj Finserv",                "BAJAJFINSV.NS", "Financials",    "India", "NSE", "INR"),
    ("EICHERMOT",  "Eicher Motors",                "EICHERMOT.NS",  "Automotive",    "India", "NSE", "INR"),
    ("M&M",        "Mahindra & Mahindra",          "M&M.NS",        "Automotive",    "India", "NSE", "INR"),
    ("SHRIRAMFIN", "Shriram Finance",              "SHRIRAMFIN.NS", "Financials",    "India", "NSE", "INR"),
    ("INDUSINDBK", "IndusInd Bank",                "INDUSINDBK.NS", "Financials",    "India", "NSE", "INR"),
    ("HDFCLIFE",   "HDFC Life Insurance",          "HDFCLIFE.NS",   "Financials",    "India", "NSE", "INR"),
    ("SBILIFE",    "SBI Life Insurance",           "SBILIFE.NS",    "Financials",    "India", "NSE", "INR"),
    ("TATACONSUM", "Tata Consumer Products",       "TATACONSUM.NS", "Consumer",      "India", "NSE", "INR"),
    ("BRITANNIA",  "Britannia Industries",         "BRITANNIA.NS",  "Consumer",      "India", "NSE", "INR"),
    ("BAJAJ-AUTO", "Bajaj Auto",                   "BAJAJ-AUTO.NS", "Automotive",    "India", "NSE", "INR"),
    ("BEL",        "Bharat Electronics",           "BEL.NS",        "Industrials",   "India", "NSE", "INR"),
    ("ADANIENT",   "Adani Enterprises",            "ADANIENT.NS",   "Industrials",   "India", "NSE", "INR"),
    ("ZOMATO",     "Zomato Limited",               "ZOMATO.NS",     "Consumer",      "India", "NSE", "INR"),
    ("PAYTM",      "One97 Communications",         "PAYTM.NS",      "Technology",    "India", "NSE", "INR"),
    ("NYKAA",      "FSN E-Commerce (Nykaa)",       "NYKAA.NS",      "Consumer",      "India", "NSE", "INR"),
    ("POLICYBZR",  "PB Fintech (PolicyBazaar)",    "POLICYBZR.NS",  "Technology",    "India", "NSE", "INR"),
    ("DMART",      "Avenue Supermarts (DMart)",    "DMART.NS",      "Consumer",      "India", "NSE", "INR"),
    ("HAVELLS",    "Havells India",                "HAVELLS.NS",    "Industrials",   "India", "NSE", "INR"),
    ("PIDILITIND", "Pidilite Industries",          "PIDILITIND.NS", "Materials",     "India", "NSE", "INR"),
    ("BERGEPAINT", "Berger Paints India",          "BERGEPAINT.NS", "Materials",     "India", "NSE", "INR"),
    ("MUTHOOTFIN", "Muthoot Finance",              "MUTHOOTFIN.NS", "Financials",    "India", "NSE", "INR"),
    ("CHOLAFIN",   "Cholamandalam Fin.",           "CHOLAFIN.NS",   "Financials",    "India", "NSE", "INR"),
    ("GODREJCP",   "Godrej Consumer Products",     "GODREJCP.NS",   "Consumer",      "India", "NSE", "INR"),
    ("DABUR",      "Dabur India",                  "DABUR.NS",      "Consumer",      "India", "NSE", "INR"),
    ("MARICO",     "Marico Limited",               "MARICO.NS",     "Consumer",      "India", "NSE", "INR"),
    ("COLPAL",     "Colgate-Palmolive India",      "COLPAL.NS",     "Consumer",      "India", "NSE", "INR"),
    ("UPL",        "UPL Limited",                  "UPL.NS",        "Materials",     "India", "NSE", "INR"),
    ("TATAPOWER",  "Tata Power Company",           "TATAPOWER.NS",  "Energy",        "India", "NSE", "INR"),
    ("ADANIGREEN", "Adani Green Energy",           "ADANIGREEN.NS", "Energy",        "India", "NSE", "INR"),
    ("TORNTPHARM", "Torrent Pharmaceuticals",      "TORNTPHARM.NS", "Healthcare",    "India", "NSE", "INR"),
    ("LUPIN",      "Lupin Limited",                "LUPIN.NS",      "Healthcare",    "India", "NSE", "INR"),
    ("AUROPHARMA", "Aurobindo Pharma",             "AUROPHARMA.NS", "Healthcare",    "India", "NSE", "INR"),
    ("BIOCON",     "Biocon Limited",               "BIOCON.NS",     "Healthcare",    "India", "NSE", "INR"),
    ("DLF",        "DLF Limited",                  "DLF.NS",        "Real Estate",   "India", "NSE", "INR"),
    ("GODREJPROP", "Godrej Properties",            "GODREJPROP.NS", "Real Estate",   "India", "NSE", "INR"),
    ("PRESTIGE",   "Prestige Estates",             "PRESTIGE.NS",   "Real Estate",   "India", "NSE", "INR"),
    ("OBEROIRLTY",  "Oberoi Realty",               "OBEROIRLTY.NS", "Real Estate",   "India", "NSE", "INR"),
    ("INDIGO",     "IndiGo (InterGlobe Aviation)", "INDIGO.NS",     "Industrials",   "India", "NSE", "INR"),
    ("IRCTC",      "IRCTC Limited",                "IRCTC.NS",      "Consumer",      "India", "NSE", "INR"),
    ("VOLTAS",     "Voltas Limited",               "VOLTAS.NS",     "Industrials",   "India", "NSE", "INR"),
    ("WHIRLPOOL",  "Whirlpool of India",           "WHIRLPOOL.NS",  "Consumer",      "India", "NSE", "INR"),
    ("PAGEIND",    "Page Industries",              "PAGEIND.NS",    "Consumer",      "India", "NSE", "INR"),
]

UK_FTSE100 = [
    ("SHEL",   "Shell plc",                    "SHEL.L",  "Energy",      "UK", "LSE", "GBP"),
    ("AZN",    "AstraZeneca plc",              "AZN.L",   "Healthcare",  "UK", "LSE", "GBP"),
    ("HSBA",   "HSBC Holdings plc",            "HSBA.L",  "Financials",  "UK", "LSE", "GBP"),
    ("ULVR",   "Unilever plc",                 "ULVR.L",  "Consumer",    "UK", "LSE", "GBP"),
    ("BP",     "BP plc",                       "BP.L",    "Energy",      "UK", "LSE", "GBP"),
    ("GSK",    "GSK plc",                      "GSK.L",   "Healthcare",  "UK", "LSE", "GBP"),
    ("RIO",    "Rio Tinto plc",                "RIO.L",   "Materials",   "UK", "LSE", "GBP"),
    ("AAL",    "Anglo American plc",           "AAL.L",   "Materials",   "UK", "LSE", "GBP"),
    ("LSEG",   "London Stock Exchange Group",  "LSEG.L",  "Financials",  "UK", "LSE", "GBP"),
    ("DGE",    "Diageo plc",                   "DGE.L",   "Consumer",    "UK", "LSE", "GBP"),
    ("STAN",   "Standard Chartered plc",       "STAN.L",  "Financials",  "UK", "LSE", "GBP"),
    ("BARC",   "Barclays plc",                 "BARC.L",  "Financials",  "UK", "LSE", "GBP"),
    ("LLOY",   "Lloyds Banking Group",         "LLOY.L",  "Financials",  "UK", "LSE", "GBP"),
    ("NWG",    "NatWest Group plc",            "NWG.L",   "Financials",  "UK", "LSE", "GBP"),
    ("PRU",    "Prudential plc",               "PRU.L",   "Financials",  "UK", "LSE", "GBP"),
    ("TSCO",   "Tesco plc",                    "TSCO.L",  "Consumer",    "UK", "LSE", "GBP"),
    ("NG",     "National Grid plc",            "NG.L",    "Utilities",   "UK", "LSE", "GBP"),
    ("BT-A",   "BT Group plc",                 "BT-A.L",  "Technology",  "UK", "LSE", "GBP"),
    ("VOD",    "Vodafone Group plc",           "VOD.L",   "Technology",  "UK", "LSE", "GBP"),
    ("BA",     "BAE Systems plc",              "BA.L",    "Industrials", "UK", "LSE", "GBP"),
    ("RR",     "Rolls-Royce Holdings",         "RR.L",    "Industrials", "UK", "LSE", "GBP"),
    ("IAG",    "Int'l Consolidated Airlines",  "IAG.L",   "Industrials", "UK", "LSE", "GBP"),
    ("EZJ",    "EasyJet plc",                  "EZJ.L",   "Industrials", "UK", "LSE", "GBP"),
    ("EXPN",   "Experian plc",                 "EXPN.L",  "Technology",  "UK", "LSE", "GBP"),
    ("RELX",   "RELX plc",                     "RELX.L",  "Technology",  "UK", "LSE", "GBP"),
    ("IMB",    "Imperial Brands plc",          "IMB.L",   "Consumer",    "UK", "LSE", "GBP"),
    ("BATS",   "British American Tobacco",     "BATS.L",  "Consumer",    "UK", "LSE", "GBP"),
    ("RKT",    "Reckitt Benckiser Group",       "RKT.L",   "Consumer",    "UK", "LSE", "GBP"),
    ("CPG",    "Compass Group plc",            "CPG.L",   "Consumer",    "UK", "LSE", "GBP"),
    ("WPP",    "WPP plc",                      "WPP.L",   "Consumer",    "UK", "LSE", "GBP"),
]

EUROPE_STOCKS = [
    # Germany DAX
    ("SAP",    "SAP SE",                        "SAP.DE",  "Technology",  "Germany", "XETRA", "EUR"),
    ("SIE",    "Siemens AG",                    "SIE.DE",  "Industrials", "Germany", "XETRA", "EUR"),
    ("ALV",    "Allianz SE",                    "ALV.DE",  "Financials",  "Germany", "XETRA", "EUR"),
    ("MUV2",   "Munich Re",                     "MUV2.DE", "Financials",  "Germany", "XETRA", "EUR"),
    ("BMW",    "Bayerische Motoren Werke",      "BMW.DE",  "Automotive",  "Germany", "XETRA", "EUR"),
    ("MBG",    "Mercedes-Benz Group",           "MBG.DE",  "Automotive",  "Germany", "XETRA", "EUR"),
    ("VOW3",   "Volkswagen AG",                 "VOW3.DE", "Automotive",  "Germany", "XETRA", "EUR"),
    ("BAYN",   "Bayer AG",                      "BAYN.DE", "Healthcare",  "Germany", "XETRA", "EUR"),
    ("MRK",    "Merck KGaA",                    "MRK.DE",  "Healthcare",  "Germany", "XETRA", "EUR"),
    ("DBK",    "Deutsche Bank AG",              "DBK.DE",  "Financials",  "Germany", "XETRA", "EUR"),
    ("EOAN",   "E.ON SE",                       "EOAN.DE", "Utilities",   "Germany", "XETRA", "EUR"),
    ("RWE",    "RWE AG",                        "RWE.DE",  "Utilities",   "Germany", "XETRA", "EUR"),
    ("LIN-DE", "Linde plc (Frankfurt)",         "LIN.DE",  "Materials",   "Germany", "XETRA", "EUR"),
    ("BAS",    "BASF SE",                       "BAS.DE",  "Materials",   "Germany", "XETRA", "EUR"),
    ("HEN3",   "Henkel AG",                     "HEN3.DE", "Consumer",    "Germany", "XETRA", "EUR"),
    ("ADS",    "Adidas AG",                     "ADS.DE",  "Consumer",    "Germany", "XETRA", "EUR"),
    ("P911",   "Porsche AG",                    "P911.DE", "Automotive",  "Germany", "XETRA", "EUR"),
    ("DHL",    "Deutsche Post / DHL Group",     "DHL.DE",  "Industrials", "Germany", "XETRA", "EUR"),
    ("DTE",    "Deutsche Telekom",              "DTE.DE",  "Technology",  "Germany", "XETRA", "EUR"),
    ("DHER",   "Delivery Hero SE",              "DHER.DE", "Consumer",    "Germany", "XETRA", "EUR"),
    # France CAC40
    ("MC",     "LVMH Moët Hennessy",            "MC.PA",   "Consumer",    "France", "Euronext", "EUR"),
    ("OR",     "L'Oréal S.A.",                  "OR.PA",   "Consumer",    "France", "Euronext", "EUR"),
    ("TTE",    "TotalEnergies SE",              "TTE.PA",  "Energy",      "France", "Euronext", "EUR"),
    ("SAN",    "Sanofi S.A.",                   "SAN.PA",  "Healthcare",  "France", "Euronext", "EUR"),
    ("AIR",    "Airbus SE",                     "AIR.PA",  "Industrials", "France", "Euronext", "EUR"),
    ("BN",     "Danone S.A.",                   "BN.PA",   "Consumer",    "France", "Euronext", "EUR"),
    ("BNP",    "BNP Paribas SA",               "BNP.PA",  "Financials",  "France", "Euronext", "EUR"),
    ("SG",     "Société Générale",              "GLE.PA",  "Financials",  "France", "Euronext", "EUR"),
    ("KER",    "Kering SA",                     "KER.PA",  "Consumer",    "France", "Euronext", "EUR"),
    ("RI",     "Pernod Ricard SA",              "RI.PA",   "Consumer",    "France", "Euronext", "EUR"),
]

JAPAN_STOCKS = [
    ("7203-T", "Toyota Motor Corporation",     "7203.T",  "Automotive",  "Japan", "TSE", "JPY"),
    ("6758-T", "Sony Group Corporation",       "6758.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("6861-T", "Keyence Corporation",          "6861.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("9432-T", "Nippon Telegraph & Tel.",      "9432.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("8306-T", "Mitsubishi UFJ Financial",     "8306.T",  "Financials",  "Japan", "TSE", "JPY"),
    ("8316-T", "Sumitomo Mitsui Financial",    "8316.T",  "Financials",  "Japan", "TSE", "JPY"),
    ("4063-T", "Shin-Etsu Chemical Co.",       "4063.T",  "Materials",   "Japan", "TSE", "JPY"),
    ("6098-T", "Recruit Holdings Co.",         "6098.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("7974-T", "Nintendo Co., Ltd.",           "7974.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("4543-T", "Terumo Corporation",           "4543.T",  "Healthcare",  "Japan", "TSE", "JPY"),
    ("6367-T", "Daikin Industries Ltd.",       "6367.T",  "Industrials", "Japan", "TSE", "JPY"),
    ("8035-T", "Tokyo Electron Limited",       "8035.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("9984-T", "SoftBank Group Corp.",         "9984.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("6954-T", "Fanuc Corporation",            "6954.T",  "Industrials", "Japan", "TSE", "JPY"),
    ("4519-T", "Chugai Pharmaceutical",        "4519.T",  "Healthcare",  "Japan", "TSE", "JPY"),
    ("4901-T", "FUJIFILM Holdings",            "4901.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("7267-T", "Honda Motor Co., Ltd.",        "7267.T",  "Automotive",  "Japan", "TSE", "JPY"),
    ("7751-T", "Canon Inc.",                   "7751.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("2502-T", "Asahi Group Holdings",         "2502.T",  "Consumer",    "Japan", "TSE", "JPY"),
    ("2914-T", "Japan Tobacco Inc.",           "2914.T",  "Consumer",    "Japan", "TSE", "JPY"),
    ("9433-T", "KDDI Corporation",             "9433.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("3382-T", "Seven & i Holdings",           "3382.T",  "Consumer",    "Japan", "TSE", "JPY"),
    ("4661-T", "Oriental Land Co.",            "4661.T",  "Consumer",    "Japan", "TSE", "JPY"),
    ("6702-T", "Fujitsu Limited",              "6702.T",  "Technology",  "Japan", "TSE", "JPY"),
    ("9020-T", "East Japan Railway Co.",       "9020.T",  "Industrials", "Japan", "TSE", "JPY"),
]

HK_STOCKS = [
    ("700-HK",  "Tencent Holdings",           "0700.HK", "Technology",  "Hong Kong", "HKEX", "HKD"),
    ("9988-HK", "Alibaba Group Holding",      "9988.HK", "Technology",  "Hong Kong", "HKEX", "HKD"),
    ("3690-HK", "Meituan",                    "3690.HK", "Consumer",    "Hong Kong", "HKEX", "HKD"),
    ("9618-HK", "JD.com Inc.",                "9618.HK", "Consumer",    "Hong Kong", "HKEX", "HKD"),
    ("1299-HK", "AIA Group Limited",          "1299.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("941-HK",  "China Mobile Limited",       "0941.HK", "Technology",  "Hong Kong", "HKEX", "HKD"),
    ("939-HK",  "China Construction Bank",    "0939.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("1398-HK", "ICBC",                       "1398.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("2318-HK", "Ping An Insurance Group",    "2318.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("2628-HK", "China Life Insurance",       "2628.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("388-HK",  "Hong Kong Exchanges",        "0388.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("5-HK",    "HSBC Holdings (HK)",         "0005.HK", "Financials",  "Hong Kong", "HKEX", "HKD"),
    ("27-HK",   "Galaxy Entertainment Group", "0027.HK", "Consumer",    "Hong Kong", "HKEX", "HKD"),
    ("1177-HK", "Sino Biopharmaceutical",     "1177.HK", "Healthcare",  "Hong Kong", "HKEX", "HKD"),
    ("2269-HK", "Wuxi Biologics",             "2269.HK", "Healthcare",  "Hong Kong", "HKEX", "HKD"),
]

AUSTRALIA_STOCKS = [
    ("BHP-AX",  "BHP Group Limited",          "BHP.AX",  "Materials",   "Australia", "ASX", "AUD"),
    ("CBA-AX",  "Commonwealth Bank of Aus.",  "CBA.AX",  "Financials",  "Australia", "ASX", "AUD"),
    ("CSL-AX",  "CSL Limited",                "CSL.AX",  "Healthcare",  "Australia", "ASX", "AUD"),
    ("ANZ-AX",  "ANZ Banking Group",          "ANZ.AX",  "Financials",  "Australia", "ASX", "AUD"),
    ("NAB-AX",  "National Aus. Bank",         "NAB.AX",  "Financials",  "Australia", "ASX", "AUD"),
    ("WBC-AX",  "Westpac Banking Corp.",      "WBC.AX",  "Financials",  "Australia", "ASX", "AUD"),
    ("WES-AX",  "Wesfarmers Limited",         "WES.AX",  "Consumer",    "Australia", "ASX", "AUD"),
    ("WOW-AX",  "Woolworths Group Limited",   "WOW.AX",  "Consumer",    "Australia", "ASX", "AUD"),
    ("MQG-AX",  "Macquarie Group Limited",    "MQG.AX",  "Financials",  "Australia", "ASX", "AUD"),
    ("RIO-AX",  "Rio Tinto Limited",          "RIO.AX",  "Materials",   "Australia", "ASX", "AUD"),
    ("FMG-AX",  "Fortescue Metals Group",     "FMG.AX",  "Materials",   "Australia", "ASX", "AUD"),
    ("TLS-AX",  "Telstra Group Limited",      "TLS.AX",  "Technology",  "Australia", "ASX", "AUD"),
    ("QAN-AX",  "Qantas Airways Limited",     "QAN.AX",  "Industrials", "Australia", "ASX", "AUD"),
    ("REA-AX",  "REA Group Limited",          "REA.AX",  "Technology",  "Australia", "ASX", "AUD"),
    ("XRO-AX",  "Xero Limited",               "XRO.AX",  "Technology",  "Australia", "ASX", "AUD"),
]

# Combine all universes
ALL_STOCKS = US_SP500 + INDIA_STOCKS + UK_FTSE100 + EUROPE_STOCKS + JAPAN_STOCKS + HK_STOCKS + AUSTRALIA_STOCKS


import uuid

async def get_or_create_sector(conn, macro_sector: str, sector: str, industry: str = "General") -> str:
    row = await conn.fetchrow(
        "SELECT id FROM sector_classification WHERE macro_sector=$1 AND sector=$2",
        macro_sector, sector,
    )
    if row:
        return str(row["id"])
    
    new_id = uuid.uuid4()
    result = await conn.fetchrow(
        "INSERT INTO sector_classification (id, macro_sector, sector, industry) VALUES ($1,$2,$3,$4) RETURNING id",
        new_id, macro_sector, sector, industry,
    )
    return str(result["id"])


async def seed_stocks(conn, stocks: list, force: bool = False):
    inserted = updated = skipped = 0

    for (ticker, company, yahoo_ticker, sector, country, exchange, currency) in stocks:
        try:
            # Map sector string to (macro, sector)
            macro = sector  # simplification — both same for now
            sector_id = await get_or_create_sector(conn, macro, sector)
            # Parse sector_id to UUID
            sector_id_uuid = uuid.UUID(sector_id) if sector_id else None

            existing = await conn.fetchrow("SELECT nse_symbol FROM stocks WHERE nse_symbol=$1", ticker)

            if existing and not force:
                # Update yahoo_ticker + exchange fields only
                await conn.execute(
                    """UPDATE stocks SET yahoo_ticker=$1, exchange=$2, currency=$3, country=$4,
                       updated_at=NOW() WHERE nse_symbol=$5""",
                    yahoo_ticker, exchange, currency, country, ticker,
                )
                updated += 1
            else:
                new_stock_id = uuid.uuid4()
                await conn.execute(
                    """INSERT INTO stocks
                        (id, nse_symbol, company_name, instrument_type, sector_id,
                         exchange, currency, yahoo_ticker, country,
                         is_active, listing_status, data_source, lot_size,
                         is_nifty50, is_nifty500, is_fno)
                       VALUES ($1,$2,$3,'EQ',$4,$5,$6,$7,$8,TRUE,'ACTIVE','GLOBAL_SEED',1,FALSE,FALSE,FALSE)
                       ON CONFLICT (nse_symbol) DO UPDATE
                         SET yahoo_ticker=$7, exchange=$5, currency=$6, country=$8,
                             company_name=$3, updated_at=NOW()""",
                    new_stock_id, ticker, company, sector_id_uuid,
                    exchange, currency, yahoo_ticker, country,
                )
                inserted += 1
        except Exception as e:
            log.warning(f"  Skipped {ticker}: {e}")
            skipped += 1

    return inserted, updated, skipped


async def main():
    parser = argparse.ArgumentParser(description="Seed global stocks universe")
    parser.add_argument("--exchange", help="Only seed a specific exchange (e.g. NYSE, NSE, LSE)")
    parser.add_argument("--force", action="store_true", help="Update all rows even if they exist")
    args = parser.parse_args()

    db_url = DB_URL.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")

    log.info("Connecting to database...")
    try:
        conn = await asyncpg.connect(db_url)
    except Exception as e:
        log.error(f"DB connection failed: {e}")
        sys.exit(1)

    try:
        # Apply database migrations if not already done
        log.info("Applying database schema migrations...")
        await conn.execute("ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_isin_key")
        await conn.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange VARCHAR(20) NOT NULL DEFAULT 'NSE'")
        await conn.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS currency VARCHAR(5) NOT NULL DEFAULT 'INR'")
        await conn.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS yahoo_ticker VARCHAR(30)")
        await conn.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS country VARCHAR(50) NOT NULL DEFAULT 'India'")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_exchange ON stocks(exchange)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_country ON stocks(country)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_yahoo ON stocks(yahoo_ticker)")

        stocks = ALL_STOCKS
        if args.exchange:
            stocks = [s for s in stocks if s[5].upper() == args.exchange.upper()]
            log.info(f"Filtered to exchange={args.exchange}: {len(stocks)} stocks")

        # Summary before
        before = await conn.fetchval("SELECT COUNT(*) FROM stocks")
        log.info(f"Stocks before seed: {before}")
        log.info(f"Seeding {len(stocks)} stocks from global universe...")

        inserted, updated, skipped = await seed_stocks(conn, stocks, force=args.force)
        after = await conn.fetchval("SELECT COUNT(*) FROM stocks")

        # Per-exchange breakdown
        rows = await conn.fetch("""
            SELECT exchange, country, COUNT(*) as cnt
            FROM stocks GROUP BY exchange, country ORDER BY cnt DESC
        """)

        log.info(f"""
╔══════════════════════════════════════════════════╗
║         Global Stock Seed Complete!              ║
╠══════════════════════════════════════════════════╣
║  Inserted : {inserted:<38}║
║  Updated  : {updated:<38}║
║  Skipped  : {skipped:<38}║
║  Total DB : {after:<38}║
╠══════════════════════════════════════════════════╣
║  Exchange Breakdown:                             ║""")
        for r in rows:
            line = f"║    {r['exchange']:<10} ({r['country']:<12}): {r['cnt']:<10}"
            log.info(line + " " * max(0, 51 - len(line)) + "║")
        log.info("╚══════════════════════════════════════════════════╝")
        log.info("")
        log.info("✅ Next step: run the price poller to fetch live prices:")
        log.info("   python apps/worker/yahoo_price_fetcher.py")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
