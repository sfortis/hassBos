"""Live-state coordinator for ComfortClick bOS.

Polls GetClientData on a short interval (the mechanism the web client uses) and
keeps a map of object path -> current raw value. GetClientData is incremental
per session, so initial values are seeded from GetPanel.

Air-quality readings (CO2/PM/VOC) live behind device forms, not panels. They are
seeded and then refreshed on a much slower cadence via GetDeviceForm, because
they change slowly and do not need the 2s live channel.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BosClient, BosError
from .const import DOMAIN, FORM_SCAN_INTERVAL, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BosCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """Keeps the latest raw value for each watched object path."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BosClient,
        panel_paths: set[str],
        form_objs: set[str] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.client = client
        self._panel_paths = set(panel_paths)
        self._form_objs = set(form_objs or ())
        self._values: dict[str, object] = {}
        self._seeded = False
        # Refresh forms every Nth poll (slow cadence relative to the live channel).
        self._form_every = max(1, FORM_SCAN_INTERVAL // SCAN_INTERVAL)
        self._poll_count = 0

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
            else:
                self._poll_count += 1
                if self._form_objs and self._poll_count % self._form_every == 0:
                    await self._refresh_forms()
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
        """Initial snapshot from panels + air-quality forms.

        Marked done only if at least one read succeeded, so a transient failure
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
            self._absorb(panel.get("ThemeObject", {}) or {})
        any_ok = await self._refresh_forms() or any_ok
        if any_ok:
            self._seeded = True

    async def _refresh_forms(self) -> bool:
        """Fetch air-quality device forms into the cache. Returns True if any read."""
        any_ok = False
        for form_obj in self._form_objs:
            try:
                form = await self.client.get_device_form(form_obj)
            except BosError as err:
                _LOGGER.debug("Form %r refresh failed: %s", form_obj, err)
                continue
            any_ok = True
            self._absorb(form)
        return any_ok

    def _absorb(self, panel_like: dict) -> None:
        """Copy a panel/form's ValueUpdates into the value cache."""
        for update in panel_like.get("ValueUpdates", []):
            if update.get("PropertyName") == "Value" and update.get("DeviceName"):
                self._values[update["DeviceName"]] = update.get("Value")
