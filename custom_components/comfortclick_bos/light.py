"""Light platform for ComfortClick bOS (dimmer + on/off)."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry, coordinator_for, entities_from_entry
from .api import BosError
from .const import (
    BOS_MAX,
    BOS_MIN,
    ENT_KIND,
    ENT_MAX,
    ENT_MIN,
    KIND_DIMMER,
    KIND_RGB,
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
    entities: list[BosEntity] = []
    for item in entities_from_entry(entry):
        kind = item.get(ENT_KIND)
        coordinator = coordinator_for(entry, item)
        if kind == KIND_DIMMER:
            entities.append(BosDimmerLight(coordinator, entry, item))
        elif kind == KIND_SWITCH:
            entities.append(BosSwitchLight(coordinator, entry, item))
        elif kind == KIND_RGB:
            entities.append(BosRgbLight(coordinator, entry, item))
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
        # Floor above min so a turn_on never maps to a value that reads as off.
        bos = round(self._last_brightness / HA_MAX * self._max)
        await self._set(max(bos, self._min + 1))

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


class BosRgbLight(_BosLightBase):
    """An RGB(W) light: a Color object {A,R,G,B} where A is brightness."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    @property
    def _color(self) -> dict:
        raw = self._raw
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):  # tolerate a JSON string form
            try:
                return json.loads(raw)
            except ValueError:
                return {}
        return {}

    @property
    def is_on(self) -> bool | None:
        alpha = self._color.get("A")
        return None if alpha is None else alpha > 0

    @property
    def brightness(self) -> int | None:
        alpha = self._color.get("A")
        return int(alpha) if alpha else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color = self._color
        if not color:
            return None
        return int(color.get("R", 0)), int(color.get("G", 0)), int(color.get("B", 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        color = self._color
        red, green, blue = color.get("R", 255), color.get("G", 255), color.get("B", 255)
        alpha = color.get("A") or HA_MAX
        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            alpha = kwargs[ATTR_BRIGHTNESS]
        # Avoid turning "on" to black when the stored color is all-zero.
        if not any((red, green, blue)):
            red = green = blue = 255
        await self._write_color(int(red), int(green), int(blue), max(int(alpha), 1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        color = self._color
        await self._write_color(
            int(color.get("R", 0)), int(color.get("G", 0)), int(color.get("B", 0)), 0
        )

    async def _write_color(self, red: int, green: int, blue: int, alpha: int) -> None:
        payload = {"ObjectName": "", "Error": False, "A": alpha, "R": red, "G": green, "B": blue}
        try:
            await self.coordinator.client.set_value(
                self._object, json.dumps(payload), value_name="Color"
            )
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS RGB light: {err}") from err
        self.coordinator.set_local(self._object, payload)
