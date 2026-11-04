"""
Den on Meta Ray-Ban Display glasses (services/contracts/lens_glasses.py, /lens/glasses-link, /lens/glasses/*).

Why these matter:
  - the glasses never hold the agent's API key: a glasses token opens only the glasses routes;
  - the token rides in the URL fragment, which browsers do not send to servers, and only its hash is stored;
  - one link per agent: making a new one revokes the old, so a lost pair of glasses is one tap from locked out;
  - an expired or unknown token is refused with 401, never treated as anonymous.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.contracts import lens_glasses

pytestmark = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"), reason="needs the Firestore emulator")


def _token(url: str) -> str:
    assert url.startswith(lens_glasses.APP_URL + "#t=")
    return url.split("#t=", 1)[1]


async def test_a_new_link_revokes_the_old_and_only_the_hash_is_stored():
    from services.firestore_client import get_firestore_client

    fs, agent = get_firestore_client(), f"wearer-{uuid4().hex[:8]}"
    old = _token((await lens_glasses.create_link(fs, agent))["url"])
    assert await lens_glasses.resolve(fs, old) == agent

    new = _token((await lens_glasses.create_link(fs, agent))["url"])
    assert await lens_glasses.resolve(fs, new) == agent
    assert await lens_glasses.resolve(fs, old) is None
    stored = (await lens_glasses._col(fs).document(agent).get()).to_dict()
    assert new not in str(stored)


async def test_expired_or_unknown_tokens_are_refused():
    from routers.contracts import _glasses_agent
    from services.firestore_client import get_firestore_client

    fs, agent = get_firestore_client(), f"wearer-{uuid4().hex[:8]}"
    token = _token((await lens_glasses.create_link(fs, agent))["url"])
    assert (await _glasses_agent(token)).agent_id == agent

    await lens_glasses._col(fs).document(agent).update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    for bad in (token, "lg_nope", "an-api-key"):
        with pytest.raises(HTTPException) as e:
            await _glasses_agent(bad)
        assert e.value.status_code == 401


async def test_glasses_ask_has_no_photo_and_a_short_spoken_answer(monkeypatch):
    from routers import contracts
    from services.contracts import lens_ask

    seen = {}

    async def fake_ask(meta, svc, agent_id, utterance, jpeg=None, **kw):
        seen.update(agent_id=agent_id, jpeg=jpeg, **kw)
        return {"answer": "ok", "steps": [], "sources": [], "crew": None, "hire": None}

    monkeypatch.setattr(lens_ask, "ask", fake_ask)
    monkeypatch.setattr("services.meta_client.get_meta_client", lambda: None)
    out = await contracts.lens_glasses_ask(contracts.GlassesAsk(question="what time does the hardware store close"),
                                           SimpleNamespace(agent_id="wearer"))
    assert out["answer"] == "ok"
    assert seen["jpeg"] is None and seen["surface"] == lens_ask.GLASSES
    assert lens_ask.ANSWER_CHARS[lens_ask.GLASSES] <= 200
