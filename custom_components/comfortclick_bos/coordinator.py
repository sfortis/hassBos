"""Live-state coordinator for ComfortClick bOS.

Polls GetClientData on an interval (the mechanism the official web client uses)
and keeps a map of object path -> current raw value (int for dimmers, bool for
on/off lights). GetClientData is incremental per session, so initial values are
seeded once from GetPanel (one call per distinct panel the lights live on).
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
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.client = client
        self._panel_paths = set(panel_paths)
        self._values: dict[str, object] = {}
        self._seeded = False

    def set_local(self, object_name: str, value: object) -> None:
        """Optimistically record a value we just wrote and notify listeners.

        The gateway echoes the change on the next poll; this updates the UI now
        instead of waiting up to SCAN_INTERVAL.
        """
        self._values[object_name] = value
        self.async_set_updated_data(dict(self._values))

    async def _async_update_data(self) -> dict[str, object]:
        try:
            if not self._seeded:
                await self._seed()
            for update in await self.client.get_client_data():
                if update.get("PropertyName") == "Value":
                    name = update.get("DeviceName")
                    if name:
                        self._values[name] = update.get("Value")
        except BosError as err:
            # Any transport/auth/protocol error -> transient; keep last data.
            raise UpdateFailed(str(err)) from err
        return dict(self._values)

    async def _seed(self) -> None:
        """Initial snapshot: read every panel the lights live on.

        Marked done only if at least one panel was read, so a transient failure
        (e.g. a dropped connection) is retried on the next poll instead of
        leaving entities stuck at unknown.
        """
        any_ok = False
        for path in self._panel_paths:
            try:
                panel = await self.client.get_panel(path)
            except BosError as err:
                _LOGGER.debug("Seed of panel %r failed: %s", path, err)
                continue
            any_ok = True
            theme_object = panel.get("ThemeObject", {}) or {}
            for update in theme_object.get("ValueUpdates", []):
                if update.get("PropertyName") == "Value" and update.get("DeviceName"):
                    self._values[update["DeviceName"]] = update.get("Value")
        if any_ok:
            self._seeded = True
