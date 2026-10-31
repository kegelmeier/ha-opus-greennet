"""Config flow and setup for the two gateway generations.

A gateway before WebUI 2026 is found first, at the entered port, and stays
exactly as it was (its entry data carries neither a generation nor a
certificate). When nothing answers there, the flow looks for a WebUI 2026
gateway over HTTPS, remembers its certificate and logs in with the user the
person entered. A different certificate than the remembered one is shown and
needs a confirmation before anything is sent to it.

The network functions are replaced; the client itself is covered in
``test_gateway_generation_client.py``.
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.opus_greennet import async_setup_entry
from custom_components.opus_greennet.api import (
    OpusAuthError,
    OpusCertificateError,
    OpusConnectionError,
    OpusResponseError,
)
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.discovery import (
    GatewayEndpoint,
    async_detect_webui_2026,
    async_read_gateway_id,
)

EAG_ID = "AABB0011"
FINGERPRINT = "ab" * 32
OTHER_FINGERPRINT = "cd" * 32
ENDPOINT = GatewayEndpoint(443, FINGERPRINT)
CONFIG_FLOW = "custom_components.opus_greennet.config_flow"
PASSWORD = "S3cret-passw0rd"

USER_INPUT = {
    "host": "192.168.1.5",
    "port": 8080,
    "username": "installateur",
    "password": PASSWORD,
}
WEBUI_DATA = {
    "eag_id": EAG_ID,
    "host": "192.168.1.5",
    "port": 443,
    "username": "installateur",
    "password": "old-password",
    "generation": "webui2026",
    "certificate_fingerprint": FINGERPRINT,
}
LEGACY_DATA = {
    "eag_id": EAG_ID,
    "host": "192.168.1.5",
    "port": 8080,
    "username": "admin",
    "password": EAG_ID,
}


def _detect(endpoint: GatewayEndpoint | None):
    return patch(
        f"{CONFIG_FLOW}.async_detect_webui_2026", AsyncMock(return_value=endpoint)
    )


def _read(*outcomes: Any):
    """Replace the gateway read; an exception in ``outcomes`` is raised."""
    return patch(
        f"{CONFIG_FLOW}.async_read_gateway_id", AsyncMock(side_effect=outcomes)
    )


@pytest.fixture(autouse=True)
def _no_setup():
    """Creating or updating an entry must not start a real connection."""
    with (
        patch("custom_components.opus_greennet.async_setup_entry", return_value=True),
        patch("custom_components.opus_greennet.async_unload_entry", return_value=True),
    ):
        yield


async def _add(hass: HomeAssistant, data: dict[str, Any]) -> ConfigEntry:
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({EAG_ID})",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=EAG_ID,
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    return entry


async def _user(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def _confirm_first_contact(
    hass: HomeAssistant, result: dict[str, Any], read: Any = None
) -> dict[str, Any]:
    """Check the first-contact question, then confirm the certificate."""
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "certificate_new"
    placeholders = result["description_placeholders"]
    assert placeholders["fingerprint"] == ":".join(["AB"] * 32)
    assert placeholders["host"] == "192.168.1.5"
    if read is not None:
        # Nothing was sent to the gateway before the user confirmed.
        read.assert_not_awaited()
    return await hass.config_entries.flow.async_configure(result["flow_id"], {})


async def _reauth(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    return flow


# ── adding a gateway ───────────────────────────────────────────────────────


async def test_a_new_generation_gateway_is_found_when_the_old_port_is_closed(hass):
    with _detect(ENDPOINT), _read(EAG_ID) as read:
        result = await _user(hass, USER_INPUT)
        result = await _confirm_first_contact(hass, result, read)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "eag_id": EAG_ID,
        "host": "192.168.1.5",
        "port": 443,
        "username": "installateur",
        "password": PASSWORD,
        "generation": "webui2026",
        "certificate_fingerprint": FINGERPRINT,
    }
    # The entered port was asked without credentials first; the only read
    # already uses HTTPS and the remembered certificate.
    (call,) = read.await_args_list
    assert (
        call.args[1]["port"],
        call.args[1]["generation"],
        call.args[1]["certificate_fingerprint"],
    ) == (
        443,
        "webui2026",
        FINGERPRINT,
    )


async def test_an_earlier_generation_gateway_keeps_its_entry_data(hass):
    with _detect(None), _read(EAG_ID) as read:
        result = await _user(hass, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "eag_id": EAG_ID,
        "host": "192.168.1.5",
        "port": 8080,
        "username": "installateur",
        "password": PASSWORD,
    }
    assert read.await_count == 1


async def test_the_https_port_is_never_asked_over_plain_http(hass):
    with _detect(ENDPOINT), _read(EAG_ID) as read:
        result = await _user(hass, {**USER_INPUT, "port": 443})
        result = await _confirm_first_contact(hass, result, read)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (call,) = read.await_args_list
    assert call.args[1]["generation"] == "webui2026"


async def test_a_tls_service_at_another_port_never_gets_the_credentials_in_clear(hass):
    """A gateway of the new generation on a port of the user's choice."""
    endpoint = GatewayEndpoint(8443, FINGERPRINT)
    with _detect(endpoint), _read(EAG_ID) as read:
        result = await _user(hass, {**USER_INPUT, "port": 8443})
        result = await _confirm_first_contact(hass, result, read)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["generation"] == "webui2026"
    assert result["data"]["port"] == 8443
    # Only the read over HTTPS was made: no plain-HTTP request carried the
    # user name and password.
    (call,) = read.await_args_list
    assert call.args[1]["generation"] == "webui2026"


async def test_another_port_without_tls_is_still_asked_as_an_earlier_gateway(hass):
    with _detect(None), _read(EAG_ID) as read:
        result = await _user(hass, {**USER_INPUT, "port": 9090})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == 9090
    assert "generation" not in result["data"]
    (call,) = read.await_args_list
    assert "generation" not in call.args[1]


async def test_nothing_found_at_either_port_cannot_connect(hass):
    with _detect(None), _read(OpusConnectionError("refused")):
        result = await _user(hass, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_nothing_answering_on_the_https_port_cannot_connect(hass):
    with _detect(None), _read(EAG_ID) as read:
        result = await _user(hass, {**USER_INPUT, "port": 443})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    # The HTTPS port is never asked over plain HTTP.
    read.assert_not_awaited()


async def test_a_wrong_password_on_a_new_generation_gateway(hass):
    with _detect(ENDPOINT), _read(OpusAuthError("x")) as read:
        result = await _user(hass, USER_INPUT)
        result = await _confirm_first_contact(hass, result, read)

    assert result["errors"] == {"base": "invalid_auth"}
    # The only read went over HTTPS: nothing was sent to port 8080.
    (call,) = read.await_args_list
    assert call.args[1]["generation"] == "webui2026"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (OpusCertificateError("changed"), "cannot_connect"),
        (OpusConnectionError("reset"), "cannot_connect"),
        (OpusResponseError("not a gateway"), "unsupported_gateway"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_other_failures_of_a_new_generation_gateway(hass, error, key):
    with _detect(ENDPOINT), _read(error):
        result = await _user(hass, USER_INPUT)
        result = await _confirm_first_contact(hass, result)

    assert result["errors"] == {"base": key}


async def test_the_default_port_is_asked_without_credentials_before_basic_auth(hass):
    """A gateway that was updated must never get its new password over HTTP."""
    order: list[str] = []

    async def detect(*args: Any) -> GatewayEndpoint | None:
        order.append("detect")
        return None

    async def read(*args: Any) -> str:
        order.append("read")
        return EAG_ID

    with (
        patch(f"{CONFIG_FLOW}.async_detect_webui_2026", detect),
        patch(f"{CONFIG_FLOW}.async_read_gateway_id", read),
    ):
        result = await _user(hass, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert order == ["detect", "read"]


async def test_a_wrong_password_on_the_old_port_is_not_looked_up_again(hass):
    """A gateway that answered but refused the login is not searched again."""
    detect = AsyncMock(return_value=None)
    with (
        patch(f"{CONFIG_FLOW}.async_detect_webui_2026", detect),
        _read(OpusAuthError("x")),
    ):
        result = await _user(hass, USER_INPUT)

    assert result["errors"] == {"base": "invalid_auth"}
    detect.assert_awaited_once()


# ── reauthentication and reconfiguration ───────────────────────────────────


async def test_reauth_of_a_new_generation_gateway_with_the_same_certificate(hass):
    entry = await _add(hass, dict(WEBUI_DATA))
    flow = await _reauth(hass, entry)

    with _detect(ENDPOINT), _read(EAG_ID) as read:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {**USER_INPUT, "port": 443, "password": "new-password"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-password"
    assert entry.data["certificate_fingerprint"] == FINGERPRINT
    # Never asked over plain HTTP: one read, over HTTPS.
    (call,) = read.await_args_list
    assert call.args[1]["generation"] == "webui2026"


async def test_a_changed_certificate_is_confirmed_before_anything_is_sent(hass):
    entry = await _add(hass, dict(WEBUI_DATA))
    flow = await _reauth(hass, entry)
    changed = GatewayEndpoint(443, OTHER_FINGERPRINT)

    with _detect(changed), _read(EAG_ID) as read:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {**USER_INPUT, "port": 443, "password": "new-password"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "certificate"
        placeholder = result["description_placeholders"]["fingerprint"]
        assert placeholder == ":".join(["CD"] * 32)
        # No credentials went to the other certificate.
        read.assert_not_awaited()
        assert entry.data["certificate_fingerprint"] == FINGERPRINT

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["certificate_fingerprint"] == OTHER_FINGERPRINT
    assert entry.data["password"] == "new-password"
    (call,) = read.await_args_list
    assert call.args[1]["certificate_fingerprint"] == OTHER_FINGERPRINT


async def test_an_updated_gateway_asks_for_its_new_login_and_keeps_its_data(hass):
    entry = await _add(hass, dict(LEGACY_DATA))
    flow = await _reauth(hass, entry)

    # The earlier entry asks for 8080: the new password goes only to HTTPS.
    with _detect(ENDPOINT), _read(EAG_ID) as read:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {**USER_INPUT, "password": "new-password"}
        )
        # The certificate of the updated gateway is new to the entry: it is
        # confirmed before the new password is sent.
        result = await _confirm_first_contact(hass, result, read)

    (call,) = read.await_args_list
    assert call.args[1]["generation"] == "webui2026"

    assert result["type"] is FlowResultType.ABORT
    assert dict(entry.data) == {
        "eag_id": EAG_ID,
        "host": "192.168.1.5",
        "port": 443,
        "username": "installateur",
        "password": "new-password",
        "generation": "webui2026",
        "certificate_fingerprint": FINGERPRINT,
    }
    assert entry.unique_id == EAG_ID


async def test_a_different_gateway_is_rejected_on_the_new_generation_too(hass):
    entry = await _add(hass, dict(WEBUI_DATA))
    flow = await _reauth(hass, entry)

    with _detect(ENDPOINT), _read("11223344"):
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {**USER_INPUT, "port": 443}
        )

    assert result["errors"] == {"base": "wrong_gateway"}
    assert entry.data["password"] == "old-password"


async def test_reconfiguring_to_an_earlier_generation_gateway_drops_the_certificate(
    hass,
):
    entry = await _add(hass, dict(WEBUI_DATA))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    with _detect(None), _read(EAG_ID):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, "port": 8080, "username": "admin", "password": EAG_ID},
        )

    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {**LEGACY_DATA, "port": 8080}


# ── setup ──────────────────────────────────────────────────────────────────


def _setup_entry(data: dict[str, Any]) -> ConfigEntry:
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({EAG_ID})",
        data=data,
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=EAG_ID,
        discovery_keys={},
        subentries_data=None,
    )


async def test_setup_builds_the_client_of_the_entry_generation(hass):
    created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def client(*args: Any, **kwargs: Any) -> MagicMock:
        created.append((args[1:], kwargs))
        mock = MagicMock()
        mock.async_get = AsyncMock(side_effect=OpusConnectionError("down"))
        return mock

    with (
        patch("custom_components.opus_greennet.OpusGatewayClient", client),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, _setup_entry(dict(WEBUI_DATA)))

    assert created == [
        (
            ("192.168.1.5", 443, "installateur", "old-password"),
            {"generation": "webui2026", "fingerprint": FINGERPRINT},
        )
    ]


async def test_setup_of_an_earlier_generation_entry_is_unchanged(hass):
    created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def client(*args: Any, **kwargs: Any) -> MagicMock:
        created.append((args[1:], kwargs))
        mock = MagicMock()
        mock.async_get = AsyncMock(side_effect=OpusConnectionError("down"))
        return mock

    with (
        patch("custom_components.opus_greennet.OpusGatewayClient", client),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, _setup_entry(dict(LEGACY_DATA)))

    assert created == [(("192.168.1.5", 8080, "admin", EAG_ID), {})]


async def test_a_gateway_that_moved_to_the_new_generation_asks_for_a_login(hass):
    client = MagicMock()
    client.async_get = AsyncMock(side_effect=OpusConnectionError("refused"))
    detect = AsyncMock(return_value=ENDPOINT)

    with (
        patch(
            "custom_components.opus_greennet.OpusGatewayClient", lambda *a, **k: client
        ),
        patch("custom_components.opus_greennet.async_detect_webui_2026", detect),
        pytest.raises(ConfigEntryAuthFailed) as raised,
    ):
        await async_setup_entry(hass, _setup_entry(dict(LEGACY_DATA)))

    assert raised.value.translation_key == "webui_2026_login_required"
    detect.assert_awaited_once_with(hass, "192.168.1.5", 8080)


async def test_a_gateway_that_is_only_down_is_retried(hass):
    client = MagicMock()
    client.async_get = AsyncMock(side_effect=OpusConnectionError("refused"))

    with (
        patch(
            "custom_components.opus_greennet.OpusGatewayClient", lambda *a, **k: client
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, _setup_entry(dict(LEGACY_DATA)))


async def test_a_new_generation_gateway_is_not_searched_again_when_it_is_down(hass):
    client = MagicMock()
    client.async_get = AsyncMock(side_effect=OpusConnectionError("refused"))
    detect = AsyncMock(return_value=ENDPOINT)

    with (
        patch(
            "custom_components.opus_greennet.OpusGatewayClient", lambda *a, **k: client
        ),
        patch("custom_components.opus_greennet.async_detect_webui_2026", detect),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, _setup_entry(dict(WEBUI_DATA)))

    detect.assert_not_awaited()


async def test_a_changed_certificate_at_setup_asks_to_authenticate_again(hass):
    client = MagicMock()
    client.async_get = AsyncMock(side_effect=OpusCertificateError("changed"))

    with (
        patch(
            "custom_components.opus_greennet.OpusGatewayClient", lambda *a, **k: client
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, _setup_entry(dict(WEBUI_DATA)))


# ── recognising the generation ─────────────────────────────────────────────

DISCOVERY = "custom_components.opus_greennet.discovery"


async def _detect_with(hass, port: int, *, probe=True, fingerprint=FINGERPRINT):
    probe_mock = AsyncMock(return_value=probe)
    if isinstance(fingerprint, BaseException):
        read_mock = AsyncMock(side_effect=fingerprint)
    else:
        read_mock = AsyncMock(return_value=fingerprint)
    with (
        patch(f"{DISCOVERY}.async_get_clientsession", return_value=MagicMock()),
        patch(f"{DISCOVERY}.async_probe_webui_2026", probe_mock),
        patch(f"{DISCOVERY}.async_read_certificate_fingerprint", read_mock),
    ):
        found = await async_detect_webui_2026(hass, "192.168.1.5", port)
    return found, probe_mock, read_mock


@pytest.mark.parametrize(("entered", "tried"), [(8080, 443), (443, 443), (8443, 8443)])
async def test_the_default_port_stands_for_not_chosen(hass, entered, tried):
    found, probe, read = await _detect_with(hass, entered)

    assert found == GatewayEndpoint(tried, FINGERPRINT)
    assert probe.await_args.args[1:] == ("192.168.1.5", tried)
    assert read.await_args.args == ("192.168.1.5", tried)


async def test_no_certificate_is_read_from_a_device_that_is_not_a_gateway(hass):
    found, _, read = await _detect_with(hass, 8080, probe=False)

    assert found is None
    read.assert_not_awaited()


@pytest.mark.parametrize("error", [OpusConnectionError("x"), OpusResponseError("x")])
async def test_a_failed_handshake_is_no_gateway(hass, error):
    found, _, _ = await _detect_with(hass, 8080, fingerprint=error)

    assert found is None


async def test_reading_the_identity_uses_the_generation_of_the_data(hass):
    created: list[dict[str, Any]] = []

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(kwargs)

        async def async_get(self, path: str) -> dict[str, Any]:
            return {"systemInfo": {"eurid": "aabb0011"}}

    data = {**USER_INPUT, "port": 443}
    with (
        patch(f"{DISCOVERY}.OpusGatewayClient", Client),
        patch(f"{DISCOVERY}.async_get_clientsession", return_value=MagicMock()),
    ):
        assert await async_read_gateway_id(hass, data) == "AABB0011"
        assert (
            await async_read_gateway_id(
                hass,
                {
                    **data,
                    "generation": "webui2026",
                    "certificate_fingerprint": FINGERPRINT,
                },
            )
            == "AABB0011"
        )

    assert created == [
        {"generation": "legacy", "fingerprint": None},
        {"generation": "webui2026", "fingerprint": FINGERPRINT},
    ]


# ── address change announced by zeroconf ───────────────────────────────────


def _announcement(host: str) -> ZeroconfServiceInfo:
    address = ip_address(host)
    return ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=80,
        hostname="opusshgw.local.",
        type="_enocean._tcp.local.",
        name="opusshgw._enocean._tcp.local.",
        properties={},
    )


async def _announce(hass: HomeAssistant, host: str) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_announcement(host),
    )


async def test_a_new_generation_entry_keeps_its_login_and_certificate_on_a_new_address(
    hass,
):
    entry = await _add(hass, WEBUI_DATA)

    with _read(EAG_ID) as read:
        result = await _announce(hass, "192.168.1.9")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The gateway is asked at the new address with what the entry stores: over
    # HTTPS, with its certificate and its own login.
    (call,) = read.await_args_list
    connection = {key: value for key, value in WEBUI_DATA.items() if key != "eag_id"}
    assert call.args[1] == {**connection, "host": "192.168.1.9"}
    assert entry.data == {**WEBUI_DATA, "host": "192.168.1.9"}


async def test_an_earlier_generation_entry_stays_one_on_a_new_address(hass):
    entry = await _add(hass, LEGACY_DATA)

    with _read(EAG_ID) as read:
        result = await _announce(hass, "192.168.1.9")

    assert result["reason"] == "already_configured"
    (call,) = read.await_args_list
    assert "generation" not in call.args[1]
    assert entry.data == {**LEGACY_DATA, "host": "192.168.1.9"}


async def test_a_gateway_with_another_eurid_does_not_move_a_new_generation_entry(
    hass,
):
    entry = await _add(hass, WEBUI_DATA)

    with _read("CCDD0022"):
        result = await _announce(hass, "192.168.1.9")

    assert result["type"] is FlowResultType.FORM
    assert entry.data == WEBUI_DATA


# ── adding a known gateway again ───────────────────────────────────────────


async def test_adding_a_known_gateway_again_with_the_same_certificate(hass):
    entry = await _add(hass, dict(WEBUI_DATA))

    with _detect(ENDPOINT), _read(EAG_ID):
        result = await _user(hass, {**USER_INPUT, "port": 443, "password": "newer"})

    # No question: the certificate is the remembered one.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["password"] == "newer"
    assert entry.data["certificate_fingerprint"] == FINGERPRINT


async def test_adding_a_known_gateway_again_confirms_a_changed_certificate(hass):
    entry = await _add(hass, dict(WEBUI_DATA))
    changed = GatewayEndpoint(443, OTHER_FINGERPRINT)

    with _detect(changed), _read(EAG_ID) as read:
        result = await _user(hass, {**USER_INPUT, "port": 443, "password": "newer"})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "certificate"
        assert result["description_placeholders"]["eag_id"] == EAG_ID
        # Neither sent to the other certificate nor replaced silently.
        read.assert_not_awaited()
        assert entry.data["certificate_fingerprint"] == FINGERPRINT

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["certificate_fingerprint"] == OTHER_FINGERPRINT
    assert entry.data["password"] == "newer"


async def test_a_known_new_generation_gateway_added_again_as_an_earlier_one(hass):
    """The old HTTPS port and certificate do not stay on an earlier gateway."""
    entry = await _add(hass, dict(WEBUI_DATA))

    with _detect(None), _read(EAG_ID):
        result = await _user(hass, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert dict(entry.data) == {
        "eag_id": EAG_ID,
        "host": "192.168.1.5",
        "port": 8080,
        "username": "installateur",
        "password": PASSWORD,
    }
