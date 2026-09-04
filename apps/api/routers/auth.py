"""
Upstox OAuth2 Authentication Router
Handles the OAuth2 authorization flow for Upstox API access.
Access tokens expire daily at midnight IST and must be refreshed via this flow.
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse

from config import settings

router = APIRouter()
log = logging.getLogger("auth")

# Token file shared with the worker
TOKEN_FILE = Path(__file__).resolve().parent.parent.parent.parent / "apps" / "worker" / "upstox_token.json"
UPSTOX_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _save_token(access_token: str, refresh_token: str = ""):
    """Save token to file so worker picks it up without restart."""
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        # Upstox tokens expire at 3:30 AM IST next day (midnight IST = 6:00 PM UTC)
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(data, indent=2))
        log.info(f"Token saved to {TOKEN_FILE}")
    except Exception as e:
        log.error(f"Failed to save token file: {e}")


@router.get("/auth/upstox")
async def upstox_auth_start():
    """
    Step 1: Redirect user to Upstox login page.
    Visit this URL in browser to start the OAuth2 flow.
    """
    if not settings.UPSTOX_API_KEY:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UPSTOX_API_KEY not set",
                "fix": "Add UPSTOX_API_KEY and UPSTOX_API_SECRET to your .env file",
                "get_keys": "https://developer.upstox.com",
            },
        )

    params = {
        "response_type": "code",
        "client_id": settings.UPSTOX_API_KEY,
        "redirect_uri": settings.UPSTOX_REDIRECT_URI,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"{UPSTOX_AUTH_URL}?{query}"
    log.info(f"Redirecting to Upstox auth: {auth_url}")
    return RedirectResponse(url=auth_url)


@router.get("/auth/upstox/callback")
async def upstox_auth_callback(code: str = Query(..., description="Authorization code from Upstox")):
    """
    Step 2: Upstox redirects here with an authorization code.
    Exchange code for access token automatically.
    """
    if not settings.UPSTOX_API_KEY or not settings.UPSTOX_API_SECRET:
        raise HTTPException(
            status_code=400,
            detail="UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                UPSTOX_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.UPSTOX_API_KEY,
                    "client_secret": settings.UPSTOX_API_SECRET,
                    "redirect_uri": settings.UPSTOX_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        if response.status_code != 200:
            log.error(f"Upstox token exchange failed: {response.status_code} — {response.text}")
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "Token exchange failed",
                    "upstox_status": response.status_code,
                    "upstox_response": response.text[:500],
                },
            )

        data = response.json()
        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")

        if not access_token:
            raise HTTPException(status_code=502, detail="No access_token in Upstox response")

        # Save token for worker
        _save_token(access_token, refresh_token)

        # Also update the .env file hint (we don't overwrite .env automatically)
        log.info("Upstox OAuth2 authorization successful!")

        return JSONResponse(content={
            "status": "success",
            "message": "Upstox authorization successful! Live data is now active.",
            "token_preview": f"{access_token[:8]}...{access_token[-4:]}",
            "expires": "Tonight at ~3:30 AM IST (auto-refreshes daily)",
            "next": "Restart the worker: python upstox_ws.py",
        })

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Auth callback error: {e}")
        raise HTTPException(status_code=500, detail=f"Auth callback failed: {str(e)}")


@router.get("/auth/upstox/status")
async def upstox_auth_status():
    """Check current Upstox token status."""
    env_token = settings.UPSTOX_ACCESS_TOKEN
    token_file_exists = TOKEN_FILE.exists()
    token_file_valid = False
    token_preview = None

    if token_file_exists:
        try:
            data = json.loads(TOKEN_FILE.read_text())
            token = data.get("access_token", "")
            expires_at = data.get("expires_at", "")
            if token and expires_at:
                expiry = datetime.fromisoformat(expires_at)
                token_file_valid = datetime.now(timezone.utc) < expiry
                if token_file_valid:
                    token_preview = f"{token[:8]}...{token[-4:]}"
        except Exception:
            pass

    if env_token:
        token_preview = f"{env_token[:8]}...{env_token[-4:]}"

    return {
        "has_env_token": bool(env_token),
        "has_token_file": token_file_exists,
        "token_file_valid": token_file_valid,
        "token_preview": token_preview,
        "authorize_url": "http://localhost:8000/api/v1/auth/upstox",
        "api_key_set": bool(settings.UPSTOX_API_KEY),
        "api_secret_set": bool(settings.UPSTOX_API_SECRET),
    }
