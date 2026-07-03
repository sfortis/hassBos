"""Live-state coordinator for ComfortClick bOS.

Polls GetClientData on an interval (the same mechanism the official web client
uses) and keeps a map of object path -> current bOS value. GetClientData is
incremental per session, so we seed the initial value once from GetPanel (when a
panel path is configured) and then merge every PropertyUpdate as it arrives.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BosClient, BosError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _to_int(value: object) -> int:
    """Coerce a bOS Value (number from reads, string from echoes) to int."""
    try:
        return int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class BosCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Keeps the latest bOS value for each watched object path."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BosClient,
        panel: str | None,
        objects: list[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.client = client
        self._panel = panel
        self._objects = set(objects)
        self._values: dict[str, int] = {}
        self._seeded = False

    def set_local(self, object_name: str, value: int) -> None:
        """Optimistically record a value we just wrote and notify listeners.

        The gateway echoes the change on the next GetClientData poll, but this
        makes the UI react immediately instead of waiting up to SCAN_INTERVAL.
        """
        self._values[object_name] = value
        self.async_set_updated_data(dict(self._values))

    async def _async_update_data(self) -> dict[str, int]:
        try:
            if not self._seeded:
                await self._seed_from_panel()
            for update in await self.client.get_client_data():
                if update.get("PropertyName") == "Value":
                    name = update.get("DeviceName")
                    if name:
                        self._values[name] = _to_int(update.get("Value"))
        except BosError as err:
            # Any transport/auth/protocol error -> transient failure; the
            # coordinator keeps the last data and retries on the next interval.
            raise UpdateFailed(str(err)) from err
        return dict(self._values)

    async def _seed_from_panel(self) -> None:
        """One-time initial snapshot so we know state before the first change."""
        self._seeded = True  # do not retry forever; live updates fill gaps
        if not self._panel:
            return
        try:
            panel = await self.client.get_panel(self._panel)
        except BosError as err:
            _LOGGER.debug("Initial panel snapshot failed (%s); relying on live", err)
            return
        for update in panel.get("ThemeObject", {}).get("ValueUpdates", []):
            if (
                update.get("PropertyName") == "Value"
                and update.get("DeviceName") in self._objects
            ):
                self._values[update["DeviceName"]] = _to_int(update.get("Value"))
