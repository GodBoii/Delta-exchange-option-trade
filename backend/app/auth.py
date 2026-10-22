import logging
from typing import Any

from fastapi import Header, Request

from .config import Settings
from .credential_store import account_store
from .database import Database
from .delta import DeltaClient
from .errors import AppError

logger = logging.getLogger(__name__)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


async def optional_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any] | None:
    token = bearer_token(authorization)
    if not token:
        return None
    db: Database = request.app.state.db
    user = await db.auth_user(token)
    if user:
        request.state.user_id = str(user["id"])
        request.state.session_id = user.get("session_id")
    return user


async def require_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = await optional_user(request, authorization)
    if not user:
        logger.info("Application authentication rejected route=%s token_present=%s",
                    getattr(request.scope.get("route"), "path", "unknown"), bool(bearer_token(authorization)))
        raise AppError(401, "Sign in to continue", "not_authenticated")
    return user


async def require_owner(db: Database, user: dict[str, Any], *, max_age: float | None = None) -> None:
    user_id = str(user["id"])
    # Owner reporting passes a short max_age so a role change applies within seconds.
    profile = await (db.profile(user_id, max_age=max_age) if max_age is not None else db.profile(user_id))
    if profile.get("user_type") != "owner":
        raise AppError(403, "Only the owner can perform this action", "owner_required")


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    name, domain = email.split("@", 1)
    return f"{name[:2]}{'*' * max(2, len(name) - 2)}@{domain}"


def user_name(user: dict[str, Any]) -> str:
    metadata = user.get("user_metadata") or {}
    email = str(user.get("email") or "")
    return str(metadata.get("full_name") or metadata.get("name") or email.split("@")[0] or "Client")


async def current_account(
    db: Database, user: dict[str, Any] | None, *, required: bool = True
) -> dict[str, Any] | None:
    if not user:
        if required:
            raise AppError(401, "Sign in to continue", "not_authenticated")
        return None
    user_id = str(user["id"])
    overview = await account_store(db).data.request("accounts:overview", {"userId": user_id})
    connection = overview["connection"]
    profile = await db.profile(user_id)
    if (not connection or connection.get("status") != "connected") and required:
        raise AppError(401, "Connect Delta Exchange to continue", "delta_not_connected")
    metadata = user.get("user_metadata") or {}
    return {
        "id": user_id,
        "connection_id": connection.get("id") if connection and connection.get("status") == "connected" else None,
        "delta_user_id": connection.get("delta_user_id") if connection else None,
        "account_name": connection.get("account_name") if connection else None,
        "email_masked": connection.get("email_masked") if connection else None,
        "environment": "production",
        "status": connection.get("status") if connection else None,
        "app_email": user.get("email"),
        "display_name": profile.get("display_name") or user_name(user),
        "user_type": profile.get("user_type", "user"),
        "phone_number": profile.get("phone_number"),
        "avatar_url": profile.get("avatar_url") or metadata.get("avatar_url"),
    }


async def credentials_for_user(db: Database, user_id: str) -> dict[str, str]:
    return await account_store(db).read(user_id)


async def delta_client_for_user(db: Database, settings: Settings, user_id: str) -> DeltaClient:
    credentials = await credentials_for_user(db, user_id)
    return DeltaClient(settings, credentials["api_key"], credentials["api_secret"])


async def create_connection(
    db: Database, settings: Settings, user_id: str, api_key: str, api_secret: str
) -> dict[str, Any]:
    client = DeltaClient(settings, api_key.strip(), api_secret.strip())
    try:
        profile = (await client.profile()).get("result")
        if not isinstance(profile, dict) or not profile.get("id"):
            raise AppError(502, "Delta account identity could not be verified", "invalid_delta_response")
        # Establish required read access before replacing any saved credential.
        # These probes do not prove order-placement permission and never place an order.
        for response in (await client.balances(), await client.positions(), await client.open_orders()):
            if not isinstance(response.get("result"), list):
                raise AppError(502, "Delta account read access could not be verified", "delta_access_unknown")
    finally:
        await client.close()
    store = account_store(db)
    existing = await store.data.request("accounts:overview", {"userId": user_id})
    connection = existing["connection"]
    if connection and connection["delta_user_id"] != str(profile["id"]):
        await guard_subaccount_connections(db, user_id)
        active = await db.select(
            "strategies",
            {
                "select": "id",
                "user_id": f"eq.{user_id}",
                "status": "in.(active,executing_entry,executing_exit,attention)",
                "limit": "1",
            },
        )
        if active:
            raise AppError(
                409, "Resolve existing strategy runs before switching exchange accounts", "account_switch_blocked"
            )
    connection_id = await store.save(
        user_id,
        api_key.strip(),
        api_secret.strip(),
        {
            **profile,
            "email_masked": mask_email(profile.get("email")),
        },
    )
    logger.info("Delta connection saved user_id=%s delta_user_id=%s", user_id, profile["id"])
    return {
        "connectionId": str(connection_id),
        "account": {
            "id": str(profile["id"]),
            "accountName": profile.get("account_name") or "Main",
            "email": mask_email(profile.get("email")),
            "environment": "production",
        },
    }


async def remove_connection(db: Database, user_id: str) -> None:
    await guard_subaccount_connections(db, user_id)
    await account_store(db).data.request("accounts:revoke", {"userId": user_id}, mutation=True)
    logger.info("Delta connection revoked user_id=%s", user_id)


async def guard_subaccount_connections(db: Database, user_id: str) -> None:
    if not isinstance(db, Database):
        return
    async with db.pool.connection() as connection:
        result = await connection.execute(
            """select 1 from trade.account_assignments where owner_id=%s and state<>'released'
               union all select 1 from trade.account_transfers where owner_id=%s
               and state in ('prepared','submitting','unknown') limit 1""", (user_id, user_id),
        )
        if await result.fetchone():
            raise AppError(409, "Finish subaccount runs and resolve transfers before disconnecting or switching",
                           "subaccount_busy")
