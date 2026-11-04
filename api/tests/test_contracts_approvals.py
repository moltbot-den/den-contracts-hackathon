"""
Signed approvals on contracts (hackathon D6, approval/1.0).

What these pin down, and why:
  - once the owner has a device, an unsigned approve is refused, so an agent
    holding only the API key cannot approve its own spend;
  - a signature covers one event_seq and one payload: a stale or altered one
    fails, because the owner must have seen the state they approved;
  - mainnet spend needs a signature, so a device must exist before real money moves;
  - the accepted signature is written into the hash-chained event log;
  - after the first device, only the human session can add or remove devices.

Endpoint functions are called directly, against the Firestore emulator.
"""

import os
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, decode_dss_signature
from fastapi import HTTPException

from services.approvals import approval_digest, b64u_encode

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (set FIRESTORE_EMULATOR_HOST or run scripts/test-api.sh)",
)

AGENT_REQ = SimpleNamespace(headers={"x-api-key": "k"})
HUMAN_REQ = SimpleNamespace(headers={"authorization": "Bearer t"})


@pytest.fixture
def owner():
    return SimpleNamespace(agent_id=f"ap-owner-{time.time_ns()}")


def _keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, b64u_encode(raw)


async def _register(owner, key_b64, device_id="phone-0001", request=AGENT_REQ):
    from routers.approver_devices import DeviceRegister, register_device

    body = DeviceRegister(device_id=device_id, public_key={"format": "sec1", "value": key_b64})
    return await register_device(body, request, owner)


async def _waiting_contract(owner, budget=2_000):
    from routers.contracts import ContractCreate, create_contract
    from services.contracts.store import get_contract_store
    from services.firestore_client import get_firestore_client

    db = get_firestore_client()
    await db.db.collection(db._collection("agents")).document(owner.agent_id).set(
        {"agent_id": owner.agent_id, "status": "active", "credits_balance": 1_000_000}, merge=True)
    c = await create_contract(ContractCreate(goal="Fix the pump", budget_cents=budget, surface="lens", require_approval=True), owner)
    store = get_contract_store()
    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "paused",
                           lambda d: {"pause_reason": "awaiting_plan_approval", "resume_state": "hiring"})
    return c["id"]


def _sign(key, req, decision="approve"):
    digest = approval_digest(req["request_id"], req["payload_digest"], decision, req["expires_at"])
    r, s = decode_dss_signature(key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256()))))
    return {"version": "approval/1.0", "request_id": req["request_id"], "payload_digest": req["payload_digest"],
            "decision": decision, "expires_at": req["expires_at"], "device_id": "phone-0001", "form": "p256",
            "signature": b64u_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}


async def test_device_lifecycle_and_who_may_change_it(owner):
    from routers.approver_devices import delete_device, list_devices

    key, pub = _keypair()
    d = await _register(owner, pub)
    assert d["kind"] == "p256" and d["attestation_verified"] is False
    assert [x["device_id"] for x in (await list_devices(owner))["devices"]] == ["phone-0001"]

    _, pub2 = _keypair()
    with pytest.raises(HTTPException) as e:  # the agent key cannot enroll a second key
        await _register(owner, pub2, "phone-0002")
    assert e.value.status_code == 403
    await _register(owner, pub2, "phone-0002", HUMAN_REQ)
    with pytest.raises(HTTPException) as e:
        await delete_device("phone-0002", AGENT_REQ, owner)
    assert e.value.status_code == 403
    await delete_device("phone-0002", HUMAN_REQ, owner)
    assert [x["device_id"] for x in (await list_devices(owner))["devices"]] == ["phone-0001"]


async def test_registration_rejects_a_bad_app_attest_and_stores_a_webauthn_aaguid(owner):
    from routers.approver_devices import DeviceRegister, register_device
    from tests.test_attestation import DEVICE, ORIGIN, RP, _sec1, webauthn_object

    key, pub = _keypair()
    bad = DeviceRegister(device_id="phone-0001", public_key={"format": "sec1", "value": pub},
                         attestation={"type": "app_attest", "data": "AAAA", "key_id": "AAAA"})
    with pytest.raises(HTTPException) as e:
        await register_device(bad, AGENT_REQ, owner)
    assert e.value.status_code == 422

    pk = ec.generate_private_key(ec.SECP256R1())
    obj, client = webauthn_object("none", {}, pk)
    body = DeviceRegister(device_id=DEVICE, public_key={"format": "sec1", "value": b64u_encode(_sec1(pk))},
                          attestation={"type": "webauthn", "data": b64u_encode(obj), "client_data_json": b64u_encode(client)},
                          rp_id=RP, origin=ORIGIN)
    d = await register_device(body, AGENT_REQ, owner)
    assert d["kind"] == "webauthn" and d["aaguid"] == "11" * 16 and d["attestation_verified"] is False


async def test_register_rejects_a_key_that_is_not_p256(owner):
    with pytest.raises(HTTPException) as e:
        await _register(owner, b64u_encode(b"\x04" + b"\x01" * 64))
    assert e.value.status_code == 422


async def test_without_a_device_approve_still_works_on_testnet(owner):
    from routers.contracts import approve_contract

    cid = await _waiting_contract(owner)
    assert (await approve_contract(cid, owner))["state"] == "hiring"


async def test_with_a_device_an_unsigned_approve_is_refused(owner):
    from routers.contracts import approve_contract

    await _register(owner, _keypair()[1])
    cid = await _waiting_contract(owner)
    with pytest.raises(HTTPException) as e:
        await approve_contract(cid, owner)
    assert e.value.status_code == 403


async def test_signed_approve_succeeds_and_the_signature_is_in_the_chain(owner):
    from routers.contracts import ApproveBody, approve_contract, audit_contract, get_approval_request
    from services.contracts.store import get_contract_store

    key, pub = _keypair()
    await _register(owner, pub)
    cid = await _waiting_contract(owner)
    req = await get_approval_request(cid, owner)
    assert req["signature_required"] is True
    out = await approve_contract(cid, owner, ApproveBody(approval=_sign(key, req)))
    assert out["state"] == "hiring"
    event = (await get_contract_store().events(cid))[-1]
    assert event["payload"]["approval"]["device_id"] == "phone-0001" and event["payload"]["approval"]["signature"]
    assert (await audit_contract(cid, owner))["valid"] is True


async def test_signature_from_another_key_or_for_deny_is_refused(owner):
    from routers.contracts import ApproveBody, approve_contract, get_approval_request

    key, pub = _keypair()
    await _register(owner, pub)
    cid = await _waiting_contract(owner)
    req = await get_approval_request(cid, owner)
    for bad in (_sign(_keypair()[0], req), _sign(key, req, "deny")):
        with pytest.raises(HTTPException) as e:
            await approve_contract(cid, owner, ApproveBody(approval=bad))
        assert e.value.status_code == 403
    assert (await approve_contract(cid, owner, ApproveBody(approval=_sign(key, req))))["state"] == "hiring"


async def test_stale_signature_is_refused_after_the_contract_moves(owner):
    from routers.contracts import ApproveBody, approve_contract, get_approval_request
    from services.contracts.store import get_contract_store

    key, pub = _keypair()
    await _register(owner, pub)
    cid = await _waiting_contract(owner)
    signed = _sign(key, await get_approval_request(cid, owner))
    await get_contract_store().append_event(cid, "progress", payload={"note": "new plan detail"})
    with pytest.raises(HTTPException) as e:
        await approve_contract(cid, owner, ApproveBody(approval=signed))
    assert e.value.status_code == 403 and "request_mismatch" in e.value.detail


async def test_mainnet_spend_needs_a_signature_even_without_a_device(owner, monkeypatch):
    from config import get_settings
    from routers.contracts import approve_contract

    monkeypatch.setattr(get_settings(), "contracts_chain_id", 8453)
    cid = await _waiting_contract(owner)
    with pytest.raises(HTTPException) as e:
        await approve_contract(cid, owner)
    assert e.value.status_code == 403 and "mainnet_spend" in e.value.detail


async def test_over_budget_replan_is_flagged(owner):
    from routers.contracts import approval_flags

    assert approval_flags({"budget_cents": 1000, "committed_cents": 1500}) == ["over_budget_replan"]
    assert approval_flags({"budget_cents": 1000, "committed_cents": 900}) == []
