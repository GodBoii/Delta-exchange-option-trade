import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from composio_client import ComposioClient
from composio_tools import (
    ComposioFacebookTools,
    ComposioInstagramTools,
    ComposioYouTubeTools,
    has_active_composio_connection,
)


class _Response:
    ok = True
    status_code = 200
    text = '{"items": []}'

    def json(self):
        return {"items": []}


def test_social_toolkits_expose_provider_specific_functions():
    facebook = ComposioFacebookTools("user-1")
    instagram = ComposioInstagramTools("user-1")
    youtube = ComposioYouTubeTools("user-1")

    assert facebook.TOOLKIT_SLUG == "FACEBOOK"
    assert instagram.TOOLKIT_SLUG == "INSTAGRAM"
    assert youtube.TOOLKIT_SLUG == "YOUTUBE"
    assert callable(facebook.list_facebook_actions)
    assert callable(instagram.execute_instagram_action)
    assert callable(youtube.execute_youtube_action)


def test_social_toolkit_requires_listing_and_rejects_cross_toolkit_slug():
    toolkit = ComposioFacebookTools("user-1")
    assert toolkit.execute_facebook_action("FACEBOOK_CREATE_POST").startswith(
        "Error: call list_facebook_actions() first"
    )

    toolkit._actions_listed = True
    toolkit._action_slug_set = {"INSTAGRAM_POST_IG_USER_MEDIA"}
    result = toolkit.execute_facebook_action("INSTAGRAM_POST_IG_USER_MEDIA")
    assert result == "Error: action slug must start with 'FACEBOOK_'."


def test_social_toolkit_executes_with_active_user_account():
    toolkit = ComposioYouTubeTools("user-1")
    client = MagicMock()
    client.list_tools.return_value = [
        {"slug": "YOUTUBE_SEARCH_YOU_TUBE", "description": "Search YouTube"}
    ]
    client.list_connected_accounts.return_value = [{"id": "ca-1", "status": "ACTIVE"}]
    client.execute_tool.return_value = {"successful": True, "data": {"items": []}}
    toolkit._client = client

    assert "YOUTUBE_SEARCH_YOU_TUBE" in toolkit.list_youtube_actions()
    result = json.loads(
        toolkit.execute_youtube_action(
            "YOUTUBE_SEARCH_YOU_TUBE",
            '{"query": "Aetheria"}',
        )
    )

    assert result["successful"] is True
    client.execute_tool.assert_called_once_with(
        tool_slug="YOUTUBE_SEARCH_YOU_TUBE",
        connected_account_id="ca-1",
        user_id="user-1",
        arguments={"query": "Aetheria"},
    )


def test_generic_connection_check_filters_by_user_toolkit_and_active_status():
    with patch("composio_tools.ComposioClient") as client_class:
        client_class.return_value.list_connected_accounts.return_value = [{"id": "ca-1"}]
        assert has_active_composio_connection("user-1", "INSTAGRAM") is True
        client_class.return_value.list_connected_accounts.assert_called_once_with(
            user_id="user-1",
            toolkit_slug="INSTAGRAM",
            statuses=["ACTIVE"],
        )


def test_tools_catalog_uses_v31_and_provider_filter():
    client = ComposioClient(api_key="test-key")
    with patch("composio_client.requests.request", return_value=_Response()) as request:
        client.list_tools("FACEBOOK")

    kwargs = request.call_args.kwargs
    assert kwargs["url"].endswith("/api/v3.1/tools")
    assert kwargs["params"]["toolkit_slug"] == "FACEBOOK"
    assert kwargs["params"]["toolkit_versions"] == "latest"
