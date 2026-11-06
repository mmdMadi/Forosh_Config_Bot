"""Tests for PanelAPI using a scripted fake HTTP client (no network)."""
import asyncio

import pytest

from panel_api import PanelAPI


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeAsyncClient:
    """Records requests, replays scripted responses."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.is_closed = False

    async def get(self, url, headers=None):
        return self._next(url, method="GET", headers=headers)

    async def post(self, url, json=None, headers=None):
        return self._next(url, method="POST", json=json, headers=headers)

    def _next(self, url, method, json=None, headers=None):
        self.requests.append({"url": url, "method": method, "json": json, "headers": headers})
        resp = self.responses.pop(0) if self.responses else FakeResponse(500, {"success": False, "msg": "no scripted response"})
        return resp


def make_api(client):
    api = PanelAPI("https://panel.example.com", "/secret_path", "admin", "pw")
    api._client = client  # inject fake transport
    return api


def test_bearer_token_skips_login():
    api = PanelAPI("https://panel.example.com", "/p", "admin", "pw", api_token="tok123")
    assert asyncio.run(api.login()) is True
    assert api.session_cookie is None


def test_login_flow_with_csrf():
    client = FakeAsyncClient([
        FakeResponse(200, {"success": True, "obj": "csrfvalue"}, headers={"set-cookie": "session=abc; Path=/"}),
        FakeResponse(200, {"success": True}, headers={"set-cookie": "session=newcookie; Path=/"}),
    ])
    api = make_api(client)
    assert asyncio.run(api.login()) is True
    assert api.csrf_token == "csrfvalue"
    assert api.session_cookie == "session=newcookie"
    # Login request carried CSRF header and credentials
    login_req = client.requests[1]
    assert login_req["method"] == "POST"
    assert login_req["url"].endswith("/login")
    assert login_req["json"]["username"] == "admin"
    assert login_req["headers"].get("X-CSRF-Token") == "csrfvalue"


def test_login_failure_returns_false():
    client = FakeAsyncClient([
        FakeResponse(200, {"success": False, "msg": "bad credentials"}),
    ])
    api = make_api(client)
    assert asyncio.run(api.login()) is False


def test_get_success_returns_json():
    client = FakeAsyncClient([FakeResponse(200, {"success": True, "obj": [1, 2]})])
    api = make_api(client)
    data = asyncio.run(api.inbounds_list())
    assert data == {"success": True, "obj": [1, 2]}
    assert client.requests[0]["method"] == "GET"
    assert client.requests[0]["url"].endswith("/panel/api/inbounds/list")


def test_get_404_retries_after_relogin():
    # First GET -> 404; login succeeds; retry GET -> OK
    client = FakeAsyncClient([
        FakeResponse(404, {"success": False}),
        FakeResponse(200, {"success": True, "obj": "csrf"}, headers={"set-cookie": "session=x; Path=/"}),
        FakeResponse(200, {"success": True}, headers={"set-cookie": "session=y; Path=/"}),
        FakeResponse(200, {"success": True, "obj": "recovered"}),
    ])
    api = make_api(client)
    data = asyncio.run(api.server_status())
    assert data == {"success": True, "obj": "recovered"}
    methods = [r["method"] for r in client.requests]
    assert methods == ["GET", "GET", "POST", "GET"]


def test_get_network_error_returns_success_false():
    class BrokenClient:
        is_closed = False

        async def get(self, url, headers=None):
            raise RuntimeError("connection refused")

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    api = make_api(BrokenClient())
    data = asyncio.run(api.server_status())
    assert data["success"] is False
    assert "connection refused" in data["msg"]


def test_headers_include_authorization_when_token_present():
    client = FakeAsyncClient([FakeResponse(200, {"success": True, "obj": {}})])
    api = PanelAPI("https://panel.example.com", "/p", "admin", "pw", api_token="tok")
    api._client = client
    asyncio.run(api.server_status())
    headers = client.requests[0]["headers"]
    assert headers["Authorization"] == "Bearer tok"
