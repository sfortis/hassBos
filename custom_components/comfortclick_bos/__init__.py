"""The ComfortClick bOS integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BosAuthError, BosClient, BosConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_ENTITIES,
    CONF_POLLING,
    DOMAIN,
    ENT_FORM,
    ENT_OBJECT,
    ENT_PANEL,
    ENT_PANEL_PATH,
)
from .coordinator import BosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
]

type BosConfigEntry = ConfigEntry[BosCoordinator]


def _device_id(base: str, panel: str | None) -> tuple[str, str]:
    return (DOMAIN, f"{base}::{panel or 'bOS'}")


def entities_from_entry(entry: ConfigEntry) -> list[dict]:
    """Configured entity descriptors."""
    return entry.data.get(CONF_ENTITIES, [])


async def async_setup_entry(hass: HomeAssistant, entry: BosConfigEntry) -> bool:
    """Set up ComfortClick bOS from a config entry."""
    # A dedicated session gives us an isolated cookie jar for the JWT Token.
    session = async_create_clientsession(hass)
    client = BosClient(
        session,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    try:
        await client.login()
    except BosAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BosConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    configured = entities_from_entry(entry)
    panel_paths = {
        item[ENT_PANEL_PATH] for item in configured if item.get(ENT_PANEL_PATH)
    }
    form_objs = {item[ENT_FORM] for item in configured if item.get(ENT_FORM)}
    polling = entry.options.get(CONF_POLLING, True)
    coordinator = BosCoordinator(hass, client, panel_paths, form_objs, polling)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _cleanup_stale(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BosConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: BosConfigEntry) -> None:
    """Reload when options (e.g. the polling toggle) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: BosConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device only if it no longer maps to a configured entity."""
    base = config_entry.unique_id or config_entry.entry_id
    active = {
        _device_id(base, item.get(ENT_PANEL))
        for item in entities_from_entry(config_entry)
    }
    return not (device_entry.identifiers & active)


@callback
def _cleanup_stale(hass: HomeAssistant, entry: BosConfigEntry) -> None:
    """Drop entities/devices left over from a previous configuration."""
    base = entry.unique_id or entry.entry_id
    items = entities_from_entry(entry)
    current_uids = {f"{base}::{item[ENT_OBJECT]}" for item in items}
    current_devices = {_device_id(base, item.get(ENT_PANEL)) for item in items}

    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id not in current_uids:
            _LOGGER.debug("Removing stale entity %s", entity.entity_id)
            ent_reg.async_remove(entity.entity_id)

    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not (device.identifiers & current_devices):
            _LOGGER.debug("Removing stale device %s", device.name)
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
