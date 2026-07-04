"""Light platform for ComfortClick bOS (dimmer + on/off)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry
from .api import BosError
from .const import (
    BOS_MAX,
    BOS_MIN,
    CONF_ENTITIES,
    ENT_KIND,
    ENT_MAX,
    ENT_MIN,
    KIND_DIMMER,
    KIND_SWITCH,
)
from .entity import BosEntity

_LOGGER = logging.getLogger(__name__)

HA_MAX = 255


def _as_int(value: object) -> int | None:
    try:
        return int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "on")
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light entities (dimmer + on/off) from the config entry."""
    coordinator = entry.runtime_data
    entities: list[BosEntity] = []
    for item in entry.data.get(CONF_ENTITIES, []):
        if item.get(ENT_KIND) == KIND_DIMMER:
            entities.append(BosDimmerLight(coordinator, entry, item))
        elif item.get(ENT_KIND) == KIND_SWITCH:
            entities.append(BosSwitchLight(coordinator, entry, item))
    async_add_entities(entities)


class _BosLightBase(BosEntity, LightEntity):
    """Shared write path for bOS lights."""

    async def _set(self, value: object) -> None:
        try:
            await self.coordinator.client.set_value(self._object, value)
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS light: {err}") from err
        self.coordinator.set_local(self._object, value)


class BosDimmerLight(_BosLightBase):
    """A dimmable bOS light (0..max, mapped to HA brightness 0..255)."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        self._min = item.get(ENT_MIN) or BOS_MIN
        self._max = item.get(ENT_MAX) or BOS_MAX
        self._last_brightness = HA_MAX

    @property
    def is_on(self) -> bool | None:
        value = _as_int(self._raw)
        return None if value is None else value > self._min

    @property
    def brightness(self) -> int | None:
        value = _as_int(self._raw)
        if not value or value <= self._min:
            return None
        return max(1, round(value / self._max * HA_MAX))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._last_brightness = kwargs[ATTR_BRIGHTNESS]
        # Floor to 1 so a turn_on never maps to 0 (which reads as off).
        await self._set(max(round(self._last_brightness / HA_MAX * self._max), 1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(0)

    def _handle_coordinator_update(self) -> None:
        value = _as_int(self._raw)
        if value and value > self._min:
            self._last_brightness = max(1, round(value / self._max * HA_MAX))
        super()._handle_coordinator_update()


class BosSwitchLight(_BosLightBase):
    """An on/off bOS light (boolean object)."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        return _as_bool(self._raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        # The web client sends lowercase "true"/"false" (verified from HAR).
        await self._set("true")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set("false")
