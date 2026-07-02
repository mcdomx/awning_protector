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
    assert client.deployed_seconds() == float("inf")


@pytest.mark.asyncio
@respx.mock
async def test_undeploy_success(client):
    respx.get(f"{BASE}/awning/undeploy").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    ok = await client.undeploy()
    assert ok is True
    assert client.current_state == "undeployed"
    assert client.deployed_seconds() == 0.0


@pytest.mark.asyncio
@respx.mock
async def test_deploy_failure(client):
    respx.get(f"{BASE}/awning/deploy").mock(return_value=httpx.Response(500))
    ok = await client.deploy()
    assert ok is False
    assert client.current_state == "unknown"


def test_deploy_delta_seconds_starts_at_zero(client):
    assert client.deploy_delta_seconds(10) == 10


def test_deploy_delta_seconds_no_op_once_target_reached(client):
    """Regression: re-deploying to the same target must not re-run the motor.

    The awning has no auto-retract and its extension doesn't decay with time, so
    asking for the same target seconds later (as a periodic AI re-evaluation
    would) must not stack additional motor travel on top.
    """
    client.record_deploy_extension(10)
    assert client.deploy_delta_seconds(10) is None


def test_deploy_delta_seconds_only_sends_remaining_delta(client):
    client.record_deploy_extension(4)
    assert client.deploy_delta_seconds(10) == 6


def test_record_partial_retract_reduces_extension(client):
    client.record_deploy_extension(10)
    client.record_partial_retract(4)
    assert client.deployed_seconds() == 6
    assert client.current_state == "deployed"


def test_record_partial_retract_to_zero_marks_undeployed(client):
    client.record_deploy_extension(5)
    client.record_partial_retract(5)
    assert client.deployed_seconds() == 0
    assert client.current_state == "undeployed"


def test_record_full_retract_resets_extension(client):
    client.record_deploy_extension(10)
    client.record_full_retract()
    assert client.deployed_seconds() == 0.0
    assert client.current_state == "undeployed"
