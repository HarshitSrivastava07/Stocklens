"""
Recorded-shape Yahoo Finance payloads.

These mirror the exact JSON structure the live endpoints return, including the
awkward parts the parsers have to survive: null bars on halted sessions, nulled
`meta` fields outside market hours, negative capex, and the `reportedValue.raw`
wrapper on every fundamentals observation.

They exist so the parsing logic is provably correct without a network call.
Live-source agreement is proven separately by scripts/verify_live.py.
"""
from __future__ import annotations


def chart_quote_payload(
    *,
    price: float = 2945.50,
    prev_close: float | None = 2910.25,
    currency: str = "INR",
    null_meta_ohlc: bool = False,
) -> dict:
    """A /v8/finance/chart response as used by get_quote."""
    meta = {
        "currency": currency,
        "symbol": "RELIANCE.NS",
        "exchangeName": "NSI",
        "fullExchangeName": "NSE",
        "instrumentType": "EQUITY",
        "regularMarketPrice": price,
        "previousClose": prev_close,
        "chartPreviousClose": prev_close,
        "marketState": "REGULAR",
        "fiftyTwoWeekHigh": 3217.90,
        "fiftyTwoWeekLow": 2220.30,
        "regularMarketVolume": 8_452_113,
    }
    if not null_meta_ohlc:
        meta.update(
            {
                "regularMarketOpen": 2915.00,
                "regularMarketDayHigh": 2951.80,
                "regularMarketDayLow": 2908.10,
            }
        )
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [1726790400, 1726876800],
                    "indicators": {
                        "quote": [
                            {
                                "open": [2901.0, 2915.0],
                                "high": [2930.0, 2951.8],
                                "low": [2888.5, 2908.1],
                                "close": [2910.25, price],
                                "volume": [7_100_000, 8_452_113],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def chart_history_payload() -> dict:
    """
    Five sessions of daily history. The middle session is a trading halt: every
    OHLCV field is null. A correct parser drops that row rather than charting a
    zero-price bar.
    """
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "symbol": "AAPL"},
                    "timestamp": [
                        1704412800,  # 2024-01-05
                        1704672000,  # 2024-01-08
                        1704758400,  # 2024-01-09  <- halted
                        1704844800,  # 2024-01-10
                        1704931200,  # 2024-01-11
                    ],
                    "indicators": {
                        "quote": [
                            {
                                "open": [181.99, 182.09, None, 184.35, 186.54],
                                "high": [182.76, 185.60, None, 186.40, 187.05],
                                "low": [180.17, 181.50, None, 183.92, 183.62],
                                "close": [181.18, 185.56, None, 186.19, 185.59],
                                "volume": [62_303_300, 59_144_500, None, 46_792_900, 49_128_400],
                            }
                        ],
                        "adjclose": [
                            {"adjclose": [180.32, 184.68, None, 185.31, 184.71]}
                        ],
                    },
                }
            ],
            "error": None,
        }
    }


def fundamentals_payload(prefix: str = "annual") -> dict:
    """
    A /ws/fundamentals-timeseries response covering three annual periods.

    Capex arrives negative from the source, as it really does; the parser is
    expected to store the magnitude.
    """

    def obs(as_of: str, value: float) -> dict:
        return {
            "dataId": 20001,
            "asOfDate": as_of,
            "periodType": "12M",
            "currencyCode": "USD",
            "reportedValue": {"raw": value, "fmt": str(value)},
        }

    def series(type_name: str, values: dict[str, float]) -> dict:
        return {
            "meta": {"symbol": ["AAPL"], "type": [type_name]},
            "timestamp": [1, 2, 3],
            type_name: [obs(d, v) for d, v in values.items()],
        }

    years = ["2022-09-30", "2023-09-30", "2024-09-30"]

    def by_year(*vals: float) -> dict[str, float]:
        return dict(zip(years, vals))

    return {
        "timeseries": {
            "result": [
                series(f"{prefix}TotalRevenue", by_year(394_328e6, 383_285e6, 391_035e6)),
                series(f"{prefix}EBITDA", by_year(130_541e6, 125_820e6, 134_661e6)),
                series(f"{prefix}OperatingIncome", by_year(119_437e6, 114_301e6, 123_216e6)),
                series(f"{prefix}NetIncome", by_year(99_803e6, 96_995e6, 93_736e6)),
                series(f"{prefix}PretaxIncome", by_year(119_103e6, 113_736e6, 123_485e6)),
                series(f"{prefix}TaxProvision", by_year(19_300e6, 16_741e6, 29_749e6)),
                series(f"{prefix}TotalDebt", by_year(132_480e6, 123_930e6, 119_059e6)),
                series(f"{prefix}CashAndCashEquivalents", by_year(23_646e6, 29_965e6, 29_943e6)),
                series(f"{prefix}StockholdersEquity", by_year(50_672e6, 62_146e6, 56_950e6)),
                series(f"{prefix}OperatingCashFlow", by_year(122_151e6, 110_543e6, 118_254e6)),
                # Source reports capex as a negative outflow.
                series(f"{prefix}CapitalExpenditure", by_year(-10_708e6, -10_959e6, -9_447e6)),
                series(f"{prefix}OrdinarySharesNumber", by_year(15_943e6, 15_550e6, 15_117e6)),
                series(f"{prefix}DilutedEPS", by_year(6.11, 6.13, 6.08)),
            ],
            "error": None,
        }
    }


def empty_chart_payload() -> dict:
    return {"chart": {"result": [], "error": None}}


def error_chart_payload() -> dict:
    return {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
        }
    }
