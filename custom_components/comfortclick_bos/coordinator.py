"""Live-state coordinator for ComfortClick bOS.

Keeps a map of object path -> current raw value for the panels it owns.

A GetPanel(floor path) returns the current value of EVERY object on that floor
(lights, A/C members, air-quality CO2/VOC, ventilation) and subscribes the
session's GetClientData channel to that panel. So the natural unit is one
coordinator (with its own session) per floor panel:

- Seed: GetPanel(path) once -> all current values + subscription.
- Live: GetClientData each poll -> incremental PropertyUpdates for that floor.

GetClientData only covers the session's last-opened panel, so a coordinator that
owns MORE than one panel (the shared fallback for panels beyond MAX_LIVE_SESSIONS)
round-robins one GetPanel per poll to rotate the subscription across them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BosClient, BosError
from .const import DOMAIN, MAX_TOLERATED_FAILURES, RESYNC_EVERY, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BosCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """Keeps the latest raw value for each object on the panels it owns."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BosClient,
        panel_paths: list[str],
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
        self._panels = sorted(panel_paths)
        self._values: dict[str, object] = {}
        self._seeded = False
        self._rr = 0
        self._polls = 0
        self._seen_login_gen = 0
        self._fail_streak = 0

    def set_local(self, object_name: str, value: object) -> None:
        """Optimistically record a value we just wrote and notify listeners."""
        self._values[object_name] = value
        self.async_set_updated_data(dict(self._values))

    async def _async_update_data(self) -> dict[str, object]:
        try:
            if not self._seeded:
                for path in self._panels:
                    await self._read_panel(path)
                self._seeded = bool(self._values) or not self._panels
                self._seen_login_gen = self.client.login_generation
            else:
                await self._refresh_panels()
            for update in await self.client.get_client_data():
                if update.get("PropertyName") in ("Value", "Color"):
                    name = update.get("DeviceName")
                    if name:
                        self._values[name] = update.get("Value")
            # A re-login (e.g. GetClientData 404 -> re-auth) leaves the fresh session
            # with no open panel, so its GetClientData stays silent. Re-read our
            # panels at once to re-subscribe AND capture any change made meanwhile,
            # instead of waiting up to RESYNC_EVERY for the periodic resync.
            gen = self.client.login_generation
            if gen != self._seen_login_gen:
                self._seen_login_gen = gen
                for path in self._panels:
                    await self._read_panel(path)
        except BosError as err:
            # The gateway blips often (LB 404 / dropped socket). Tolerate a short
            # streak of failures, keeping the last known values so entities do not
            # flap to "unavailable"; only surface a sustained outage. During the
            # initial seed (not yet seeded) we still fail fast -> ConfigEntryNotReady.
            self._fail_streak += 1
            if self._seeded and self._fail_streak <= MAX_TOLERATED_FAILURES:
                _LOGGER.debug(
                    "Transient poll failure %d/%d, keeping last data: %s",
                    self._fail_streak,
                    MAX_TOLERATED_FAILURES,
                    err,
                )
                return dict(self._values)
            raise UpdateFailed(str(err)) from err
        self._fail_streak = 0
        return dict(self._values)

    async def _refresh_panels(self) -> None:
        """Keep the subscription/state fresh between GetClientData polls."""
        if not self._panels:
            return
        self._polls += 1
        if len(self._panels) == 1:
            # Live via GetClientData alone; re-read occasionally to re-subscribe
            # after a reconnect and reconcile any missed push.
            if self._polls % RESYNC_EVERY == 0:
                await self._read_panel(self._panels[0])
            return
        # Shared session across several panels: round-robin one GetPanel per poll,
        # which also moves the GetClientData subscription onto that panel.
        path = self._panels[self._rr % len(self._panels)]
        self._rr += 1
        await self._read_panel(path)

    async def _read_panel(self, path: str) -> None:
        try:
            data = await self.client.get_panel(path)
            self._absorb(data.get("ThemeObject", {}) or {})
        except BosError as err:
            _LOGGER.debug("Panel re-read %r failed: %s", path, err)

    def _absorb(self, panel_like: dict) -> None:
        """Copy a panel's ValueUpdates into the value cache."""
        for update in panel_like.get("ValueUpdates", []):
            if update.get("PropertyName") in ("Value", "Color") and update.get("DeviceName"):
                self._values[update["DeviceName"]] = update.get("Value")
