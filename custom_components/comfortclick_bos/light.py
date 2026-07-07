"""Light platform for ComfortClick bOS (dimmer + on/off)."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
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
    """An RGBW light: a Color object {A,R,G,B} where A is the white channel and
    the R/G/B magnitude carries brightness (verified from HAR: proportional RGB
    scaling dims, A drives a separate white LED). Modeled as ColorMode.RGBW.

    The strip has no separate power object (the nearby "Group ON/OFF" is a blink
    command, not power), so on/off is simply "any channel lit".
    """

    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}

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

    def _channels(self) -> tuple[int, int, int, int]:
        """Raw (R, G, B, W) with W = the bOS Color 'A' (white) slot, clamped."""
        color = self._color

        def _chan(key: str) -> int:
            try:
                return max(0, min(255, int(color.get(key, 0) or 0)))
            except (TypeError, ValueError):
                return 0

        return _chan("R"), _chan("G"), _chan("B"), _chan("A")

    @property
    def is_on(self) -> bool | None:
        if not self._color:
            return None
        return any(self._channels())

    @property
    def brightness(self) -> int | None:
        level = max(self._channels())
        return level or None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        red, green, blue, white = self._channels()
        level = max(red, green, blue, white)
        if not level:
            return None
        # Report the colour at full brightness; HA scales it by `brightness`.
        return tuple(min(255, round(c * HA_MAX / level)) for c in (red, green, blue, white))

    async def async_turn_on(self, **kwargs: Any) -> None:
        red, green, blue, white = self._channels()
        level = max(red, green, blue, white)
        # Current colour scaled up to full; default to plain white if nothing set.
        if level:
            full = tuple(round(c * HA_MAX / level) for c in (red, green, blue, white))
        else:
            full = (HA_MAX, HA_MAX, HA_MAX, 0)
        if ATTR_RGBW_COLOR in kwargs:
            full = tuple(int(c) for c in kwargs[ATTR_RGBW_COLOR])
        brightness = kwargs.get(ATTR_BRIGHTNESS, level or HA_MAX)
        red, green, blue, white = (round(c * brightness / HA_MAX) for c in full)
        # Never turn "on" to fully black.
        if not any((red, green, blue, white)):
            red = green = blue = white = brightness or HA_MAX
        await self._write_color(red, green, blue, white)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write_color(0, 0, 0, 0)

    async def _write_color(self, red: int, green: int, blue: int, white: int) -> None:
        payload = {"ObjectName": "", "Error": False, "A": white, "R": red, "G": green, "B": blue}
        try:
            await self.coordinator.client.set_value(
                self._object, json.dumps(payload), value_name="Color"
            )
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS RGB light: {err}") from err
        self.coordinator.set_local(self._object, payload)
