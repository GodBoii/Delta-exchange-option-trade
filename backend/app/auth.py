from typing import Any

from fastapi import Header, Request

from .config import Settings
from .delta import DeltaClient
from .errors import AppError
from .supabase import SupabaseAdmin


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


async def optional_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any] | None:
    token = bearer_token(authorization)
    if not token:
        return None
    db: SupabaseAdmin = request.app.state.db
    return await db.auth_user(token)


async def require_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = await optional_user(request, authorization)
    if not user:
        raise AppError(401, "Sign in to continue", "not_authenticated")
    return user


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
    db: SupabaseAdmin, user: dict[str, Any] | None, *, required: bool = True
) -> dict[str, Any] | None:
    if not user:
        if required:
            raise AppError(401, "Sign in to continue", "not_authenticated")
        return None
    user_id = str(user["id"])
    connections = await db.select(
        "exchange_connections",
        {
            "select": "id,delta_user_id,account_name,email_masked,environment,status",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    profiles = await db.select("profiles", {"select": "display_name,avatar_url", "id": f"eq.{user_id}", "limit": "1"})
    connection = connections[0] if connections else None
    profile = profiles[0] if profiles else {}
    if not connection and required:
        raise AppError(401, "Connect Delta Exchange to continue", "delta_not_connected")
    metadata = user.get("user_metadata") or {}
    return {
        "id": user_id,
        "connection_id": connection.get("id") if connection else None,
        "delta_user_id": connection.get("delta_user_id") if connection else None,
        "account_name": connection.get("account_name") if connection else None,
        "email_masked": connection.get("email_masked") if connection else None,
        "environment": "production",
        "status": connection.get("status") if connection else None,
        "app_email": user.get("email"),
        "display_name": profile.get("display_name") or user_name(user),
        "avatar_url": profile.get("avatar_url") or metadata.get("avatar_url"),
    }


async def credentials_for_user(db: SupabaseAdmin, user_id: str) -> dict[str, str]:
    result = await db.rpc("get_delta_credentials", {"p_user_id": user_id})
    row = result[0] if isinstance(result, list) and result else result if isinstance(result, dict) else None
    if not row or row.get("status") != "connected":
        raise AppError(401, "Connect Delta Exchange to continue", "delta_not_connected")
    return {"api_key": str(row["api_key"]), "api_secret": str(row["api_secret"])}


async def delta_client_for_user(db: SupabaseAdmin, settings: Settings, user_id: str) -> DeltaClient:
    credentials = await credentials_for_user(db, user_id)
    return DeltaClient(settings, credentials["api_key"], credentials["api_secret"])


async def create_connection(
    db: SupabaseAdmin, settings: Settings, user_id: str, api_key: str, api_secret: str
) -> dict[str, Any]:
    client = DeltaClient(settings, api_key.strip(), api_secret.strip())
    try:
        profile = (await client.profile())["result"]
    finally:
        await client.close()
    connection_id = await db.rpc(
        "store_delta_connection",
        {
            "p_user_id": user_id,
            "p_api_key": api_key.strip(),
            "p_api_secret": api_secret.strip(),
            "p_delta_user_id": str(profile["id"]),
            "p_account_name": profile.get("account_name") or "Main",
            "p_email_masked": mask_email(profile.get("email")),
        },
    )
    return {
        "connectionId": str(connection_id),
        "account": {
            "id": str(profile["id"]),
            "accountName": profile.get("account_name") or "Main",
            "email": mask_email(profile.get("email")),
            "environment": "production",
        },
    }
