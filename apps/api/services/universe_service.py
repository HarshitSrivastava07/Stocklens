"""
Stock universe import.

Loads the list of listed companies — symbol, name, ISIN — from the exchange's
own published file, and links each one to the data provider's ticker so the
ingestion pipeline can fetch prices and filings for it.

**This module never invents a symbol.** If the exchange file cannot be fetched
or cannot be parsed, the import fails and the stock table is left exactly as it
was. The implementation this replaced fell back to generating 2,700 companies
with names like "TECH0001 India Enterprises 1", all sharing the placeholder ISIN
``INE000000000``, and inserted them as active tradeable equities. A fabricated
universe is worse than an empty one, because nothing downstream — not the
valuation engine, not the screener, not the customer — can tell the difference.
"""
from __future__ import annotations

import csv
import io
import logging

import asyncpg
import httpx

from config import settings
from .providers.base import FetchReport

log = logging.getLogger("universe")

NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# NSE's archive rejects requests that do not look like a browser arriving from
# its own site.
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/market-data/securities-available-for-trading",
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Series codes that represent ordinary equity. Everything else — debentures,
# warrants, partly paid shares — is not a company you can value with an equity
# DCF, so it is not imported as one.
_EQUITY_SERIES = {"EQ", "BE"}


class UniverseImportError(RuntimeError):
    """The exchange list could not be fetched or parsed. Nothing was written."""


def yahoo_ticker_for(symbol: str, exchange: str = "NSE") -> str:
    """Map an exchange symbol to the data provider's ticker."""
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(exchange.upper(), "")
    return f"{symbol.strip().upper()}{suffix}"


async def fetch_nse_equity_list(*, timeout: float = 30.0) -> list[dict]:
    """
    Download and parse the official NSE equity list.

    Raises ``UniverseImportError`` on any failure. It never returns a partial or
    substitute list, because a caller cannot distinguish one from the real thing.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # Priming the session first; the archive host sets a cookie on the
            # main site and rejects cold requests to the CSV.
            try:
                await client.get("https://www.nseindia.com/", headers=_NSE_HEADERS)
            except httpx.HTTPError:
                pass
            response = await client.get(NSE_EQUITY_LIST_URL, headers=_NSE_HEADERS)
    except httpx.HTTPError as exc:
        raise UniverseImportError(
            f"Could not reach the NSE equity list: {type(exc).__name__}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise UniverseImportError(
            f"NSE equity list returned HTTP {response.status_code}"
        )

    text = response.text
    if not text.strip():
        raise UniverseImportError("NSE equity list came back empty")

    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip().upper() for f in (reader.fieldnames or [])}
    if "SYMBOL" not in fields:
        raise UniverseImportError(
            f"NSE equity list is not in the expected format; columns were {sorted(fields)}"
        )

    rows: list[dict] = []
    for raw in reader:
        record = {(k or "").strip().upper(): (v or "").strip() for k, v in raw.items()}
        symbol = record.get("SYMBOL", "")
        if not symbol:
            continue
        series = record.get("SERIES", "EQ").upper()
        if series not in _EQUITY_SERIES:
            continue
        rows.append(
            {
                "symbol": symbol.upper(),
                "name": record.get("NAME OF COMPANY") or symbol,
                "isin": record.get("ISIN NUMBER") or None,
                "series": series,
                "face_value": _to_float(record.get("FACE VALUE")),
                "listing_date": record.get("DATE OF LISTING") or None,
            }
        )

    if not rows:
        raise UniverseImportError("NSE equity list parsed but contained no equities")

    log.info("Parsed %d equities from the NSE list", len(rows))
    return rows


async def import_nse_equity_list(pool: asyncpg.Pool | None = None) -> dict:
    """
    Fetch the NSE list and upsert it into ``stocks``.

    Existing rows keep their sector, benchmark and any operator edits: only the
    name, ISIN and provider ticker are refreshed. Symbols that have left the
    exchange list are marked inactive rather than deleted, so their stored
    history and any user's watchlist entry survive.
    """
    rows = await fetch_nse_equity_list()
    report = FetchReport(requested=len(rows))

    owns_pool = pool is None
    if pool is None:
        database_url = settings.DATABASE_SYNC_URL.replace(
            "postgresql+asyncpg://", "postgresql://"
        ).replace("postgresql+psycopg2://", "postgresql://")
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for row in rows:
                    try:
                        await conn.execute(
                            """INSERT INTO stocks
                                   (nse_symbol, company_name, isin, instrument_type,
                                    face_value, exchange, currency, country,
                                    yahoo_ticker, benchmark_ticker, data_source,
                                    listing_status, is_active)
                               VALUES ($1,$2,$3,'EQ',$4,'NSE','INR','India',$5,'^NSEI',
                                       'NSE_EQUITY_LIST','ACTIVE',TRUE)
                               ON CONFLICT (nse_symbol) DO UPDATE
                                 SET company_name = EXCLUDED.company_name,
                                     isin = COALESCE(EXCLUDED.isin, stocks.isin),
                                     yahoo_ticker = COALESCE(stocks.yahoo_ticker,
                                                             EXCLUDED.yahoo_ticker),
                                     listing_status = 'ACTIVE',
                                     is_active = TRUE,
                                     updated_at = NOW()""",
                            row["symbol"],
                            row["name"],
                            row["isin"],
                            row["face_value"],
                            yahoo_ticker_for(row["symbol"], "NSE"),
                        )
                        report.record_success(1)
                    except Exception as exc:  # noqa: BLE001
                        report.record_failure(row["symbol"], str(exc))

                # Delisted names are deactivated, never deleted: their price
                # history is still real, and a user may hold them.
                present = [r["symbol"] for r in rows]
                delisted = await conn.fetchval(
                    """UPDATE stocks
                          SET is_active = FALSE, listing_status = 'DELISTED',
                              updated_at = NOW()
                        WHERE exchange = 'NSE'
                          AND data_source = 'NSE_EQUITY_LIST'
                          AND is_active = TRUE
                          AND NOT (nse_symbol = ANY($1::text[]))
                    RETURNING 1""",
                    present,
                )
                if delisted:
                    log.info("Marked symbols no longer on the NSE list as inactive")
    finally:
        if owns_pool:
            await pool.close()

    return report.finish().as_dict()


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
