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

client = genai.Client(api_key=settings.GEMINI_API_KEY)

test_models = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.0-flash-lite-001",
    "gemini-2.0-flash-001",
]

for model_name in test_models:
    print(f"Testing model: {model_name}...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents="Say 'OK' if you receive this.",
        )
        print(f"-> SUCCESS! Response: {response.text.strip()}")
        print("="*40)
    except Exception as e:
        print(f"-> FAILED: {str(e)[:150]}")
        print("="*40)
