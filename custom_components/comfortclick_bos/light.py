"""Light platform for ComfortClick bOS.

Creates one entity per discovered light. Dimmers expose brightness (scaled to
each control's own min/max); on/off lights expose plain on/off. State is read
live from the coordinator (GetClientData polling), so external changes show up.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BosConfigEntry
from .api import BosError
from .const import (
    DOMAIN,
    KIND_DIMMER,
    LIGHT_KIND,
    LIGHT_MAX,
    LIGHT_MIN,
    LIGHT_NAME,
    LIGHT_OBJECT,
    LIGHT_PANEL,
)
from .coordinator import BosCoordinator

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
    """Set up bOS lights from the discovered, user-selected list."""
    coordinator = entry.runtime_data
    entities: list[BosBaseLight] = []
    for light in entry.data.get("lights", []):
        if light.get(LIGHT_KIND) == KIND_DIMMER:
            entities.append(BosDimmerLight(coordinator, entry, light))
        else:
            entities.append(BosSwitchLight(coordinator, entry, light))
    async_add_entities(entities)


class BosBaseLight(CoordinatorEntity[BosCoordinator], LightEntity):
    """Shared wiring for a single bOS light object."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: BosCoordinator,
        entry: BosConfigEntry,
        light: dict,
    ) -> None:
        super().__init__(coordinator)
        self._object = light[LIGHT_OBJECT]
        self._attr_name = light[LIGHT_NAME]
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}::{self._object}"
        panel = light.get(LIGHT_PANEL) or "bOS"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{base}::{panel}")},
            name=panel,
            manufacturer="ComfortClick",
            model="bOS",
        )

    @property
    def _raw(self) -> object:
        return self.coordinator.data.get(self._object)

    async def _set(self, value: object) -> None:
        try:
            await self.coordinator.client.set_value(self._object, value)
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS light: {err}") from err
        self.coordinator.set_local(self._object, value)


class BosDimmerLight(BosBaseLight):
    """A dimmable bOS light (0..max, mapped to HA brightness 0..255)."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, entry, light) -> None:
        super().__init__(coordinator, entry, light)
        self._min = light.get(LIGHT_MIN) or 0
        self._max = light.get(LIGHT_MAX) or 100
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
        bos = round(self._last_brightness / HA_MAX * self._max)
        # Floor to 1 so a turn_on never maps to 0 (which reads as off).
        await self._set(max(bos, 1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(0)

    def _handle_coordinator_update(self) -> None:
        value = _as_int(self._raw)
        if value and value > self._min:
            self._last_brightness = max(1, round(value / self._max * HA_MAX))
        super()._handle_coordinator_update()


class BosSwitchLight(BosBaseLight):
    """An on/off bOS light (boolean object)."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        return _as_bool(self._raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set("True")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set("False")
