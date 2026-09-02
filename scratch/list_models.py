import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "apps" / "api"))
load_dotenv(project_root / ".env")

from config import settings
import google.genai as genai

print("GEMINI_API_KEY:", settings.GEMINI_API_KEY)

try:
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    print("Listing available models...")
    for model in client.models.list():
        print(f"- {model.name}")
except Exception as e:
    print("List models failed with exception:")
    import traceback
    traceback.print_exc()
