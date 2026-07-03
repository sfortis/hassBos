"""Light platform for ComfortClick bOS.

State is read live from the coordinator (GetClientData polling), so changes made
outside Home Assistant (physical switch, bOS panel, KNX) are reflected here.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BosConfigEntry
from .api import BosError
from .const import BOS_MAX, BOS_MIN, CONF_OBJECT_NAME
from .coordinator import BosCoordinator

_LOGGER = logging.getLogger(__name__)

HA_MAX = 255


def _ha_to_bos(brightness: int) -> int:
    """Map HA brightness (0-255) to the bOS DALI range (0-100)."""
    return round(brightness / HA_MAX * BOS_MAX)


def _bos_to_ha(value: int) -> int:
    """Map a bOS DALI value (1-100) to HA brightness (1-255)."""
    return max(1, round(value / BOS_MAX * HA_MAX))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the bOS light from a config entry."""
    async_add_entities([ComfortClickLight(entry.runtime_data, entry)])


class ComfortClickLight(CoordinatorEntity[BosCoordinator], LightEntity):
    """A single bOS DALI light with live state and brightness."""

    _attr_has_entity_name = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: BosCoordinator, entry: BosConfigEntry) -> None:
        super().__init__(coordinator)
        self._object = entry.data[CONF_OBJECT_NAME]
        self._attr_name = entry.data.get(CONF_NAME) or self._object.split("\\")[-1]
        self._attr_unique_id = entry.unique_id or entry.entry_id
        # Remembered HA brightness for a turn_on that carries no brightness.
        self._last_brightness = HA_MAX

    @property
    def _bos_value(self) -> int | None:
        """Current bOS value, or None until we have learned it."""
        return self.coordinator.data.get(self._object)

    @property
    def available(self) -> bool:
        # Tied to coordinator health only. Before we have learned a value the
        # entity is still available; is_on/brightness report None (unknown)
        # until the first update or command arrives.
        return super().available

    @property
    def is_on(self) -> bool | None:
        value = self._bos_value
        return None if value is None else value > BOS_MIN

    @property
    def brightness(self) -> int | None:
        value = self._bos_value
        if not value:
            return None
        return _bos_to_ha(value)

    @callback
    def _handle_coordinator_update(self) -> None:
        # Keep the restore-brightness in sync with the real value when lit.
        value = self._bos_value
        if value:
            self._last_brightness = _bos_to_ha(value)
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally at a given brightness."""
        if ATTR_BRIGHTNESS in kwargs:
            self._last_brightness = kwargs[ATTR_BRIGHTNESS]
        # Floor to 1 so a turn_on never maps to 0 (which reads as off).
        value = max(_ha_to_bos(self._last_brightness), 1)
        await self._write(value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._write(BOS_MIN)

    async def _write(self, value: int) -> None:
        try:
            await self.coordinator.client.set_value(self._object, value)
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS light: {err}") from err
        # Reflect immediately; the next poll will confirm from the gateway.
        self.coordinator.set_local(self._object, value)
