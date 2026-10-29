"""Sensors for USPS Tracking."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import UspsConfigEntry
from .api import PackageInfo
from .const import CONF_MAX_EVENTS, CONF_TRACKING_NUMBER, DEFAULT_MAX_EVENTS, DOMAIN
from .coordinator import UspsTrackingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UspsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one sensor per tracked package plus an 'active packages' counter."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [ActivePackagesSensor(coordinator, entry)]
    entities.extend(
        PackageSensor(coordinator, entry, pkg[CONF_TRACKING_NUMBER])
        for pkg in coordinator.packages
    )
    async_add_entities(entities)


def _device_info(entry: UspsConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="USPS Tracking",
        manufacturer="USPS",
        entry_type=DeviceEntryType.SERVICE,
    )


class PackageSensor(CoordinatorEntity[UspsTrackingCoordinator], SensorEntity):
    """State = current USPS status; attributes carry the recent scan history."""

    _attr_has_entity_name = True
    # Event history is bulky and changes every poll; keep it out of the recorder DB.
    _unrecorded_attributes = frozenset({"recent_events", "recent_events_text"})

    def __init__(
        self,
        coordinator: UspsTrackingCoordinator,
        entry: UspsConfigEntry,
        tracking_number: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._number = tracking_number
        self._attr_unique_id = f"{entry.entry_id}_{tracking_number}"
        self._attr_device_info = _device_info(entry)

    @property
    def _info(self) -> PackageInfo | None:
        return (self.coordinator.data or {}).get(self._number)

    @property
    def name(self) -> str:
        """Use the user's label, or the tracking number."""
        return self.coordinator.label_for(self._number)

    @property
    def available(self) -> bool:
        return super().available and self._info is not None

    @property
    def native_value(self) -> str | None:
        info = self._info
        return (info.status or "Unknown") if info else None

    @property
    def icon(self) -> str:
        info = self._info
        if info and info.delivered:
            return "mdi:package-variant-closed-check"
        return "mdi:package-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info
        if info is None:
            return {}
        limit = self._entry.options.get(CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS)
        recent = info.events[:limit]
        lines = []
        for event in recent:
            when = (
                dt_util.as_local(event.timestamp).strftime("%b %d %H:%M")
                if event.timestamp
                else "unknown time"
            )
            where = f" ({event.location})" if event.location else ""
            lines.append(f"- {when}: {event.description}{where}")

        latest = info.latest_event
        return {
            "tracking_number": self._number,
            "status_category": info.status_category,
            "summary": info.summary,
            "expected_delivery": info.expected_delivery,
            "mail_class": info.mail_class,
            "origin": info.origin,
            "destination": info.destination,
            "delivered": info.delivered,
            "last_event": latest.description if latest else None,
            "last_event_time": (
                latest.timestamp.isoformat() if latest and latest.timestamp else None
            ),
            "last_event_location": latest.location if latest else None,
            "recent_events": [e.as_dict() for e in recent],
            # Ready to drop into a Markdown dashboard card.
            "recent_events_text": "\n".join(lines),
        }


class ActivePackagesSensor(CoordinatorEntity[UspsTrackingCoordinator], SensorEntity):
    """Number of tracked packages that have not been delivered yet."""

    _attr_has_entity_name = True
    _attr_name = "Active packages"
    _attr_icon = "mdi:truck-delivery"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "packages"

    def __init__(
        self, coordinator: UspsTrackingCoordinator, entry: UspsConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_active_count"
        self._attr_device_info = _device_info(entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> int:
        data = self.coordinator.data or {}
        return sum(1 for info in data.values() if not info.delivered)
