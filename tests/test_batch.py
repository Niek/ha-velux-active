"""Async checks for the batch sender. Run: python3 tests/test_batch.py"""

import asyncio
import sys
import types
from pathlib import Path

# Bootstrap a stub package so batch.py's relative imports resolve without HA.
_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
_pkg = types.ModuleType("velux_active")
_pkg.__path__ = [str(_PKG)]
sys.modules.setdefault("velux_active", _pkg)

from velux_active import batch  # noqa: E402

KEY = "AAAAAAAAAAAAAAAAAAAAAA=="


class _Resp:
    def __init__(self, status=200, text='{"body":{}}'):
        self.status = status
        self.ok = 200 <= status < 300
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return self._text


class _Session:
    def __init__(self, resp=None):
        self.payloads = []
        self._resp = resp or _Resp()

    def post(self, url, json=None, headers=None):
        self.payloads.append(json)
        return self._resp


def _mgr(session, token_getter):
    m = batch.BatchCommandManager()
    m.setup(
        home_id="home1",
        bridge_id="bridge1",
        session=session,
        access_token_getter=token_getter,
        hash_sign_key=KEY,
        sign_key_id="kid",
        timezone="UTC",
    )
    return m


async def _token():
    return "tok"


async def test_command_queued_during_inflight_send_is_not_stranded():
    session = _Session()
    gate = asyncio.Event()

    async def slow_token():
        await gate.wait()  # hold the first send mid-flight
        return "tok"

    m = _mgr(session, slow_token)
    f1 = m.queue("m1", 100)
    await asyncio.sleep(0.16)   # first _send_batch is now blocked on slow_token
    f2 = m.queue("m2", 50)      # queued while the send is in flight
    gate.set()
    await asyncio.wait_for(asyncio.gather(f1, f2), timeout=2)  # both must resolve
    assert len(session.payloads) == 2


async def test_nonces_never_collide_across_two_sends_in_same_second():
    session = _Session()
    m = _mgr(session, _token)
    await asyncio.wait_for(m.queue("m1", 100), timeout=2)
    await asyncio.wait_for(m.queue("m2", 50), timeout=2)
    pairs = [
        (mod["timestamp"], mod["nonce"])
        for payload in session.payloads
        for mod in payload["home"]["modules"]
    ]
    assert len(set(pairs)) == len(pairs), pairs


async def test_body_errors_propagate_as_batch_error():
    session = _Session(_Resp(text='{"body":{"errors":[{"code":28}]}}'))
    m = _mgr(session, _token)
    try:
        await asyncio.wait_for(m.queue("m1", 100), timeout=2)
    except batch.BatchCommandError:
        return
    raise AssertionError("expected BatchCommandError")


async def test_invalid_signing_key_resolves_future_instead_of_hanging():
    # An un-decodable Base64 key raises during payload build; the queued future
    # must still resolve (as BatchCommandError), not hang the service call.
    m = batch.BatchCommandManager()
    m.setup(
        home_id="h",
        bridge_id="b",
        session=_Session(),
        access_token_getter=_token,
        hash_sign_key="!!!not-base64!!!",
        sign_key_id="kid",
        timezone="UTC",
    )
    try:
        await asyncio.wait_for(m.queue("m1", 100), timeout=2)
    except batch.BatchCommandError:
        return
    raise AssertionError("expected BatchCommandError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            asyncio.run(fn())
            print(f"ok {name}")
    print("all passed")
