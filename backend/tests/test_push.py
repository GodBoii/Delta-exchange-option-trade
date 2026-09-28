import base64
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg_pool import AsyncConnectionPool
from pywebpush import WebPushException, webpush
from requests import Response

from app.push import PushMessage, PushNotifier, PushSubscriptionIn, leg_label, legs_text, strategy_message

BACKEND = Path(__file__).resolve().parents[1]


def _row(**extra):
    return {
        "user_id": "user-1",
        "definition_json": {"name": "Short Strangle", "instrument": {"underlying": "ETH"}},
        **extra,
    }


def test_leg_label_reads_delta_option_symbols():
    assert leg_label("sell", "C-BTC-84000-280926") == "S C 84000"
    assert leg_label("buy", "P-ETH-2450.5-280926") == "B P 2450.5"
    assert leg_label("sell", "BTCUSD") == "S BTCUSD"
    assert leg_label(None, "C-BTC-84000-280926") == "C 84000"
    assert leg_label("sell", None) is None


def test_legs_text_keeps_order_and_drops_repeats():
    legs = [("sell", "C-BTC-84000-280926"), ("sell", "P-BTC-84400-280926"), ("sell", "C-BTC-84000-280926")]
    assert legs_text(legs) == "S C 84000, S P 84400"


def test_activation_message_names_asset_strategy_and_legs():
    message = strategy_message(
        event="activated",
        strategy_id="s1",
        row=_row(),
        legs=[("sell", "C-ETH-2600-280926"), ("sell", "P-ETH-2400-280926")],
    )
    assert message.title == "ETH Short Strangle activated"
    assert message.body == "S C 2600, S P 2400"
    assert message.tag == "strategy-s1"
    assert message.url == "/?tab=runs&strategy=s1"
    assert not message.urgent


def test_closed_message_shows_exit_reason_and_pnl():
    message = strategy_message(
        event="closed",
        strategy_id="s1",
        row=_row(risk_state={"exitReason": "stop_loss"}, realized_pnl="-12.345"),
        legs=[("sell", "C-ETH-2600-280926")],
    )
    assert message.title == "ETH Short Strangle closed"
    assert message.body == "S C 2600 · Stop loss hit · P&L -12.35 USD"


def test_closed_message_falls_back_to_detail_and_positive_sign():
    message = strategy_message(
        event="closed", strategy_id="s1", row=_row(realized_pnl="4"), legs=[], detail="Settled at expiry"
    )
    assert message.body == "Settled at expiry · P&L +4.00 USD"


def test_failure_messages_are_urgent_and_trim_detail():
    message = strategy_message(event="exit_failed", strategy_id="s1", row=_row(), legs=[], detail="x" * 500)
    assert message.urgent
    assert message.title == "ETH Short Strangle exit needs attention"
    assert len(message.body) == 120


def test_message_survives_missing_definition():
    message = strategy_message(event="entry_rejected", strategy_id="s1", row={"user_id": "u"}, legs=[])
    assert message.title == "Strategy entry not placed"
    assert message.body == "Open the app for details"


def test_generated_vapid_key_encrypts_a_real_payload():
    output = subprocess.run(
        [sys.executable, "scripts/generate_vapid_keys.py"], cwd=BACKEND, capture_output=True, text=True, check=True
    ).stdout
    keys = dict(line.split("=", 1) for line in output.strip().splitlines())
    browser = ec.generate_private_key(ec.SECP256R1())
    p256dh = browser.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test",
        "keys": {
            "p256dh": base64.urlsafe_b64encode(p256dh).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode(),
        },
    }
    # curl=True builds the signed, encrypted request without sending it.
    request = webpush(
        subscription,
        data=PushMessage(title="t", body="b", tag="x").payload(),
        vapid_private_key=keys["VAPID_PRIVATE_KEY"],
        vapid_claims={"sub": "mailto:test@example.com"},
        curl=True,
    )
    assert "vapid t=" in request
    assert f"k={keys['VAPID_PUBLIC_KEY']}" in request


def test_disabled_notifier_drops_spawned_work():
    notifier = PushNotifier(None, public_key=None, private_key=None, subject="mailto:x")  # type: ignore[arg-type]
    ran = []

    async def work():
        ran.append(True)

    notifier.spawn(work(), name="noop")
    assert not notifier.enabled
    assert ran == []


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL")
async def test_send_delivers_to_each_device_and_forgets_gone_ones():
    from scripts.init_local_db import apply_migrations

    url = os.environ["TEST_LOCAL_DATABASE_URL"]
    apply_migrations(url)
    user_id = f"push-{uuid4()}"
    sent: list[str] = []

    async def sender(subscription, message):
        if subscription["endpoint"].endswith("gone"):
            response = Response()
            response.status_code = 410
            raise WebPushException("gone", response=response)
        sent.append(subscription["endpoint"])

    async with AsyncConnectionPool(url, open=False) as pool:
        notifier = PushNotifier(pool, public_key="pub", private_key="priv", subject="mailto:x", sender=sender)
        for suffix in ("live", "gone"):
            await notifier.save(
                user_id,
                PushSubscriptionIn.model_validate(
                    {"endpoint": f"https://push.example/{user_id}/{suffix}", "keys": {"p256dh": "k", "auth": "a"}}
                ),
            )
        delivered = await notifier.send(user_id, PushMessage(title="t", body="b", tag="x"))
        assert delivered == 1
        assert sent == [f"https://push.example/{user_id}/live"]
        remaining = await notifier._subscriptions(user_id)
        assert [item["endpoint"] for item in remaining] == [f"https://push.example/{user_id}/live"]
        await notifier.remove(user_id, f"https://push.example/{user_id}/live")
        assert await notifier._subscriptions(user_id) == []
