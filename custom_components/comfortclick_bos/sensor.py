"""Sensor platform for ComfortClick bOS (read-only numeric + enum controls)."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry, coordinator_for, entities_from_entry
from .const import (
    ENT_DEVICE_CLASS,
    ENT_DIAGNOSTIC,
    ENT_ICON,
    ENT_KIND,
    ENT_OPTIONS,
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
    """Set up numeric and enum sensors from the config entry."""
    async_add_entities(
        BosSensor(coordinator_for(entry, item), entry, item)
        for item in entities_from_entry(entry)
        if item.get(ENT_KIND) == KIND_SENSOR
    )


class BosSensor(BosEntity, SensorEntity):
    """A read-only bOS value: numeric (with a unit) or enum (index -> text)."""

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        self._options: dict[str, str] = item.get(ENT_OPTIONS) or {}
        if item.get(ENT_DIAGNOSTIC):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if item.get(ENT_ICON):
            self._attr_icon = item[ENT_ICON]

        if self._options:
            # Enum sensor: value is one of a fixed set of texts.
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(self._options.values())
            return

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
    def native_value(self) -> float | str | None:
        raw = self._raw
        if self._options:
            try:
                return self._options.get(str(int(float(raw))))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
        try:
            return float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
