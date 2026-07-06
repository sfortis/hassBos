"""The ComfortClick bOS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    ENT_OBJECT,
    ENT_PANEL,
    ENT_PANEL_PATH,
    MAX_LIVE_SESSIONS,
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


@dataclass
class BosRuntime:
    """Per-entry runtime: one coordinator/session per floor panel."""

    # panel_path -> coordinator that owns it.
    by_panel: dict[str, BosCoordinator]
    # Distinct coordinators (for first refresh / iteration).
    coordinators: list[BosCoordinator]
    # Clients whose sessions we must close on unload.
    clients: list[BosClient] = field(default_factory=list)

    def for_item(self, item: dict) -> BosCoordinator:
        """Coordinator that carries this entity's live value."""
        coord = self.by_panel.get(item.get(ENT_PANEL_PATH))
        if coord is not None:
            return coord
        # Defensive fallback (should not happen: every item has a mapped panel).
        return self.coordinators[0]


type BosConfigEntry = ConfigEntry[BosRuntime]


def _device_id(base: str, panel: str | None) -> tuple[str, str]:
    return (DOMAIN, f"{base}::{panel or 'bOS'}")


def entities_from_entry(entry: ConfigEntry) -> list[dict]:
    """Configured entity descriptors."""
    return entry.data.get(CONF_ENTITIES, [])


def coordinator_for(entry: BosConfigEntry, item: dict) -> BosCoordinator:
    """Resolve the coordinator that owns a given entity's panel."""
    return entry.runtime_data.for_item(item)


async def _close_all(clients: list[BosClient]) -> None:
    for client in clients:
        await client.close()


async def async_setup_entry(hass: HomeAssistant, entry: BosConfigEntry) -> bool:
    """Set up ComfortClick bOS from a config entry."""
    configured = entities_from_entry(entry)
    panels = sorted(
        {item[ENT_PANEL_PATH] for item in configured if item.get(ENT_PANEL_PATH)}
    )
    polling = entry.options.get(CONF_POLLING, True)

    # First MAX_LIVE_SESSIONS panels get a dedicated live session; any extras share
    # one fallback session that round-robins across them. Empty -> a single session
    # with no panels (still validates credentials).
    groups: list[list[str]] = [[p] for p in panels[:MAX_LIVE_SESSIONS]]
    overflow = panels[MAX_LIVE_SESSIONS:]
    if overflow:
        groups.append(overflow)
    if not groups:
        groups = [[]]

    clients: list[BosClient] = []
    coordinators: list[BosCoordinator] = []
    by_panel: dict[str, BosCoordinator] = {}

    try:
        for group in groups:
            # A dedicated session per coordinator = an isolated cookie jar for its
            # JWT Token, so each panel's GetClientData subscription stays separate.
            client = BosClient(
                async_create_clientsession(hass),
                entry.data[CONF_BASE_URL],
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
            )
            await client.login()
            clients.append(client)
            coordinator = BosCoordinator(hass, client, group, polling)
            coordinators.append(coordinator)
            for path in group:
                by_panel[path] = coordinator

        for coordinator in coordinators:
            await coordinator.async_config_entry_first_refresh()
    except BosAuthError as err:
        await _close_all(clients)
        raise ConfigEntryAuthFailed(str(err)) from err
    except BosConnectionError as err:
        await _close_all(clients)
        raise ConfigEntryNotReady(str(err)) from err
    except Exception:
        await _close_all(clients)
        raise

    entry.runtime_data = BosRuntime(by_panel, coordinators, clients)
    _cleanup_stale(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BosConfigEntry) -> bool:
    """Unload a config entry and close its sessions."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await _close_all(entry.runtime_data.clients)
    return unloaded


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
