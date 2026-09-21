"""Shared pytest configuration for the StockLens test suite."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The API package uses flat imports ("from services... import"), matching how
# uvicorn runs it from inside apps/api.
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
