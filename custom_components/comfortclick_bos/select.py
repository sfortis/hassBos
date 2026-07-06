"""Select platform for ComfortClick bOS (settable enum controls, e.g. ERV mode)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BosConfigEntry, coordinator_for, entities_from_entry
from .api import BosError
from .const import ENT_ICON, ENT_KIND, ENT_OPTIONS, KIND_SELECT
from .entity import BosEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BosConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from the config entry."""
    async_add_entities(
        BosSelect(coordinator_for(entry, item), entry, item)
        for item in entities_from_entry(entry)
        if item.get(ENT_KIND) == KIND_SELECT
    )


class BosSelect(BosEntity, SelectEntity):
    """A settable enum bOS value (index <-> text)."""

    def __init__(self, coordinator, entry, item) -> None:
        super().__init__(coordinator, entry, item)
        self._options: dict[str, str] = item.get(ENT_OPTIONS) or {}
        self._text_to_idx = {text: idx for idx, text in self._options.items()}
        self._attr_options = list(self._options.values())
        if item.get(ENT_ICON):
            self._attr_icon = item[ENT_ICON]

    @property
    def current_option(self) -> str | None:
        try:
            return self._options.get(str(int(float(self._raw))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        idx = self._text_to_idx.get(option)
        if idx is None:
            return
        try:
            await self.coordinator.client.set_value(self._object, idx)
        except BosError as err:
            raise HomeAssistantError(f"Failed to set bOS option: {err}") from err
        self.coordinator.set_local(self._object, int(idx))
