# USPS Tracking for Home Assistant

Track USPS packages by tracking number using the **official USPS Tracking API**.
Each package gets a sensor whose state is the current status, with the **most recent
scan events** (default 5, minimum 3) in its attributes.

> The old USPS Web Tools API was retired on 25 Jan 2026. This integration uses the
> replacement OAuth 2.0 API from the [USPS Developer Portal](https://developers.usps.com).

## Setup

1. Sign in to the USPS Customer Onboarding Portal (free USPS Business account), go to
   **My Apps → Developer Apps → Add App**, and enable the Tracking API.
   Copy the **Client ID** and **Client Secret**. (Old Web Tools User IDs do not work.)
2. Install via HACS (custom repository, category *Integration*) or copy
   `custom_components/usps_tracking` into your config directory. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → USPS Tracking** and enter the credentials.
4. Add packages: open the integration → **Configure → Add a package**, or call the
   `usps_tracking.add_package` service.

## Entities

For each package, `sensor.usps_tracking_<label or number>`:

| | |
|---|---|
| **State** | USPS status text, e.g. `Out for Delivery, Expected Delivery by 8:00pm` |
| `recent_events` | List of the latest scans, newest first: `event`, `time`, `location`, `code` |
| `recent_events_text` | Same events as a Markdown bullet list |
| `status_category`, `summary`, `expected_delivery`, `mail_class`, `origin`, `destination`, `delivered` | Package details |
| `last_event`, `last_event_time`, `last_event_location` | Shortcut to the newest scan |

Plus `sensor.usps_tracking_active_packages`, the number of undelivered packages.

Packages that USPS hasn't scanned yet (a label was just created) show
`Not yet in USPS system`. Delivered packages stop being polled.

## Polling

Once an hour by default (configurable, minimum 15 min). Scans are usually hours apart,
so this is plenty and stays well within USPS rate limits.

## Dashboard card

```yaml
type: markdown
title: Keyboard
content: |
  **{{ states('sensor.usps_tracking_keyboard') }}**

  {{ state_attr('sensor.usps_tracking_keyboard', 'recent_events_text') }}
```

## Automation example

An event is fired whenever a package's status or newest scan changes
(not on first load):

```yaml
automation:
  - alias: Package update
    trigger:
      - platform: event
        event_type: usps_tracking_package_updated
    action:
      - service: notify.notify
        data:
          title: "{{ trigger.event.data.label }}"
          message: >
            {{ trigger.event.data.latest_event.event }}
            {{ '(' ~ trigger.event.data.latest_event.location ~ ')' if trigger.event.data.latest_event.location }}
```

Event data: `tracking_number`, `label`, `status`, `status_category`, `previous_status`,
`delivered`, `latest_event`.

## Services

- `usps_tracking.add_package` — `tracking_number`, optional `label`
- `usps_tracking.remove_package` — `tracking_number`

## Development

```bash
pip install -r requirements_test.txt pytest-homeassistant-custom-component
pytest
```

The tests mock all USPS traffic. The response shape follows
[USPS/api-examples](https://github.com/USPS/api-examples); USPS runs Tracking v3 and
v3.2 side by side, so parsing tolerates a few alternative field names. If you see odd
output with your real credentials, please open an issue with the raw JSON (tracking
numbers redacted).

## License

MIT
