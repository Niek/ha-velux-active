"""Tests for pyatmo authentication compatibility."""

from typing import Any

import pytest
from velux_active.api import VeluxActiveAuth

import client


class FakeSuccessfulResponse:
    """Minimal successful response accepted by pyatmo."""

    def __init__(self) -> None:
        self.status = 200
        self.ok = True
        self.headers = {"content-type": "application/json"}

    async def read(self) -> bytes:
        return b"{}"


@pytest.mark.parametrize("auth_class", [VeluxActiveAuth, client.VeluxAsyncAuth])
async def test_process_response_accepts_request_params(auth_class: type[Any]) -> None:
    """Accept the request context passed by pyatmo 9.9 and later."""
    auth = object.__new__(auth_class)
    response = FakeSuccessfulResponse()

    result = await auth.process_response(
        response,
        "https://api.netatmo.com/api/homesdata",
        params={"home_id": "home1"},
    )

    assert result is response
