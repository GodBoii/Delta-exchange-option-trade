"""Routes the browser uses to turn phone notifications on and off."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from .auth import require_user
from .errors import AppError
from .push import PushMessage, PushNotifier, PushSubscriptionIn, PushSubscriptionOut

router = APIRouter(prefix="/api/push", tags=["push"])
RequiredUser = Annotated[dict[str, Any], Depends(require_user)]


def _notifier(request: Request) -> PushNotifier:
    notifier: PushNotifier = request.app.state.push
    if not notifier.enabled:
        raise AppError(503, "Phone notifications are not configured on the server", "push_disabled")
    return notifier


@router.get("/config")
async def push_config(request: Request, user: RequiredUser) -> dict[str, Any]:
    notifier: PushNotifier = request.app.state.push
    return {"success": True, "result": {"enabled": notifier.enabled, "publicKey": notifier.public_key}}


@router.post("/subscriptions")
async def push_subscribe(request: Request, body: PushSubscriptionIn, user: RequiredUser) -> dict[str, Any]:
    await _notifier(request).save(str(user["id"]), body)
    return {"success": True}


@router.delete("/subscriptions")
async def push_unsubscribe(request: Request, body: PushSubscriptionOut, user: RequiredUser) -> dict[str, Any]:
    await _notifier(request).remove(str(user["id"]), body.endpoint)
    return {"success": True}


@router.post("/test")
async def push_test(request: Request, user: RequiredUser) -> dict[str, Any]:
    delivered = await _notifier(request).send(
        str(user["id"]),
        PushMessage(title="Notifications are on", body="Trade alerts will show up here.", tag="push-test"),
    )
    if delivered == 0:
        raise AppError(404, "No device accepted the test notification", "push_not_delivered")
    return {"success": True, "result": {"delivered": delivered}}
