"""Constants for the USPS Tracking integration."""

from __future__ import annotations

DOMAIN = "usps_tracking"

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_PACKAGES = "packages"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MAX_EVENTS = "max_events"

CONF_TRACKING_NUMBER = "tracking_number"
CONF_LABEL = "label"

# One request per package per hour is plenty: scan events are typically hours apart.
DEFAULT_SCAN_INTERVAL_MINUTES = 60
MIN_SCAN_INTERVAL_MINUTES = 15
DEFAULT_MAX_EVENTS = 5
MIN_MAX_EVENTS = 3
MAX_MAX_EVENTS = 20

API_BASE = "https://apis.usps.com"
TOKEN_URL = f"{API_BASE}/oauth2/v3/token"
TRACKING_URL = f"{API_BASE}/tracking/v3/tracking/{{tracking_number}}"

SERVICE_ADD_PACKAGE = "add_package"
SERVICE_REMOVE_PACKAGE = "remove_package"

# Fired on the HA event bus whenever a package's status text or latest scan changes.
EVENT_PACKAGE_UPDATED = f"{DOMAIN}_package_updated"

STATUS_DELIVERED = "Delivered"
