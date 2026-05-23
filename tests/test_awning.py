import pytest
import respx
import httpx

from app.awning import AwningClient


BASE = "http://localhost:8765"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWNING_URL", BASE)
    import app.awning as mod
    mod.AWNING_URL = BASE
    return AwningClient()


@pytest.mark.asyncio
@respx.mock
async def test_deploy_success(client):
    respx.get(f"{BASE}/awning/deploy").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    ok = await client.deploy()
    assert ok is True
    assert client.current_state == "deployed"


@pytest.mark.asyncio
@respx.mock
async def test_undeploy_success(client):
    respx.get(f"{BASE}/awning/undeploy").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    ok = await client.undeploy()
    assert ok is True
    assert client.current_state == "undeployed"


@pytest.mark.asyncio
@respx.mock
async def test_deploy_failure(client):
    respx.get(f"{BASE}/awning/deploy").mock(return_value=httpx.Response(500))
    ok = await client.deploy()
    assert ok is False
    assert client.current_state == "unknown"
