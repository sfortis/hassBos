"""The ComfortClick bOS integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BosAuthError, BosClient, BosConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_LIGHTS,
    KIND_DIMMER,
    LIGHT_KIND,
    LIGHT_MAX,
    LIGHT_MIN,
    LIGHT_NAME,
    LIGHT_OBJECT,
    LIGHT_PANEL,
    LIGHT_PANEL_PATH,
)
from .coordinator import BosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT]

type BosConfigEntry = ConfigEntry[BosCoordinator]


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

    panel_paths = {
        light[LIGHT_PANEL_PATH]
        for light in entry.data.get(CONF_LIGHTS, [])
        if light.get(LIGHT_PANEL_PATH)
    }
    coordinator = BosCoordinator(hass, client, panel_paths)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BosConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry to the current schema."""
    if entry.version > 2:
        # Downgrade from a future version is not supported.
        return False
    if entry.version == 1:
        # v1 held a single light (object_name/name/panel). Wrap it as one
        # dimmer in the v2 lights list; the user can re-scan to add more.
        old = entry.data
        panel_path = old.get("panel") or ""
        light = {
            LIGHT_OBJECT: old["object_name"],
            LIGHT_NAME: old.get("name") or old["object_name"].split("\\")[-1],
            LIGHT_PANEL: panel_path.split("\\")[-1] or "bOS",
            LIGHT_PANEL_PATH: panel_path,
            LIGHT_KIND: KIND_DIMMER,
            LIGHT_MIN: 0,
            LIGHT_MAX: 100,
        }
        new = {
            CONF_BASE_URL: old[CONF_BASE_URL],
            CONF_USERNAME: old[CONF_USERNAME],
            CONF_PASSWORD: old[CONF_PASSWORD],
            CONF_LIGHTS: [light],
        }
        hass.config_entries.async_update_entry(entry, data=new, version=2)
    return True
