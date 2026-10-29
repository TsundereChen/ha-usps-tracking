"""Tests for the USPS API client and response parsing."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.usps_tracking.api import (
    UspsApiClient,
    UspsAuthError,
    UspsConnectionError,
    UspsNotFoundError,
    normalize_tracking_number,
    parse_tracking_response,
)
from custom_components.usps_tracking.const import TOKEN_URL

from .fixtures import DELIVERED, IN_TRANSIT, TOKEN_RESPONSE

NUMBER = "9400111899223397910390"
TRACK_URL = (
    f"https://apis.usps.com/tracking/v3/tracking/{NUMBER}?expand=DETAIL"
)


def test_normalize():
    assert normalize_tracking_number(" 9400 1118-9922 3397 9103 90 ") == NUMBER
    assert normalize_tracking_number("ec123456789us") == "EC123456789US"


def test_parse_orders_events_newest_first_and_cleans_html():
    info = parse_tracking_response(NUMBER, IN_TRANSIT)
    assert info.status == "Arrived at USPS Regional Facility"
    assert info.status_category == "In Transit"
    assert info.mail_class == "Priority Mail®"
    assert info.origin == "Richmond, VA 23227"
    assert info.destination == "Cedar Rapids, IA 52404"
    assert info.expected_delivery == "2026-10-31T20:00:00Z"
    assert not info.delivered

    descriptions = [e.description for e in info.events]
    assert descriptions == [
        "Arrived at USPS Regional Facility",
        "Departed USPS Regional Facility",
        "Departed Post Office",
        "USPS in possession of item",
    ]
    assert info.latest_event.location == "Columbus Oh, OH 43218"


def test_parse_delivered():
    info = parse_tracking_response(NUMBER, DELIVERED)
    assert info.delivered
    assert info.latest_event.description == "Delivered, Front Door/Porch"


def test_parse_without_events_is_ok():
    info = parse_tracking_response(NUMBER, {"status": "Pre-Shipment", "statusCategory": "Pre-Shipment"})
    assert info.events == []
    assert info.latest_event is None


def test_parse_accepts_alternate_event_field_names():
    info = parse_tracking_response(
        NUMBER,
        {
            "trackingEvents": [
                {"eventDescription": "Out for Delivery", "eventTimestamp": "2026-10-30T12:00:00"}
            ]
        },
    )
    assert info.events[0].description == "Out for Delivery"
    # Naive timestamps are treated as UTC rather than crashing.
    assert info.events[0].timestamp.tzinfo is not None


async def test_track_success_requests_detail_and_bearer_token():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)
            m.get(TRACK_URL, payload=IN_TRANSIT)
            info = await client.async_track("9400 1118 9922 3397 9103 90")

            assert len(info.events) == 4
            get_call = next(c for k, c in m.requests.items() if k[0] == "GET")[0]
            assert get_call.kwargs["headers"]["Authorization"] == "Bearer tok_123"
            post_call = next(c for k, c in m.requests.items() if k[0] == "POST")[0]
            assert post_call.kwargs["data"]["scope"] == "tracking"
            assert post_call.kwargs["data"]["client_id"] == "id"


async def test_token_is_cached_between_calls():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)  # only registered once
            m.get(TRACK_URL, payload=IN_TRANSIT, repeat=True)
            await client.async_track(NUMBER)
            await client.async_track(NUMBER)
            posts = [k for k in m.requests if k[0] == "POST"]
            assert sum(len(m.requests[k]) for k in posts) == 1


async def test_scope_rejection_falls_back_to_scopeless_token():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, status=400, body='{"error":"invalid_scope"}')
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)
            token = await client.async_authenticate()
            assert token == "tok_123"


async def test_bad_credentials_raise_auth_error():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "wrong")
        with aioresponses() as m:
            m.post(TOKEN_URL, status=401, body='{"error":"invalid_client"}')
            with pytest.raises(UspsAuthError):
                await client.async_authenticate()


async def test_401_on_tracking_refreshes_token_once():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)
            m.post(TOKEN_URL, payload={**TOKEN_RESPONSE, "access_token": "tok_456"})
            m.get(TRACK_URL, status=401)
            m.get(TRACK_URL, payload=IN_TRANSIT)
            info = await client.async_track(NUMBER)
            assert info.status == "Arrived at USPS Regional Facility"


async def test_404_is_not_found():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)
            m.get(TRACK_URL, status=404)
            with pytest.raises(UspsNotFoundError):
                await client.async_track(NUMBER)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_server_errors_are_connection_errors(status):
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, payload=TOKEN_RESPONSE)
            m.get(TRACK_URL, status=status)
            with pytest.raises(UspsConnectionError):
                await client.async_track(NUMBER)


async def test_network_failure_is_connection_error():
    async with aiohttp.ClientSession() as session:
        client = UspsApiClient(session, "id", "secret")
        with aioresponses() as m:
            m.post(TOKEN_URL, exception=aiohttp.ClientConnectionError("boom"))
            with pytest.raises(UspsConnectionError):
                await client.async_authenticate()
