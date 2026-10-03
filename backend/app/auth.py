import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import db_session
from .models import User

router = APIRouter()
log = logging.getLogger(__name__)
signer = URLSafeTimedSerializer(config.SESSION_SECRET, salt="chat-with-pdf-session")


def current_user(request: Request, db: Session = Depends(db_session)) -> User:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Sign in required")
    try:
        user_id = int(signer.loads(token, max_age=7 * 24 * 60 * 60))
    except (BadSignature, SignatureExpired, ValueError):
        raise HTTPException(401, "Session expired") from None
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


@router.get("/auth/google/start")
def google_start():
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google sign-in is not configured")
    state = secrets.token_urlsafe(32)
    params = urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
    response.set_cookie("oauth_state", state, httponly=True, secure=config.GOOGLE_REDIRECT_URI.startswith("https://"), samesite="lax", max_age=600)
    return response


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(db_session)):
    saved_state = request.cookies.get("oauth_state")
    if not saved_state or not state or not secrets.compare_digest(saved_state, state):
        raise HTTPException(400, "Invalid Google sign-in state")
    if not code:
        raise HTTPException(400, "Google sign-in was cancelled")
    try:
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": config.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        credential = token_response.json()["id_token"]
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("Google token exchange failed")
        raise HTTPException(502, "Google sign-in could not be completed") from None
    try:
        identity = id_token.verify_oauth2_token(credential, google_requests.Request(), config.GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(401, "Invalid Google credential") from None
    if not identity.get("email_verified"):
        raise HTTPException(401, "Google email is not verified")
    user = db.scalar(select(User).where(User.google_sub == identity["sub"]))
    if user is None:
        user = User(google_sub=identity["sub"], email=identity["email"], name=identity.get("name") or identity["email"], picture=identity.get("picture"))
        db.add(user)
    else:
        user.email = identity["email"]
        user.name = identity.get("name") or identity["email"]
        user.picture = identity.get("picture")
    db.commit()
    db.refresh(user)
    response = RedirectResponse(config.FRONTEND_ORIGIN)
    response.delete_cookie("oauth_state", samesite="lax")
    response.set_cookie("session", signer.dumps(str(user.id)), httponly=True, secure=config.FRONTEND_ORIGIN.startswith("https://"), samesite="lax", max_age=7 * 24 * 60 * 60)
    return response


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("session", samesite="lax")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "picture": user.picture}
