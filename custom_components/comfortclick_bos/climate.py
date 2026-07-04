"""Climate platform for ComfortClick bOS (A/C units behind device forms)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry, entities_from_entry
from .api import BosError
from .const import (
    ENT_FAN,
    ENT_FAN_MAP,
    ENT_KIND,
    ENT_MAX,
    ENT_MIN,
    ENT_MODE,
    ENT_MODE_MAP,
    ENT_ONOFF,
    ENT_SETPOINT,
    ENT_TEMP,
    KIND_CLIMATE,
)
from .entity import BosEntity

# bOS mode text -> HA HVACMode (bOS: AUTO/HEAT/COOL/FAN/DRY).
_TEXT_HVAC: dict[str, HVACMode] = {
    "AUTO": HVACMode.HEAT_COOL,
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "FAN": HVACMode.FAN_ONLY,
    "DRY": HVACMode.DRY,
}


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "on")
    return False


def _idx(value: object) -> str | None:
    try:
        return str(int(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up A/C climate entities from the config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        BosClimate(coordinator, entry, item)
        for item in entities_from_entry(entry)
        if item.get(ENT_KIND) == KIND_CLIMATE
    )


class BosClimate(BosEntity, ClimateEntity):
    """An A/C unit: on/off + mode + fan + target/current temperature."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        self._onoff = item.get(ENT_ONOFF)
        self._setpoint = item.get(ENT_SETPOINT)
        self._mode = item.get(ENT_MODE)
        self._fan = item.get(ENT_FAN)
        self._temp = item.get(ENT_TEMP)
        self._attr_min_temp = item.get(ENT_MIN) or 16
        self._attr_max_temp = item.get(ENT_MAX) or 30
        self._attr_target_temperature_step = 0.5

        # Mode: bOS index <-> HA HVACMode.
        self._idx_to_mode: dict[str, HVACMode] = {}
        self._mode_to_idx: dict[HVACMode, str] = {}
        for idx, text in (item.get(ENT_MODE_MAP) or {}).items():
            ha_mode = _TEXT_HVAC.get(text.upper())
            if ha_mode:
                self._idx_to_mode[idx] = ha_mode
                self._mode_to_idx.setdefault(ha_mode, idx)
        modes = list(dict.fromkeys(self._idx_to_mode.values()))
        if self._onoff:
            modes = [HVACMode.OFF, *modes]
        # HA rejects an empty hvac_modes list; fall back to a sane default.
        self._attr_hvac_modes = modes or [HVACMode.HEAT_COOL]

        # Fan: bOS index <-> text.
        fan_map = item.get(ENT_FAN_MAP) or {}
        self._idx_to_fan = dict(fan_map)
        self._fan_to_idx = {text: idx for idx, text in fan_map.items()}
        self._attr_fan_modes = list(fan_map.values()) or None

        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if fan_map:
            features |= ClimateEntityFeature.FAN_MODE
        if self._onoff:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features

    @property
    def current_temperature(self) -> float | None:
        return _as_float(self.coordinator.data.get(self._temp)) if self._temp else None

    @property
    def target_temperature(self) -> float | None:
        if not self._setpoint:
            return None
        return _as_float(self.coordinator.data.get(self._setpoint))

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self._onoff and not _as_bool(self.coordinator.data.get(self._onoff)):
            return HVACMode.OFF
        idx = _idx(self.coordinator.data.get(self._mode)) if self._mode else None
        return self._idx_to_mode.get(idx) if idx is not None else None

    @property
    def fan_mode(self) -> str | None:
        if not self._fan:
            return None
        idx = _idx(self.coordinator.data.get(self._fan))
        return self._idx_to_fan.get(idx) if idx is not None else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None and self._setpoint:
            await self._write(self._setpoint, str(temp), float(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            if self._onoff:
                await self._write(self._onoff, "false", False)
            return
        if self._onoff:
            await self._write(self._onoff, "true", True)
        idx = self._mode_to_idx.get(hvac_mode)
        if idx is not None and self._mode:
            await self._write(self._mode, idx, int(idx))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        idx = self._fan_to_idx.get(fan_mode)
        if idx is not None and self._fan:
            await self._write(self._fan, idx, int(idx))

    async def async_turn_on(self) -> None:
        if self._onoff:
            await self._write(self._onoff, "true", True)

    async def async_turn_off(self) -> None:
        if self._onoff:
            await self._write(self._onoff, "false", False)

    async def _write(self, obj: str, send: str, local: object) -> None:
        try:
            await self.coordinator.client.set_value(obj, send)
        except BosError as err:
            raise HomeAssistantError(f"Failed to control bOS A/C: {err}") from err
        self.coordinator.set_local(obj, local)
