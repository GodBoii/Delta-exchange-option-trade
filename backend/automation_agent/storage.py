from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from news_agent.config import NewsAgentSettings


@dataclass(frozen=True, slots=True)
class ChartArtifact:
    id: str
    label: str
    alt_text: str
    content: bytes
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredChart:
    id: str
    label: str
    alt_text: str
    bucket: str
    path: str
    signed_url: str

    def stored_metadata(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "altText": self.alt_text,
            "bucket": self.bucket,
            "path": self.path,
        }


class SupabaseChartStorage:
    def __init__(self, settings: NewsAgentSettings, transport: httpx.BaseTransport | None = None) -> None:
        self.supabase_url, service_key = settings.require_storage()
        self.bucket = settings.automation_chart_bucket
        self.expires_in = settings.chart_signed_url_seconds
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        }
        self.transport = transport

    def upload_run_charts(
        self,
        *,
        user_id: str,
        agent_run_id: str,
        charts: list[ChartArtifact],
    ) -> list[StoredChart]:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=5),
            headers=self.headers,
            transport=self.transport,
        ) as client:
            self._ensure_bucket(client)
            stored = []
            for chart in charts:
                path = f"{user_id}/{agent_run_id}/{chart.id}.png"
                encoded_path = quote(path, safe="/")
                response = client.post(
                    f"{self.supabase_url}/storage/v1/object/{self.bucket}/{encoded_path}",
                    headers={"Content-Type": "image/png", "x-upsert": "true"},
                    content=chart.content,
                )
                response.raise_for_status()
                signed_url = self._signed_url(client, path)
                stored.append(
                    StoredChart(
                        id=chart.id,
                        label=chart.label,
                        alt_text=chart.alt_text,
                        bucket=self.bucket,
                        path=path,
                        signed_url=signed_url,
                    )
                )
            return stored

    def _ensure_bucket(self, client: httpx.Client) -> None:
        response = client.get(f"{self.supabase_url}/storage/v1/bucket/{self.bucket}")
        if response.status_code == 200:
            return
        try:
            error_code = response.json().get("code")
        except ValueError:
            error_code = None
        if response.status_code != 404 and error_code != "NoSuchBucket":
            response.raise_for_status()
        created = client.post(
            f"{self.supabase_url}/storage/v1/bucket",
            json={
                "id": self.bucket,
                "name": self.bucket,
                "public": False,
                "file_size_limit": 5_242_880,
                "allowed_mime_types": ["image/png"],
            },
        )
        if created.status_code not in {200, 201, 409}:
            created.raise_for_status()

    def _signed_url(self, client: httpx.Client, path: str) -> str:
        encoded_path = quote(path, safe="/")
        response = client.post(
            f"{self.supabase_url}/storage/v1/object/sign/{self.bucket}/{encoded_path}",
            json={"expiresIn": self.expires_in},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        signed = str(payload.get("signedURL") or payload.get("signedUrl") or "")
        if not signed:
            raise RuntimeError("Supabase Storage returned no signed chart URL")
        if signed.startswith("http://") or signed.startswith("https://"):
            return signed
        if not signed.startswith("/"):
            signed = f"/{signed}"
        return f"{self.supabase_url}/storage/v1{signed}"
