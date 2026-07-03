"""Config flow for the ComfortClick bOS integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BosAuthError, BosClient, BosConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_OBJECT_NAME,
    CONF_PANEL,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the form schema, pre-filled with defaults (for reconfigure)."""
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            ): str,
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(
                CONF_OBJECT_NAME, default=defaults.get(CONF_OBJECT_NAME, "")
            ): str,
            vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Optional(CONF_PANEL, default=defaults.get(CONF_PANEL, "")): str,
        }
    )


class ComfortClickBosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ComfortClick bOS."""

    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> None:
        """Verify credentials by logging in. Raises on failure."""
        session = async_create_clientsession(self.hass)
        client = BosClient(
            session,
            data[CONF_BASE_URL],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
        )
        await client.login()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._try(user_input)
            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_BASE_URL]}::{user_input[CONF_OBJECT_NAME]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._title(user_input), data=user_input
                )
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of server, credentials and object."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._try(user_input)
            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_BASE_URL]}::{user_input[CONF_OBJECT_NAME]}"
                )
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, title=self._title(user_input), data=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def _try(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Run validation, mapping exceptions to form errors."""
        try:
            await self._validate(user_input)
        except BosAuthError:
            return {"base": "invalid_auth"}
        except BosConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating bOS connection")
            return {"base": "unknown"}
        return {}

    @staticmethod
    def _title(user_input: dict[str, Any]) -> str:
        return user_input.get(CONF_NAME) or user_input[CONF_OBJECT_NAME].split("\\")[-1]
