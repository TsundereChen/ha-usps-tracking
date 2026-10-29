"""Minimal async client for the USPS Tracking v3 REST API.

This module deliberately has no Home Assistant imports so it can be tested and
reused on its own.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import TOKEN_URL, TRACKING_URL

# Refresh the OAuth token this many seconds before it actually expires.
_TOKEN_EXPIRY_MARGIN = 120


class UspsError(Exception):
    """Base error for the USPS client."""


class UspsAuthError(UspsError):
    """Credentials were rejected."""


class UspsConnectionError(UspsError):
    """Could not talk to USPS."""


class UspsNotFoundError(UspsError):
    """USPS has no record of this tracking number (yet)."""


def normalize_tracking_number(value: str) -> str:
    """Strip spaces/dashes and uppercase a tracking number."""
    return re.sub(r"[\s\-]", "", value or "").upper()


@dataclass(slots=True)
class TrackingEvent:
    """A single scan/event on a package."""

    description: str
    timestamp: datetime | None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    code: str | None = None

    @property
    def location(self) -> str | None:
        """Human readable 'CITY, ST 12345' string."""
        parts = []
        if self.city:
            parts.append(self.city.title())
        region = " ".join(p for p in (self.state, self.zip_code) if p)
        if region:
            parts.append(region)
        return ", ".join(parts) or None

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form for entity attributes."""
        return {
            "event": self.description,
            "time": self.timestamp.isoformat() if self.timestamp else None,
            "location": self.location,
            "code": self.code,
        }


@dataclass(slots=True)
class PackageInfo:
    """Parsed tracking result for one package."""

    tracking_number: str
    status: str | None = None
    status_category: str | None = None
    summary: str | None = None
    expected_delivery: str | None = None
    mail_class: str | None = None
    origin: str | None = None
    destination: str | None = None
    events: list[TrackingEvent] = field(default_factory=list)  # newest first

    @property
    def delivered(self) -> bool:
        """True once USPS reports the package as delivered."""
        return (self.status_category or "").strip().lower() == "delivered"

    @property
    def latest_event(self) -> TrackingEvent | None:
        """Most recent event, if any."""
        return self.events[0] if self.events else None


def _clean(value: Any) -> str | None:
    """Return a stripped string or None. Also strips the HTML entities USPS embeds."""
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", "", str(value))
    text = text.replace("&reg;", "®").replace("&#174;", "®")
    text = text.strip()
    return text or None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _place(city: Any, state: Any, zip_code: Any = None) -> str | None:
    city_s, state_s, zip_s = _clean(city), _clean(state), _clean(zip_code)
    left = city_s.title() if city_s else None
    right = " ".join(p for p in (state_s, zip_s) if p) or None
    return ", ".join(p for p in (left, right) if p) or None


def parse_tracking_response(tracking_number: str, data: dict[str, Any]) -> PackageInfo:
    """Turn a v3 tracking JSON payload into a PackageInfo.

    Field names follow the examples in github.com/USPS/api-examples. Parsing is
    defensive about alternative spellings because USPS runs v3 and v3.2 side by side.
    """
    events: list[TrackingEvent] = []
    for raw in data.get("trackingEvents") or []:
        description = _clean(raw.get("eventType") or raw.get("eventDescription"))
        if not description:
            continue
        events.append(
            TrackingEvent(
                description=description,
                timestamp=_parse_timestamp(raw.get("eventTimestamp")),
                city=_clean(raw.get("eventCity")),
                state=_clean(raw.get("eventState")),
                zip_code=_clean(raw.get("eventZIP")),
                country=_clean(raw.get("eventCountry")),
                code=_clean(raw.get("eventCode")),
            )
        )

    # Newest first, regardless of what order USPS returned them in.
    events.sort(
        key=lambda e: e.timestamp or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    expected = (
        data.get("expectedDeliveryTimeStamp")
        or data.get("expectedDeliveryTimestamp")
        or data.get("expectedDeliveryDate")
        or data.get("predictedDeliveryDate")
    )

    return PackageInfo(
        tracking_number=tracking_number,
        status=_clean(data.get("status")),
        status_category=_clean(data.get("statusCategory")),
        summary=_clean(data.get("statusSummary")),
        expected_delivery=_clean(expected),
        mail_class=_clean(data.get("mailClass")),
        origin=_place(
            data.get("originCity"), data.get("originState"), data.get("originZIP")
        ),
        destination=_place(
            data.get("destinationCity"),
            data.get("destinationState"),
            data.get("destinationZIP"),
        ),
        events=events,
    )


class UspsApiClient:
    """OAuth2 client-credentials client for the USPS Tracking API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def async_authenticate(self, *, force: bool = False) -> str:
        """Return a valid bearer token, fetching a new one if needed."""
        if (
            not force
            and self._token
            and time.monotonic() < self._token_expires_at - _TOKEN_EXPIRY_MARGIN
        ):
            return self._token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        # USPS documents a 'tracking' scope. Ask for it first; if the app wasn't
        # provisioned with it, fall back to a scope-less request.
        for scope in ("tracking", None):
            body = dict(payload, **({"scope": scope} if scope else {}))
            try:
                async with self._session.post(
                    TOKEN_URL, data=body, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status in (400, 401, 403):
                        text = await resp.text()
                        if scope and "scope" in text.lower():
                            continue
                        raise UspsAuthError(
                            f"USPS rejected the credentials (HTTP {resp.status})"
                        )
                    if resp.status >= 400:
                        raise UspsConnectionError(
                            f"USPS token endpoint returned HTTP {resp.status}"
                        )
                    token_data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise UspsConnectionError(f"Cannot reach USPS: {err}") from err

            token = token_data.get("access_token")
            if not token:
                raise UspsAuthError("USPS token response had no access_token")
            try:
                ttl = float(token_data.get("expires_in", 3600))
            except (TypeError, ValueError):
                ttl = 3600.0
            self._token = token
            self._token_expires_at = time.monotonic() + ttl
            return token

        raise UspsAuthError("USPS rejected the requested scope")

    async def async_track(self, tracking_number: str) -> PackageInfo:
        """Fetch tracking status plus full event history for one package."""
        number = normalize_tracking_number(tracking_number)
        for attempt in (1, 2):
            token = await self.async_authenticate(force=attempt == 2)
            try:
                async with self._session.get(
                    TRACKING_URL.format(tracking_number=number),
                    params={"expand": "DETAIL"},  # without this, no trackingEvents
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        continue  # token expired early; retry once with a fresh one
                    if resp.status in (401, 403):
                        raise UspsAuthError(
                            f"USPS denied access to tracking (HTTP {resp.status})"
                        )
                    if resp.status == 404:
                        raise UspsNotFoundError(number)
                    if resp.status == 429:
                        raise UspsConnectionError("USPS rate limit hit (HTTP 429)")
                    if resp.status >= 400:
                        raise UspsConnectionError(
                            f"USPS tracking returned HTTP {resp.status}"
                        )
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise UspsConnectionError(f"Cannot reach USPS: {err}") from err
            return parse_tracking_response(number, data)

        raise UspsAuthError("USPS kept rejecting the access token")
