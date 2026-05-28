"""Test ChatClient custom headers support."""
import pytest
from fy_loadtest.client import ChatClient


@pytest.mark.asyncio
async def test_custom_headers_passed_to_request():
    """Custom headers should be merged into request headers."""
    client = ChatClient(
        base_url="http://localhost:3000",
        token="sk-test",
        extra_headers={"X-Session-Id": "test-session-123"}
    )
    assert "X-Session-Id" in client._client.headers
    assert client._client.headers["X-Session-Id"] == "test-session-123"
    await client.aclose()


@pytest.mark.asyncio
async def test_no_extra_headers_default():
    """Without extra_headers, only default headers present."""
    client = ChatClient(
        base_url="http://localhost:3000",
        token="sk-test",
    )
    assert "X-Session-Id" not in client._client.headers
    assert "authorization" in client._client.headers
    await client.aclose()
