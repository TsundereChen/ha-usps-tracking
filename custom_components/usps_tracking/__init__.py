"""The USPS Tracking integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UspsApiClient, normalize_tracking_number
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_LABEL,
    CONF_PACKAGES,
    CONF_TRACKING_NUMBER,
    DOMAIN,
    SERVICE_ADD_PACKAGE,
    SERVICE_REMOVE_PACKAGE,
)
from .coordinator import UspsTrackingCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type UspsConfigEntry = ConfigEntry[UspsTrackingCoordinator]

ADD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_NUMBER): cv.string,
        vol.Optional(CONF_LABEL, default=""): cv.string,
    }
)
REMOVE_SCHEMA = vol.Schema({vol.Required(CONF_TRACKING_NUMBER): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: UspsConfigEntry) -> bool:
    """Set up USPS Tracking from a config entry."""
    client = UspsApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
    )
    coordinator = UspsTrackingCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Any options change (add/remove package, interval) reloads the entry.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UspsConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_ADD_PACKAGE)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_PACKAGE)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: UspsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _get_entry(hass: HomeAssistant) -> UspsConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("USPS Tracking is not set up")
    return entries[0]


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ADD_PACKAGE):
        return

    async def add_package(call: ServiceCall) -> None:
        entry = _get_entry(hass)
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        if len(number) < 10 or not number.isalnum():
            raise HomeAssistantError(f"'{number}' is not a valid tracking number")
        packages = list(entry.options.get(CONF_PACKAGES, []))
        if any(p[CONF_TRACKING_NUMBER] == number for p in packages):
            raise HomeAssistantError(f"{number} is already being tracked")
        packages.append(
            {CONF_TRACKING_NUMBER: number, CONF_LABEL: call.data[CONF_LABEL].strip()}
        )
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PACKAGES: packages}
        )

    async def remove_package(call: ServiceCall) -> None:
        entry = _get_entry(hass)
        number = normalize_tracking_number(call.data[CONF_TRACKING_NUMBER])
        packages = list(entry.options.get(CONF_PACKAGES, []))
        remaining = [p for p in packages if p[CONF_TRACKING_NUMBER] != number]
        if len(remaining) == len(packages):
            raise HomeAssistantError(f"{number} is not being tracked")
        async_remove_package_entities(hass, entry, [number])
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PACKAGES: remaining}
        )

    hass.services.async_register(DOMAIN, SERVICE_ADD_PACKAGE, add_package, ADD_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_PACKAGE, remove_package, REMOVE_SCHEMA
    )


def async_remove_package_entities(
    hass: HomeAssistant, entry: ConfigEntry, tracking_numbers: list[str]
) -> None:
    """Delete registry entries for packages that are no longer tracked."""
    registry = er.async_get(hass)
    for number in tracking_numbers:
        unique_id = f"{entry.entry_id}_{number}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)
