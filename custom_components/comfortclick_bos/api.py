"""Async client for the ComfortClick bOS cloud gateway HTTP/JSON API.

Ported from the reverse-engineered PoC (bos_poc.py). The gateway rejects
non-browser clients, so every request carries a browser User-Agent + Origin.
The session (JWT `Token` cookie) is held by the aiohttp cookie jar; on an auth
failure the client re-logs in once and retries, because cloud sessions expire.
"""

from __future__ import annotations

import logging

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)

# Mirror the web client. The gateway drops connections whose requests do not
# look browser-originated (non-browser UA / missing Origin).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)

_TIMEOUT = 15


class BosError(Exception):
    """Base error for the bOS client."""


class BosConnectionError(BosError):
    """Raised when the gateway is unreachable or returns a transport error."""


class BosAuthError(BosError):
    """Raised when credentials are rejected by /Login."""


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
        origin = self._base.split("/", 3)
        self._origin = "/".join(origin[:3]) if len(origin) >= 3 else self._base
        self._headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": self._base + "/",
            "Origin": self._origin,
            "User-Agent": _BROWSER_UA,
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
        try:
            async with self._session.post(
                self._base + "/Login",
                json=body,
                headers=self._headers,
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientError as err:
            raise BosConnectionError(str(err)) from err
        if data.get("Status") != "OK":
            raise BosAuthError(f"Login failed: {data.get('Status')!r}")
        _LOGGER.debug("bOS login OK for %s", self._username)
        return data

    async def set_value(self, object_name: str, value: str | int) -> dict:
        """Write a value to an object, re-logging in once on auth failure."""
        try:
            return await self._set_value(object_name, value)
        except BosAuthError:
            _LOGGER.debug("bOS session rejected, re-logging in and retrying")
            await self.login()
            return await self._set_value(object_name, value)

    async def _set_value(self, object_name: str, value: str | int) -> dict:
        # value is sent as a STRING, exactly as the web client does (e.g. "50").
        body = {
            "objectName": object_name,
            "valueName": "Value",
            "value": str(value),
        }
        try:
            async with self._session.post(
                self._base + "/SetValue",
                json=body,
                headers=self._headers,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise BosAuthError(f"SetValue rejected: HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json()
        except ClientError as err:
            raise BosConnectionError(str(err)) from err
        if not data.get("Success"):
            raise BosError(f"SetValue failed: {data}")
        return data
