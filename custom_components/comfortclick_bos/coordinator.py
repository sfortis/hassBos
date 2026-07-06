"""Live-state coordinator for ComfortClick bOS.

Keeps a map of object path -> current raw value. Two update sources per poll:

- GetClientData (fast): but its PropertyUpdates only cover the session's LAST-opened
  panel, so alone it misses external changes on other panels.
- One round-robin re-read: each poll we GetPanel (or GetDeviceForm) ONE target from
  the rotation and absorb its ValueUpdates. GetPanel returns current values
  regardless of subscription, so cycling through all panels reliably catches every
  external change, at a flat cost of one extra request per poll (no bursts).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BosClient, BosError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BosCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """Keeps the latest raw value for each watched object path."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BosClient,
        panel_paths: set[str],
        form_objs: set[str] | None = None,
        polling: bool = True,
    ) -> None:
        # update_interval None -> seed once (first refresh), no auto-poll; state
        # then comes from optimistic writes and manual homeassistant.update_entity.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL) if polling else None,
        )
        self.client = client
        # Round-robin targets: ("panel", path) and ("form", object).
        self._targets: list[tuple[str, str]] = [
            ("panel", p) for p in sorted(panel_paths)
        ] + [("form", f) for f in sorted(form_objs or ())]
        self._values: dict[str, object] = {}
        self._seeded = False
        self._rr = 0

    def set_local(self, object_name: str, value: object) -> None:
        """Optimistically record a value we just wrote and notify listeners."""
        self._values[object_name] = value
        self.async_set_updated_data(dict(self._values))

    async def _async_update_data(self) -> dict[str, object]:
        try:
            if not self._seeded:
                for kind, obj in self._targets:
                    await self._read(kind, obj)
                self._seeded = bool(self._values) or not self._targets
            else:
                await self._rotate_one()
            for update in await self.client.get_client_data():
                if update.get("PropertyName") in ("Value", "Color"):
                    name = update.get("DeviceName")
                    if name:
                        self._values[name] = update.get("Value")
        except BosError as err:
            # Any transport/auth/protocol error -> transient; keep last data.
            raise UpdateFailed(str(err)) from err
        return dict(self._values)

    async def _rotate_one(self) -> None:
        """Re-read the next panel/form in the rotation."""
        if not self._targets:
            return
        kind, obj = self._targets[self._rr % len(self._targets)]
        self._rr += 1
        await self._read(kind, obj)

    async def _read(self, kind: str, obj: str) -> None:
        try:
            if kind == "panel":
                data = await self.client.get_panel(obj)
                self._absorb(data.get("ThemeObject", {}) or {})
            else:
                self._absorb(await self.client.get_device_form(obj))
        except BosError as err:
            _LOGGER.debug("Re-read of %s %r failed: %s", kind, obj, err)

    def _absorb(self, panel_like: dict) -> None:
        """Copy a panel/form's ValueUpdates into the value cache."""
        for update in panel_like.get("ValueUpdates", []):
            if update.get("PropertyName") in ("Value", "Color") and update.get("DeviceName"):
                self._values[update["DeviceName"]] = update.get("Value")
