"""Sensor platform for ComfortClick bOS (read-only numeric controls)."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry
from .const import (
    CONF_ENTITIES,
    ENT_DEVICE_CLASS,
    ENT_KIND,
    ENT_STATE_CLASS,
    ENT_UNIT,
    KIND_SENSOR,
)
from .entity import BosEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up numeric sensors from the config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        BosSensor(coordinator, entry, item)
        for item in entry.data.get(CONF_ENTITIES, [])
        if item.get(ENT_KIND) == KIND_SENSOR
    )


class BosSensor(BosEntity, SensorEntity):
    """A read-only numeric bOS value."""

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        self._attr_native_unit_of_measurement = item.get(ENT_UNIT)
        device_class = item.get(ENT_DEVICE_CLASS)
        self._attr_device_class = (
            SensorDeviceClass(device_class) if device_class else None
        )
        state_class = item.get(ENT_STATE_CLASS)
        self._attr_state_class = (
            SensorStateClass(state_class) if state_class else None
        )

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
