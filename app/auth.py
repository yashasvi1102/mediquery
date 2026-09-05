"""
Simple JWT auth for MediQuery RBAC.

Portfolio-level: tokens contain a role claim, validated by a shared secret.
No user database, no password hashing. The point is demonstrating
role-based data filtering, not production auth.

Roles:
  doctor     — full access, sees all patient data
  researcher — de-identified data, cohort builder, no names/raw IDs
  admin      — operational metrics only, no patient-level data
  patient    — own record only
"""

from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Shared secret — fine for a portfolio project. Production uses env vars.
SECRET_KEY = "mediquery-demo-secret-2026"
ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24

VALID_ROLES = {"doctor", "researcher", "admin", "patient"}

# Demo users — role + optional patient_id for patient role
DEMO_USERS = {
    "dr_smith":    {"role": "doctor"},
    "researcher1": {"role": "researcher"},
    "admin1":      {"role": "admin"},
    "patient_demo": {"role": "patient", "patient_id": "ba519b64-a0be-f18f-3f34-2a07ea76d959"},
}

security = HTTPBearer()


def create_token(username: str, role: str, patient_id: str | None = None) -> str:
    """Create a JWT with role and optional patient_id claims."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    if patient_id:
        payload["patient_id"] = patient_id

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Returns payload dict."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: extract user info from Bearer token."""
    payload = decode_token(credentials.credentials)
    return {
        "username": payload.get("sub"),
        "role": payload.get("role"),
        "patient_id": payload.get("patient_id"),
    }


def require_role(*allowed_roles):
    """FastAPI dependency factory: restrict endpoint to specific roles."""
    def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' cannot access this endpoint. "
                       f"Required: {', '.join(allowed_roles)}.",
            )
        return user
    return checker