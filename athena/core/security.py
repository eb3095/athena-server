"""Security utilities - rate limiting, IP banning, authentication."""

import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from athena.config import (
    AGENT_KEY,
    AUTH_FAIL_BAN_DURATION_SECONDS,
    AUTH_FAIL_BAN_THRESHOLD,
    AUTH_TOKEN,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
)


# Security state (in-memory)
rate_limit_store: dict[str, list[float]] = defaultdict(list)
auth_fail_store: dict[str, list[float]] = defaultdict(list)
banned_ips: dict[str, float] = {}

# FastAPI security scheme
security = HTTPBearer()


def get_client_ip(request: Request) -> str:
    """Extract client IP from request headers."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_ip_banned(ip: str) -> bool:
    """Check if an IP is currently banned."""
    if ip in banned_ips:
        ban_expiry = banned_ips[ip]
        if time.time() < ban_expiry:
            return True
        del banned_ips[ip]
    return False


def record_auth_failure(ip: str):
    """Record an authentication failure and ban if threshold exceeded."""
    now = time.time()
    window_start = now - AUTH_FAIL_BAN_DURATION_SECONDS
    auth_fail_store[ip] = [t for t in auth_fail_store[ip] if t > window_start]
    auth_fail_store[ip].append(now)
    if len(auth_fail_store[ip]) >= AUTH_FAIL_BAN_THRESHOLD:
        banned_ips[ip] = now + AUTH_FAIL_BAN_DURATION_SECONDS
        del auth_fail_store[ip]


def check_rate_limit(ip: str):
    """Check and update rate limit for an IP."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
        )
    rate_limit_store[ip].append(now)


def verify_token(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Verify Bearer token authentication."""
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    if not AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="AUTH_TOKEN not configured")

    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")

    return credentials


def verify_agent_key(request: Request):
    """Verify agent key from X-Agent-Key header."""
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    if not AGENT_KEY:
        raise HTTPException(status_code=500, detail="AGENT_KEY not configured")

    agent_key = request.headers.get("X-Agent-Key", "")
    if not secrets.compare_digest(agent_key, AGENT_KEY):
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")


def verify_token_or_agent_key(request: Request):
    """Verify either Bearer token or X-Agent-Key header."""
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    # Try agent key first
    agent_key = request.headers.get("X-Agent-Key", "")
    if agent_key and AGENT_KEY and secrets.compare_digest(agent_key, AGENT_KEY):
        return

    # Try bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if AUTH_TOKEN and secrets.compare_digest(token, AUTH_TOKEN):
            return

    record_auth_failure(ip)
    raise HTTPException(status_code=401, detail="Unauthorized")
