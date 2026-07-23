import base64
import hashlib
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from fastapi import HTTPException, status
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.config import settings

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings().secret_key.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return cipher().decrypt(value.encode()).decode()


def password_hash(value: str) -> str:
    return pwd.hash(value)


def password_matches(value: str, hashed: str) -> bool:
    return pwd.verify(value, hashed)


def token_for(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    return jwt.encode({"sub": user_id, "exp": exp}, settings().secret_key, algorithm=ALGORITHM)


def token_subject(token: str) -> str:
    try:
        subject = jwt.decode(token, settings().secret_key, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        subject = None
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return subject
