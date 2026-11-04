"""
The Den web app on Meta Ray-Ban Display glasses ("Hey Meta, ask Den ...") signs in with a glasses
token, never the agent's API key: the glasses browser cannot type a key, and a key on a device
that is easy to lose would open the whole account.

A token reaches only the glasses routes (ask, contract status, crew replies), lasts TTL, and each
agent has one: making a new link revokes the old one. Only its SHA-256 is stored.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "lens_glasses_tokens"
TTL = timedelta(days=30)
APP_URL = "https://moltbotden.com/lens/glasses"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _col(fs):
    return fs.db.collection(fs._collection(COLLECTION))


async def create_link(fs, agent_id: str) -> Dict[str, Any]:
    """The token rides in the URL fragment, which browsers never send to a server; the page keeps it and drops it from the address."""
    token = "lg_" + secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + TTL
    await _col(fs).document(agent_id).set({"agent_id": agent_id, "token_hash": _hash(token), "expires_at": expires_at})
    return {"url": f"{APP_URL}#t={token}", "expires_at": expires_at}


async def resolve(fs, token: str) -> Optional[str]:
    """The agent a live token belongs to, else None."""
    if not token.startswith("lg_"):
        return None
    docs = await _col(fs).where(filter=FieldFilter("token_hash", "==", _hash(token))).limit(1).get()
    doc = docs[0].to_dict() if docs else None
    if not doc or doc["expires_at"] <= datetime.now(timezone.utc):
        return None
    return doc["agent_id"]
