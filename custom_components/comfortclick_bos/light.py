"""Light platform for ComfortClick bOS (optimistic / assumed state)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry
from .api import BosClient, BosError
from .const import BOS_MAX, BOS_MIN, CONF_OBJECT_NAME

_LOGGER = logging.getLogger(__name__)

HA_MAX = 255


def _ha_to_bos(brightness: int) -> int:
    """Map HA brightness (0-255) to the bOS DALI range (0-100)."""
    return round(brightness / HA_MAX * BOS_MAX)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the bOS light from a config entry."""
    client = entry.runtime_data
    async_add_entities([ComfortClickLight(client, entry)])


class ComfortClickLight(LightEntity):
    """A single bOS DALI light controlled optimistically."""

    _attr_assumed_state = True
    _attr_has_entity_name = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, client: BosClient, entry: BosConfigEntry) -> None:
        self._client = client
        self._object = entry.data[CONF_OBJECT_NAME]
        self._attr_name = (
            entry.data.get(CONF_NAME) or self._object.split("\\")[-1]
        )
        self._attr_unique_id = entry.unique_id or entry.entry_id
        # Optimistic state: no read-back, so we track locally.
        self._is_on = False
        self._brightness = HA_MAX

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally at a given brightness."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        value = _ha_to_bos(self._brightness)
        # Guard against a turn_on that maps to 0 (would read as off).
        value = max(value, 1)
        await self._write(value)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._write(BOS_MIN)
        self._is_on = False
        self.async_write_ha_state()

    async def _write(self, value: int) -> None:
        try:
            await self._client.set_value(self._object, value)
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS light: {err}") from err
