"""Config and options flow for USPS Tracking."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import async_remove_package_entities
from .api import (
    UspsApiClient,
    UspsAuthError,
    UspsConnectionError,
    normalize_tracking_number,
)
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_LABEL,
    CONF_MAX_EVENTS,
    CONF_PACKAGES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKING_NUMBER,
    DEFAULT_MAX_EVENTS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_MAX_EVENTS,
    MIN_MAX_EVENTS,
    MIN_SCAN_INTERVAL_MINUTES,
)


async def _validate(hass, client_id: str, client_secret: str) -> str | None:
    """Return an error key, or None if the credentials work."""
    client = UspsApiClient(async_get_clientsession(hass), client_id, client_secret)
    try:
        await client.async_authenticate()
    except UspsAuthError:
        return "invalid_auth"
    except UspsConnectionError:
        return "cannot_connect"
    return None


class UspsTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: just the USPS API credentials."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UspsTrackingOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIENT_ID])
            self._abort_if_unique_id_configured()
            error = await _validate(
                self.hass, user_input[CONF_CLIENT_ID], user_input[CONF_CLIENT_SECRET]
            )
            if error is None:
                return self.async_create_entry(
                    title="USPS Tracking",
                    data=user_input,
                    options={
                        CONF_PACKAGES: [],
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MINUTES,
                        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                    },
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            error = await _validate(
                self.hass, user_input[CONF_CLIENT_ID], user_input[CONF_CLIENT_SECRET]
            )
            if error is None:
                return self.async_update_reload_and_abort(entry, data=user_input)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CLIENT_ID, default=entry.data.get(CONF_CLIENT_ID, "")
                    ): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )


class UspsTrackingOptionsFlow(OptionsFlow):
    """Add/remove packages by hand and tweak polling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["add_package", "remove_package", "settings"]
        )

    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_add_package(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            number = normalize_tracking_number(user_input[CONF_TRACKING_NUMBER])
            options = self._options()
            packages = list(options.get(CONF_PACKAGES, []))
            if len(number) < 10 or not number.isalnum():
                errors[CONF_TRACKING_NUMBER] = "invalid_tracking_number"
            elif any(p[CONF_TRACKING_NUMBER] == number for p in packages):
                errors[CONF_TRACKING_NUMBER] = "already_tracked"
            else:
                packages.append(
                    {
                        CONF_TRACKING_NUMBER: number,
                        CONF_LABEL: user_input.get(CONF_LABEL, "").strip(),
                    }
                )
                options[CONF_PACKAGES] = packages
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="add_package",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRACKING_NUMBER): str,
                    vol.Optional(CONF_LABEL, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_remove_package(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options()
        packages = list(options.get(CONF_PACKAGES, []))
        if not packages:
            return self.async_abort(reason="no_packages")

        if user_input is not None:
            to_remove = set(user_input[CONF_PACKAGES])
            async_remove_package_entities(
                self.hass, self.config_entry, sorted(to_remove)
            )
            options[CONF_PACKAGES] = [
                p for p in packages if p[CONF_TRACKING_NUMBER] not in to_remove
            ]
            return self.async_create_entry(title="", data=options)

        choices = [
            {
                "value": p[CONF_TRACKING_NUMBER],
                "label": (
                    f"{p[CONF_LABEL]} ({p[CONF_TRACKING_NUMBER]})"
                    if p.get(CONF_LABEL)
                    else p[CONF_TRACKING_NUMBER]
                ),
            }
            for p in packages
        ]
        return self.async_show_form(
            step_id="remove_package",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PACKAGES): SelectSelector(
                        SelectSelectorConfig(
                            options=choices,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options()
        if user_input is not None:
            options[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            options[CONF_MAX_EVENTS] = int(user_input[CONF_MAX_EVENTS])
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=1440,
                            step=5,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_MAX_EVENTS,
                        default=options.get(CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_MAX_EVENTS,
                            max=MAX_MAX_EVENTS,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
