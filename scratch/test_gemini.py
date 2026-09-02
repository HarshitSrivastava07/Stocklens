import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure we can import from apps/api
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "apps" / "api"))

load_dotenv(project_root / ".env")

from config import settings
import google.genai as genai

print("GEMINI_API_KEY:", settings.GEMINI_API_KEY)
print("GEMINI_MODEL_FAST:", settings.GEMINI_MODEL_FAST)

try:
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents="Hello, this is a test from StockLens API.",
    )
    print("Gemini API call success!")
    print("Response text:", response.text)
except Exception as e:
    print("Gemini API call failed with exception:")
    import traceback
    traceback.print_exc()
