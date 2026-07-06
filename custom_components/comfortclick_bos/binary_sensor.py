"""Binary sensor platform for ComfortClick bOS (read-only boolean controls)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry, coordinator_for, entities_from_entry
from .const import ENT_DEVICE_CLASS, ENT_KIND, KIND_BINARY
from .entity import BosEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from the config entry."""
    async_add_entities(
        BosBinarySensor(coordinator_for(entry, item), entry, item)
        for item in entities_from_entry(entry)
        if item.get(ENT_KIND) == KIND_BINARY
    )


class BosBinarySensor(BosEntity, BinarySensorEntity):
    """A read-only boolean bOS value."""

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        device_class = item.get(ENT_DEVICE_CLASS)
        self._attr_device_class = (
            BinarySensorDeviceClass(device_class) if device_class else None
        )

    @property
    def is_on(self) -> bool | None:
        value = self._raw
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "on")
        return None
