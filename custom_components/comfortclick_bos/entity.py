"""Shared base entity for ComfortClick bOS platforms."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENT_NAME, ENT_OBJECT, ENT_PANEL
from .coordinator import BosCoordinator


class BosEntity(CoordinatorEntity[BosCoordinator]):
    """Common wiring: unique id, device (per floor/panel), raw value access."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: BosCoordinator, entry, item: dict) -> None:
        super().__init__(coordinator)
        self._object = item[ENT_OBJECT]
        self._attr_name = item[ENT_NAME]
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}::{self._object}"
        panel = item.get(ENT_PANEL) or "bOS"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{base}::{panel}")},
            name=panel,
            manufacturer="ComfortClick",
            model="bOS",
            suggested_area=panel,
        )

    @property
    def _raw(self) -> object:
        """Latest raw value for this object from the coordinator."""
        return self.coordinator.data.get(self._object)
