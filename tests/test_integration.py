"""End-to-end tests against a real (test) Home Assistant instance."""

from __future__ import annotations

from datetime import timedelta
import re

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.usps_tracking.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_LABEL,
    CONF_PACKAGES,
    CONF_TRACKING_NUMBER,
    DOMAIN,
    EVENT_PACKAGE_UPDATED,
    TOKEN_URL,
)

from .conftest import NUMBER, SECOND, make_entry
from .fixtures import DELIVERED, IN_TRANSIT, TOKEN_RESPONSE

TRACK_RE = re.compile(r"^https://apis\.usps\.com/tracking/v3/tracking/(\w+)\?expand=DETAIL$")


def _track_url(number: str) -> str:
    return f"https://apis.usps.com/tracking/v3/tracking/{number}?expand=DETAIL"


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensor_shows_status_and_at_least_three_recent_events(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), payload=IN_TRANSIT)
    await _setup(hass, make_entry())

    state = hass.states.get("sensor.usps_tracking_keyboard")
    assert state is not None
    assert state.state == "Arrived at USPS Regional Facility"

    events = state.attributes["recent_events"]
    assert len(events) == 4  # all four fixture events fit in the default limit of 5
    assert len(events) >= 3
    assert [e["event"] for e in events[:3]] == [
        "Arrived at USPS Regional Facility",
        "Departed USPS Regional Facility",
        "Departed Post Office",
    ]
    assert state.attributes["last_event"] == "Arrived at USPS Regional Facility"
    assert state.attributes["expected_delivery"] == "2026-10-31T20:00:00Z"
    assert "Departed Post Office" in state.attributes["recent_events_text"]
    assert state.attributes["recent_events_text"].count("\n") == 3  # 4 lines

    active = hass.states.get("sensor.usps_tracking_active_packages")
    assert active.state == "1"


async def test_max_events_option_limits_history(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), payload=IN_TRANSIT)
    await _setup(hass, make_entry(max_events=3))

    state = hass.states.get("sensor.usps_tracking_keyboard")
    assert len(state.attributes["recent_events"]) == 3


async def test_unknown_package_shows_not_yet_in_system(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), status=404)
    await _setup(hass, make_entry())

    state = hass.states.get("sensor.usps_tracking_keyboard")
    assert state.state == "Not yet in USPS system"
    assert state.attributes["recent_events"] == []


async def test_status_change_fires_event_and_delivered_stops_polling(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), payload=IN_TRANSIT)
    mock_usps.get(_track_url(NUMBER), payload=DELIVERED)
    entry = make_entry()
    await _setup(hass, entry)

    fired = []
    hass.bus.async_listen(EVENT_PACKAGE_UPDATED, lambda e: fired.append(e.data))

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=61))
    await hass.async_block_till_done()

    state = hass.states.get("sensor.usps_tracking_keyboard")
    assert state.state == "Delivered, Front Door/Porch"
    assert state.attributes["delivered"] is True
    assert hass.states.get("sensor.usps_tracking_active_packages").state == "0"

    assert len(fired) == 1
    assert fired[0]["label"] == "Keyboard"
    assert fired[0]["previous_status"] == "Arrived at USPS Regional Facility"
    assert fired[0]["delivered"] is True
    assert fired[0]["latest_event"]["event"] == "Delivered, Front Door/Porch"

    # Next poll: package is delivered, so no further GET should be made.
    get_calls_before = sum(
        len(v) for k, v in mock_usps.requests.items() if k[0] == "GET"
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=125))
    await hass.async_block_till_done()
    get_calls_after = sum(len(v) for k, v in mock_usps.requests.items() if k[0] == "GET")
    assert get_calls_after == get_calls_before
    assert len(fired) == 1


async def test_bad_credentials_start_reauth(hass, mock_usps):
    mock_usps.post(TOKEN_URL, status=401, body='{"error":"invalid_client"}')
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(hass.config_entries.flow.async_progress_by_handler(DOMAIN))


async def test_config_flow_success_and_invalid_auth(hass, mock_usps):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM

    mock_usps.post(TOKEN_URL, status=401, body="{}")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CLIENT_ID: "abc", CONF_CLIENT_SECRET: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CLIENT_ID: "abc", CONF_CLIENT_SECRET: "good"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_CLIENT_ID: "abc", CONF_CLIENT_SECRET: "good"}
    assert result["options"][CONF_PACKAGES] == []
    await hass.async_block_till_done()


async def test_options_flow_add_and_remove_package(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), payload=IN_TRANSIT, repeat=True)
    mock_usps.get(_track_url(SECOND), payload=DELIVERED, repeat=True)
    entry = make_entry()
    await _setup(hass, entry)

    # Add by hand (spaces/dashes tolerated, label optional).
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_package"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TRACKING_NUMBER: "9205 5901-6491 7312 7510 89", CONF_LABEL: "Book"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # update listener reloads the entry

    assert hass.states.get("sensor.usps_tracking_book").state == (
        "Delivered, Front Door/Porch"
    )
    assert hass.states.get("sensor.usps_tracking_keyboard") is not None

    # Duplicate and malformed numbers are rejected.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_package"}
    )
    dup = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRACKING_NUMBER: SECOND, CONF_LABEL: ""}
    )
    assert dup["errors"] == {CONF_TRACKING_NUMBER: "already_tracked"}
    bad = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRACKING_NUMBER: "12", CONF_LABEL: ""}
    )
    assert bad["errors"] == {CONF_TRACKING_NUMBER: "invalid_tracking_number"}
    hass.config_entries.options.async_abort(result["flow_id"])

    # Remove one.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_package"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PACKAGES: [SECOND]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert hass.states.get("sensor.usps_tracking_book") is None
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{SECOND}") is None
    assert hass.states.get("sensor.usps_tracking_keyboard") is not None


async def test_services_add_and_remove(hass, mock_usps):
    mock_usps.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    mock_usps.get(_track_url(NUMBER), payload=IN_TRANSIT, repeat=True)
    mock_usps.get(_track_url(SECOND), payload=DELIVERED, repeat=True)
    entry = make_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "add_package",
        {"tracking_number": "9205 5901 6491 7312 7510 89", "label": "Book"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.usps_tracking_book") is not None

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "add_package", {"tracking_number": SECOND}, blocking=True
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "add_package", {"tracking_number": "nope"}, blocking=True
        )

    await hass.services.async_call(
        DOMAIN, "remove_package", {"tracking_number": SECOND}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.usps_tracking_book") is None
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "remove_package", {"tracking_number": SECOND}, blocking=True
        )
