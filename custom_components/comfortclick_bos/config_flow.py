"""Config flow for ComfortClick bOS: credentials, then per-floor light selection.

After connecting and discovering, the user picks lights one floor (panel) at a
time from a dropdown, then finishes. Selections accumulate across floors.
"""

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
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

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

_FINISH = "__finish__"
_CONF_FLOOR = "floor"


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


class ComfortClickBosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 2

    def __init__(self) -> None:
        self._creds: dict[str, Any] = {}
        self._discovered: list[dict] = []
        self._by_panel: dict[str, list[dict]] = {}
        self._selected: dict[str, dict] = {}
        self._panel: str = ""

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
        self._by_panel = {}
        for light in self._discovered:
            self._by_panel.setdefault(light[LIGHT_PANEL], []).append(light)
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
                return await self.async_step_floor()
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
        # Pre-select the currently configured lights that still exist.
        discovered_objs = {light[LIGHT_OBJECT] for light in self._discovered}
        for light in entry.data.get(CONF_LIGHTS, []):
            if light[LIGHT_OBJECT] in discovered_objs:
                self._selected[light[LIGHT_OBJECT]] = light
        return await self.async_step_floor()

    async def async_step_floor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a floor to edit, or finish."""
        if user_input is not None:
            if user_input[_CONF_FLOOR] == _FINISH:
                return self._finish()
            self._panel = user_input[_CONF_FLOOR]
            return await self.async_step_lights()

        options = [
            SelectOptionDict(
                value=panel,
                label=f"{panel} ({self._selected_count(panel)}/{len(lights)} selected)",
            )
            for panel, lights in self._by_panel.items()
        ]
        options.append(SelectOptionDict(value=_FINISH, label="Finish adding lights"))
        return self.async_show_form(
            step_id="floor",
            data_schema=vol.Schema(
                {
                    vol.Required(_CONF_FLOOR): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            description_placeholders={"total": str(len(self._selected))},
        )

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Multi-select the lights on the currently chosen floor."""
        panel_lights = self._by_panel[self._panel]
        options = {
            light[LIGHT_OBJECT]: f"{light[LIGHT_NAME]} ({light[LIGHT_KIND]})"
            for light in panel_lights
        }
        if user_input is not None:
            chosen = set(user_input[CONF_LIGHTS])
            for light in panel_lights:
                obj = light[LIGHT_OBJECT]
                if obj in chosen:
                    self._selected[obj] = light
                else:
                    self._selected.pop(obj, None)
            return await self.async_step_floor()

        default = [
            light[LIGHT_OBJECT]
            for light in panel_lights
            if light[LIGHT_OBJECT] in self._selected
        ]
        return self.async_show_form(
            step_id="lights",
            data_schema=vol.Schema(
                {vol.Optional(CONF_LIGHTS, default=default): cv.multi_select(options)}
            ),
            description_placeholders={"floor": self._panel},
        )

    def _selected_count(self, panel: str) -> int:
        return sum(
            1
            for light in self._by_panel[panel]
            if light[LIGHT_OBJECT] in self._selected
        )

    def _finish(self) -> ConfigFlowResult:
        lights = list(self._selected.values())
        data = {**self._creds, CONF_LIGHTS: lights}
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data=data
            )
        return self.async_create_entry(
            title=f"ComfortClick bOS ({len(lights)} lights)", data=data
        )
