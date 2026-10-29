"""Shared fixtures."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usps_tracking.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_LABEL,
    CONF_MAX_EVENTS,
    CONF_PACKAGES,
    CONF_SCAN_INTERVAL,
    CONF_TRACKING_NUMBER,
    DOMAIN,
)

NUMBER = "9400111899223397910390"
SECOND = "9205590164917312751089"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow HA to load custom_components/ in tests."""
    yield


@pytest.fixture
def mock_usps():
    """Intercept outgoing aiohttp calls (HA's shared session included).

    HA's test harness allows loopback sockets only, so anything not registered here
    fails loudly instead of reaching the real USPS.
    """
    with aioresponses(passthrough=["http://127.0.0.1"]) as m:
        yield m


def make_entry(packages=None, **options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="USPS Tracking",
        unique_id="client-id",
        data={CONF_CLIENT_ID: "client-id", CONF_CLIENT_SECRET: "secret"},
        options={
            CONF_PACKAGES: packages
            if packages is not None
            else [{CONF_TRACKING_NUMBER: NUMBER, CONF_LABEL: "Keyboard"}],
            CONF_SCAN_INTERVAL: 60,
            CONF_MAX_EVENTS: 5,
            **options,
        },
    )
