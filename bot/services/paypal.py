import httpx
import base64
import logging
from bot.config import PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_MODE, BASE_URL

logger = logging.getLogger(__name__)

PAYPAL_BASE = (
    "https://api-m.paypal.com"
    if PAYPAL_MODE == "live"
    else "https://api-m.sandbox.paypal.com"
)


async def _get_access_token() -> str:
    """Get PayPal OAuth2 access token."""
    credentials = base64.b64encode(
        f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYPAL_BASE}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def create_order(
    amount: float,
    currency: str = "USD",
    description: str = "Image Purchase",
    custom_id: str = "",
) -> dict:
    """
    Create a PayPal checkout order.
    Returns: {"order_id": str, "approve_url": str}
    """
    token = await _get_access_token()

    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": currency,
                    "value": f"{amount:.2f}",
                },
                "description": description,
                "custom_id": custom_id,  # We store our internal order ID here
            }
        ],
        "application_context": {
            "brand_name": "Exclusive Content",
            "landing_page": "NO_PREFERENCE",
            "user_action": "PAY_NOW",
            "return_url": f"{BASE_URL}/paypal/return",
            "cancel_url": f"{BASE_URL}/paypal/cancel",
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYPAL_BASE}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=order_data,
        )
        response.raise_for_status()
        data = response.json()

    approve_url = None
    for link in data.get("links", []):
        if link["rel"] == "approve":
            approve_url = link["href"]
            break

    return {
        "order_id": data["id"],
        "approve_url": approve_url,
    }


async def capture_order(paypal_order_id: str) -> dict:
    """Capture a PayPal order after user approval."""
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYPAL_BASE}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()


async def get_order_details(paypal_order_id: str) -> dict:
    """Get details of a PayPal order."""
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PAYPAL_BASE}/v2/checkout/orders/{paypal_order_id}",
            headers={
                "Authorization": f"Bearer {token}",
            },
        )
        response.raise_for_status()
        return response.json()


async def verify_webhook_signature(
    headers: dict, body: bytes, webhook_id: str
) -> bool:
    """Verify PayPal webhook signature."""
    token = await _get_access_token()

    verification_data = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
        "webhook_id": webhook_id,
        "webhook_event": __import__("json").loads(body),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYPAL_BASE}/v1/notifications/verify-webhook-signature",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=verification_data,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("verification_status") == "SUCCESS"
