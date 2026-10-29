"""Data update coordinator for USPS Tracking."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PackageInfo,
    UspsApiClient,
    UspsAuthError,
    UspsConnectionError,
    UspsNotFoundError,
)
from .const import (
    CONF_LABEL,
    CONF_PACKAGES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKING_NUMBER,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_PACKAGE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

NOT_YET_SCANNED = "Not yet in USPS system"


def _fingerprint(info: PackageInfo) -> tuple[Any, ...]:
    latest = info.latest_event
    return (
        info.status,
        latest.description if latest else None,
        latest.timestamp if latest else None,
    )


class UspsTrackingCoordinator(DataUpdateCoordinator[dict[str, PackageInfo]]):
    """Polls USPS for every tracked package."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UspsApiClient,
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client

    @property
    def packages(self) -> list[dict[str, str]]:
        """Packages configured by the user: [{tracking_number, label}]."""
        return list(self.config_entry.options.get(CONF_PACKAGES, []))

    def label_for(self, tracking_number: str) -> str:
        """Friendly label, falling back to the tracking number."""
        for pkg in self.packages:
            if pkg[CONF_TRACKING_NUMBER] == tracking_number:
                return pkg.get(CONF_LABEL) or tracking_number
        return tracking_number

    async def _async_update_data(self) -> dict[str, PackageInfo]:
        previous = self.data or {}
        results: dict[str, PackageInfo] = {}
        errors: list[str] = []

        for pkg in self.packages:
            number = pkg[CONF_TRACKING_NUMBER]
            old = previous.get(number)

            # Delivered packages never change again: stop spending API calls on them.
            if old is not None and old.delivered:
                results[number] = old
                continue

            try:
                info = await self.client.async_track(number)
            except UspsAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except UspsNotFoundError:
                # Labels created but not yet scanned are unknown to USPS for a while.
                info = PackageInfo(tracking_number=number, status=NOT_YET_SCANNED)
            except UspsConnectionError as err:
                errors.append(f"{number}: {err}")
                if old is not None:
                    results[number] = old  # keep last known state
                continue

            results[number] = info
            if old is not None and _fingerprint(old) != _fingerprint(info):
                latest = info.latest_event
                self.hass.bus.async_fire(
                    EVENT_PACKAGE_UPDATED,
                    {
                        "tracking_number": number,
                        "label": self.label_for(number),
                        "status": info.status,
                        "status_category": info.status_category,
                        "previous_status": old.status,
                        "delivered": info.delivered,
                        "latest_event": latest.as_dict() if latest else None,
                    },
                )

        if errors and not results:
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.warning("Some packages could not be refreshed: %s", "; ".join(errors))
        return results
