"""Sample payloads shaped like USPS Tracking v3 responses (see USPS/api-examples)."""

TOKEN_RESPONSE = {"access_token": "tok_123", "token_type": "Bearer", "expires_in": 28800}

IN_TRANSIT = {
    "trackingNumber": "9400111899223397910390",
    "destinationCity": "CEDAR RAPIDS",
    "destinationState": "IA",
    "destinationZIP": "52404",
    "originCity": "RICHMOND",
    "originState": "VA",
    "originZIP": "23227",
    "mailClass": "Priority Mail<SUP>&reg;</SUP>",
    "status": "Arrived at USPS Regional Facility",
    "statusCategory": "In Transit",
    "statusSummary": "Your item arrived at the regional facility.",
    "expectedDeliveryTimeStamp": "2026-10-31T20:00:00Z",
    # Deliberately NOT in chronological order.
    "trackingEvents": [
        {
            "eventType": "USPS in possession of item",
            "eventTimestamp": "2026-10-27T11:31:00Z",
            "eventCity": "RICHMOND",
            "eventState": "VA",
            "eventZIP": "23227",
            "eventCode": "03",
        },
        {
            "eventType": "Arrived at USPS Regional Facility",
            "eventTimestamp": "2026-10-29T02:10:00Z",
            "eventCity": "COLUMBUS OH",
            "eventState": "OH",
            "eventZIP": "43218",
            "eventCode": "10",
        },
        {
            "eventType": "Departed Post Office",
            "eventTimestamp": "2026-10-27T22:00:00Z",
            "eventCity": "RICHMOND",
            "eventState": "VA",
            "eventZIP": "23227",
            "eventCode": "07",
        },
        {
            "eventType": "Departed USPS Regional Facility",
            "eventTimestamp": "2026-10-28T09:40:00Z",
            "eventCity": "RICHMOND",
            "eventState": "VA",
            "eventZIP": "23232",
            "eventCode": "SF",
        },
    ],
}

DELIVERED = {
    **IN_TRANSIT,
    "status": "Delivered, Front Door/Porch",
    "statusCategory": "Delivered",
    "statusSummary": "Your item was delivered at the front door.",
    "expectedDeliveryTimeStamp": None,
    "trackingEvents": [
        {
            "eventType": "Delivered, Front Door/Porch",
            "eventTimestamp": "2026-10-30T15:05:00Z",
            "eventCity": "CEDAR RAPIDS",
            "eventState": "IA",
            "eventZIP": "52404",
            "eventCode": "01",
        },
        *IN_TRANSIT["trackingEvents"],
    ],
}
