"""Config flow for ComfortClick bOS: credentials, then discovery + selection."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BosAuthError, BosClient, BosConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_LIGHTS,
    DEFAULT_BASE_URL,
    DOMAIN,
    LIGHT_KIND,
    LIGHT_NAME,
    LIGHT_OBJECT,
    LIGHT_PANEL,
)
from .discovery import async_discover_lights

_LOGGER = logging.getLogger(__name__)


def _creds_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            ): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


def _label(light: dict) -> str:
    return f"[{light.get(LIGHT_PANEL, '')}] {light[LIGHT_NAME]} ({light[LIGHT_KIND]})"


class ComfortClickBosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 2

    def __init__(self) -> None:
        self._creds: dict[str, Any] = {}
        self._discovered: list[dict] = []
        self._preselected: list[str] | None = None

    async def _connect_and_discover(self, creds: dict[str, Any]) -> dict[str, str]:
        """Validate credentials and run discovery. Returns form errors (empty = ok)."""
        session = async_create_clientsession(self.hass)
        client = BosClient(
            session, creds[CONF_BASE_URL], creds[CONF_USERNAME], creds[CONF_PASSWORD]
        )
        try:
            await client.login()
            self._discovered = await async_discover_lights(client)
        except BosAuthError:
            return {"base": "invalid_auth"}
        except BosConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during discovery")
            return {"base": "unknown"}
        if not self._discovered:
            return {"base": "no_lights"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: credentials, then connect and discover."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_BASE_URL])
            self._abort_if_unique_id_configured()
            errors = await self._connect_and_discover(user_input)
            if not errors:
                self._creds = user_input
                return await self.async_step_select()
        return self.async_show_form(
            step_id="user", data_schema=_creds_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-scan using the stored credentials (never re-asks for them)."""
        entry = self._get_reconfigure_entry()
        self._creds = {
            CONF_BASE_URL: entry.data[CONF_BASE_URL],
            CONF_USERNAME: entry.data[CONF_USERNAME],
            CONF_PASSWORD: entry.data[CONF_PASSWORD],
        }
        errors = await self._connect_and_discover(self._creds)
        if errors:
            return self.async_abort(reason=errors["base"])
        self._preselected = [
            light[LIGHT_OBJECT] for light in entry.data.get(CONF_LIGHTS, [])
        ]
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which discovered lights to add (or keep)."""
        options = {light[LIGHT_OBJECT]: _label(light) for light in self._discovered}
        if user_input is not None:
            chosen = set(user_input[CONF_LIGHTS])
            lights = [d for d in self._discovered if d[LIGHT_OBJECT] in chosen]
            data = {**self._creds, CONF_LIGHTS: lights}
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data=data
                )
            return self.async_create_entry(
                title=f"ComfortClick bOS ({len(lights)} lights)", data=data
            )
        default = self._preselected if self._preselected is not None else list(options)
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {vol.Required(CONF_LIGHTS, default=default): cv.multi_select(options)}
            ),
            description_placeholders={"count": str(len(options))},
        )
