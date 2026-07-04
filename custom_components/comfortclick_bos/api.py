"""Async client for the ComfortClick bOS cloud gateway HTTP/JSON API.

Ported from the reverse-engineered PoC (bos_poc.py). The gateway rejects
non-browser clients, so every request carries the exact browser User-Agent the
official web client uses, plus an Origin header. The session (JWT `Token` cookie)
is held by the aiohttp cookie jar and reused for every call: we log in ONCE and
only re-authenticate if the session is later rejected (401/403). Live state is
read the same way the web client does, by polling GetClientData.
"""

from __future__ import annotations

import logging
import time

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

# Exact User-Agent sent by the official bOS web client (verified from HAR).
# The gateway drops requests that do not look browser-originated.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)

# Keep-alive is reused (no Connection: close): the gateway's TLS handshake is
# occasionally slow (3-8s), so reusing a connection avoids paying it every poll.
# The timeout is generous so a rare slow handshake is absorbed rather than cut
# short (which would trigger a retry storm); retry-once still covers dropped sockets.
_TIMEOUT = ClientTimeout(total=30, sock_read=20)


class BosError(Exception):
    """Base error for the bOS client."""


class BosConnectionError(BosError):
    """Raised when the gateway is unreachable or returns a transport error."""


class BosAuthError(BosError):
    """Raised when the session is missing or rejected (login needed)."""


class BosClient:
    """Talks to a single bOS project through the cloud gateway."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        # Origin is the gateway host without the project path segment.
        parts = self._base.split("/", 3)
        self._origin = "/".join(parts[:3]) if len(parts) >= 3 else self._base
        # Mirror the web client's request headers (verified from HAR), including
        # keep-alive: the gateway's TLS handshake is sometimes slow, so reusing
        # the connection (as the web client does) avoids re-handshaking each poll.
        # A dropped socket surfaces as ServerDisconnected and is handled by retry.
        self._headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": self._base + "/",
            "Origin": self._origin,
            "User-Agent": _BROWSER_UA,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": (
                '"Not)A;Brand";v="24", "Microsoft Edge WebView2";v="149", '
                '"Microsoft Edge";v="149", "Chromium";v="149"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Connection": "keep-alive",
        }

    async def login(self) -> dict:
        """Establish a session. Raises BosAuthError / BosConnectionError."""
        body = {
            "UserName": self._username,
            "Password": self._password,
            "DeviceName": "home-assistant",
            "OS": "Home Assistant",
            "PushToken": "",
            "RememberMe": False,
        }
        data = await self._request("POST", "/Login", json=body)
        if data.get("Status") != "OK":
            raise BosAuthError(f"Login failed: {data.get('Status')!r}")
        _LOGGER.debug("bOS login OK for %s", self._username)
        return data

    async def set_value(self, object_name: str, value: str | int) -> dict:
        """Write a value to an object (value is sent as a STRING, e.g. "50")."""
        body = {
            "objectName": object_name,
            "valueName": "Value",
            "value": str(value),
        }
        data = await self._authed("POST", "/SetValue", json=body)
        if not data.get("Success"):
            raise BosError(f"SetValue failed: {data}")
        return data

    async def get_client_data(self) -> list[dict]:
        """Poll the live update channel, returning this session's PropertyUpdates.

        Mirrors the web client: GET /GetClientData?_=<ms>. The gateway returns
        only what changed since this session's previous poll (possibly empty).
        Each item is {DeviceName, PropertyName:"Value", Value:<number>}.

        A 404 here means the session/backend no longer knows this client (load
        balancer routed elsewhere), so treat it as an auth failure to force a
        re-login. NOTE: 404 is only auth-like for this endpoint - GetPanel uses
        404 legitimately for container nodes.
        """
        params = {"_": str(int(time.time() * 1000))}
        data = await self._authed(
            "GET", "/GetClientData", auth_statuses=(401, 403, 404), params=params
        )
        return data.get("PropertyUpdates", []) or []

    async def get_panel(self, path: str) -> dict:
        """Read a panel (initial state snapshot and discovery)."""
        return await self._authed("POST", "/GetPanel", json={"Path": path})

    async def get_theme(self) -> dict:
        """Read the theme, whose Host.Nodes is the navigation tree of panels."""
        return await self._authed("GET", "/GetTheme")

    async def _authed(
        self,
        method: str,
        endpoint: str,
        *,
        auth_statuses: tuple[int, ...] = (401, 403),
        **kwargs,
    ) -> dict:
        """Run a request, re-logging in once if the session is rejected."""
        try:
            return await self._request(
                method, endpoint, auth_statuses=auth_statuses, **kwargs
            )
        except BosAuthError:
            _LOGGER.debug("bOS session rejected, re-logging in and retrying")
            await self.login()
            return await self._request(
                method, endpoint, auth_statuses=auth_statuses, **kwargs
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        auth_statuses: tuple[int, ...] = (401, 403),
        **kwargs,
    ) -> dict:
        """Single HTTP call, retried once on a dropped connection.

        The gateway does not keep connections alive reliably; a pooled socket it
        already closed surfaces as ServerDisconnected/timeout on the next use.
        Retrying once forces aiohttp to open a fresh connection.
        """
        kwargs.setdefault("headers", self._headers)
        kwargs.setdefault("timeout", _TIMEOUT)
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                async with self._session.request(
                    method, self._base + endpoint, **kwargs
                ) as resp:
                    if resp.status in auth_statuses:
                        raise BosAuthError(f"{endpoint} rejected: HTTP {resp.status}")
                    resp.raise_for_status()
                    return await resp.json()
            except (ClientError, TimeoutError, OSError) as err:
                # ClientError covers HTTP/protocol issues; TimeoutError is a
                # connect/total timeout (not a ClientError subclass); OSError
                # covers DNS/socket failures. All are transient connectivity.
                last_err = err
                if attempt == 1:
                    _LOGGER.debug("%s attempt 1 failed (%s); retrying", endpoint, err)
                    continue
        raise BosConnectionError(str(last_err)) from last_err
