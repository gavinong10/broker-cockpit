import hmac
from fastapi import Header, HTTPException
from app.config import settings

def require_internal(x_internal_token: str = Header(default="")):
    if not hmac.compare_digest(x_internal_token, settings.internal_api_token):
        raise HTTPException(status_code=401)
