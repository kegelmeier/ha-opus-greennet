"""The gateway has no TLS: its credentials go only to the local network.

Covers the address allowlist, the config flow (user, reauth, reconfigure), the
zeroconf announcement, the mDNS search and the warning for entries that were
configured with a public address before the rule existed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryNotReady
from zeroconf import IPVersion, ServiceStateChange

from custom_components.opus_greennet import async_setup_entry
from custom_components.opus_greennet.api import OpusConnectionError
from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.discovery import (
    LOCAL_NETWORKS,
    ZEROCONF_TYPE,
    async_browse_gateway_hosts,
    async_local_networks,
    async_resolve_host,
    is_local_address,
    parse_host_literal,
)
from tests.fake_gateway import FakeGateway
from tests.ha_helpers import setup_bridge
from tests.test_config_flow import _entry_with_legacy_mqtt_data
from tests.test_discovery import _announcement
from tests.test_init import ENTRY_DATA, _mock_setup

CONFIG_FLOW = "custom_components.opus_greennet.config_flow"
DISCOVERY = "custom_components.opus_greennet.discovery"

LOCAL = [
    "10.1.2.3",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.1.10",
    "169.254.1.1",
    "127.0.0.1",
    "100.64.0.1",
    "100.127.255.254",
    "::1",
    "fe80::1",
    "fe80::1%eth0",
    "fd00::1",
    "fc12::5",
    "::ffff:192.168.1.5",
    "::ffff:10.0.0.1",
]
NOT_LOCAL = [
    "8.8.8.8",
    # Documentation ranges are no local network.
    "192.0.2.10",
    "198.51.100.7",
    "203.0.113.9",
    "1.1.1.1",
    "172.15.255.255",
    "172.32.0.1",
    "100.63.255.255",
    "100.128.0.1",
    "192.169.0.1",
    "11.0.0.1",
    "0.0.0.0",
    "0.1.2.3",
    "255.255.255.255",
    "224.0.0.1",
    "240.0.0.1",
    "169.253.0.1",
    "2001:4860:4860::8888",
    "2002:808:808::",
    "2001::1",
    "2001:db8::1",
    "::",
    "ff02::1",
    "fec0::1",
    "64:ff9b::808:808",
    "::ffff:8.8.8.8",
    "::192.168.1.1",
    "fb00::1",
    "fe00::1",
]


def _lookup(*addresses: str) -> list[tuple[object, ...]]:
    return [(2, 1, 6, "", (address, 0)) for address in addresses]


# ── the allowlist ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("address", LOCAL)
def test_local_addresses_are_allowed(address: str) -> None:
    assert is_local_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", NOT_LOCAL)
def test_every_other_address_is_refused(address: str) -> None:
    assert not is_local_address(ipaddress.ip_address(address))


def test_host_literals_are_parsed_like_the_config_flow_reads_them() -> None:
    assert parse_host_literal("[::1]") == ipaddress.ip_address("::1")
    assert parse_host_literal(" 10.0.0.1 ") == ipaddress.ip_address("10.0.0.1")
    assert parse_host_literal("opusshgw.local") is None
    assert parse_host_literal("") is None


# ── resolving a host name ───────────────────────────────────────────────────


async def test_an_ip_address_is_not_looked_up(hass: HomeAssistant) -> None:
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", AsyncMock(side_effect=AssertionError)):
        assert await async_resolve_host(hass, "[::1]") == [ipaddress.ip_address("::1")]


async def test_a_name_resolves_to_all_of_its_addresses(hass: HomeAssistant) -> None:
    loop = asyncio.get_running_loop()
    found = _lookup("10.0.0.5", "2001:4860:4860::8888", "fe80::1%eth0")
    with patch.object(loop, "getaddrinfo", AsyncMock(return_value=found)):
        assert await async_resolve_host(hass, " gateway.home ") == [
            ipaddress.ip_address("10.0.0.5"),
            ipaddress.ip_address("2001:4860:4860::8888"),
            ipaddress.ip_address("fe80::1%eth0"),
        ]


@pytest.mark.parametrize(
    "failure",
    [OSError("no such host"), ValueError("bad")],
    ids=["unresolvable", "unusable-answer"],
)
async def test_a_name_that_does_not_resolve_has_no_addresses(
    hass: HomeAssistant, failure: Exception
) -> None:
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", AsyncMock(side_effect=failure)):
        assert await async_resolve_host(hass, "gateway.home") == []


async def test_an_address_that_is_no_address_counts_as_unresolved(
    hass: HomeAssistant,
) -> None:
    loop = asyncio.get_running_loop()
    found = _lookup("10.0.0.5", "not-an-address")
    with patch.object(loop, "getaddrinfo", AsyncMock(return_value=found)):
        assert await async_resolve_host(hass, "gateway.home") == []


async def test_a_connector_without_its_own_resolver_uses_the_loop(
    hass: HomeAssistant,
) -> None:
    """Without ``async_resolve_host`` on the connector, the event loop resolves."""
    loop = asyncio.get_running_loop()
    session = SimpleNamespace(connector=SimpleNamespace())
    with (
        patch(f"{DISCOVERY}.async_get_clientsession", return_value=session),
        patch.object(
            loop, "getaddrinfo", AsyncMock(return_value=_lookup("10.0.0.5"))
        ) as lookup,
    ):
        assert await async_resolve_host(hass, " gateway.home ") == [
            ipaddress.ip_address("10.0.0.5")
        ]
    lookup.assert_awaited_once()


async def test_a_slow_lookup_is_given_up(hass: HomeAssistant) -> None:
    async def never(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        await asyncio.sleep(60)
        return []

    loop = asyncio.get_running_loop()
    with (
        patch.object(loop, "getaddrinfo", never),
        patch(f"{DISCOVERY}.RESOLVE_HOST_SECONDS", 0.01),
    ):
        assert await async_resolve_host(hass, "gateway.home") == []


# ── config flow ─────────────────────────────────────────────────────────────

PUBLIC_HOSTS = [
    "8.8.8.8",
    "2001:4860:4860::8888",
    "[2001:4860:4860::8888]",
    "172.32.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "::ffff:8.8.8.8",
]


@pytest.mark.parametrize("host", PUBLIC_HOSTS)
async def test_user_flow_refuses_a_public_address_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway, host: str
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": host}
        )
    # No request, and so no credentials, left Home Assistant.
    read_gateway_id.assert_not_called()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"host": "host_not_local"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_refuses_a_name_with_any_public_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    resolved = [ipaddress.ip_address("10.0.0.5"), ipaddress.ip_address("8.8.8.8")]
    with (
        patch(f"{CONFIG_FLOW}.async_resolve_host", AsyncMock(return_value=resolved)),
        patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": "gateway.example.org"}
        )
    read_gateway_id.assert_not_called()
    assert result["errors"] == {"host": "host_not_local"}


async def test_user_flow_treats_an_unresolvable_name_as_unreachable(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with (
        patch(f"{CONFIG_FLOW}.async_resolve_host", AsyncMock(return_value=[])),
        patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": "gateway.example.org"}
        )
    read_gateway_id.assert_not_called()
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_refuses_a_public_address_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await _entry_with_legacy_mqtt_data(hass, gateway.eurid)
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    with patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {**gateway.config, "host": "8.8.8.8"}
        )
    read_gateway_id.assert_not_called()
    assert result["errors"] == {"host": "host_not_local"}
    assert "host" not in entry.data

    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == gateway.config["host"]


async def test_reconfigure_refuses_a_public_address_and_recovers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    original_host = entry.data["host"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": "8.8.8.8"}
        )
    read_gateway_id.assert_not_called()
    assert result["errors"] == {"host": "host_not_local"}
    assert entry.data["host"] == original_host

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


# ── announcements ───────────────────────────────────────────────────────────


async def test_an_announcement_outside_the_lan_gets_no_request(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Not even a configured gateway's stored credentials are tried on it."""
    await setup_bridge(hass, gateway)
    with patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_announcement("8.8.8.8"),
        )
    read_gateway_id.assert_not_called()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_local_address"


async def test_the_search_drops_announced_addresses_outside_the_lan(
    hass: HomeAssistant,
) -> None:
    hass.config.components.add("zeroconf")

    def browser(zc: object, types: list[str], handlers: list[object]) -> MagicMock:
        handlers[0](
            zeroconf=zc,
            service_type=ZEROCONF_TYPE,
            name="a._enocean._tcp.local.",
            state_change=ServiceStateChange.Added,
        )
        return MagicMock(async_cancel=AsyncMock())

    def service_info(service_type: str, name: str) -> MagicMock:
        info = MagicMock()
        info.async_request = AsyncMock(return_value=True)

        def parsed(version: IPVersion) -> list[str]:
            assert version is IPVersion.V4Only
            return ["8.8.8.8", "192.168.1.5", "10.0.0.7"]

        info.parsed_addresses = parsed
        return info

    with (
        patch(
            f"{DISCOVERY}.ha_zeroconf.async_get_async_instance",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{DISCOVERY}.AsyncServiceBrowser", side_effect=browser),
        patch(f"{DISCOVERY}.AsyncServiceInfo", side_effect=service_info),
        patch(f"{DISCOVERY}.BROWSE_SECONDS", 0),
    ):
        assert await async_browse_gateway_hosts(hass) == ["192.168.1.5", "10.0.0.7"]


# ── entries configured before the rule ──────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "warned"),
    [
        ("8.8.8.8", True),
        ("2001:4860:4860::8888", True),
        ("[2001:4860:4860::8888]", True),
        ("192.0.2.10", True),
        ("10.0.0.5", False),
        ("fe80::1", False),
    ],
)
async def test_an_existing_entry_with_a_public_address_still_sets_up_but_warns(
    host: str, warned: bool, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=OpusConnectionError("Offline"))
    coordinator.async_unload = AsyncMock()
    coordinator.async_restore_setting_guards = AsyncMock()
    entry = SimpleNamespace(entry_id="entry-id", data={**ENTRY_DATA, "host": host})
    coordinator_patch, session_patch = _mock_setup(coordinator)
    hass = MagicMock()
    with (
        caplog.at_level(logging.WARNING, logger="custom_components.opus_greennet"),
        coordinator_patch,
        session_patch as issue,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)
    # Every setup decides the repair notice again: shown for a public address,
    # cleared otherwise (host names: see the test below).
    issue.assert_called_once_with(hass, "entry-id", non_local=warned)
    # The entry was not blocked: setup went on to the coordinator.
    coordinator.async_setup.assert_awaited_once()
    assert ("not in a local network" in caplog.text) is warned
    assert host not in caplog.text


@pytest.mark.parametrize(
    ("resolved", "warned"),
    [
        (["8.8.8.8"], True),
        (["2001:4860:4860::8888"], True),
        # One address outside the local network is enough, as in the config flow.
        (["192.168.1.5", "8.8.8.8"], True),
        (["192.168.1.5"], False),
        # A name that does not resolve raises no notice; the connection fails on
        # its own and setup goes on as before.
        ([], False),
    ],
)
async def test_an_existing_entry_whose_host_name_resolves_to_a_public_address_warns(
    resolved: list[str], warned: bool, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=OpusConnectionError("Offline"))
    coordinator.async_unload = AsyncMock()
    coordinator.async_restore_setting_guards = AsyncMock()
    host = "opusshgw.example.org"
    entry = SimpleNamespace(entry_id="entry-id", data={**ENTRY_DATA, "host": host})
    coordinator_patch, session_patch = _mock_setup(coordinator)
    hass = MagicMock()
    with (
        caplog.at_level(logging.WARNING, logger="custom_components.opus_greennet"),
        coordinator_patch,
        session_patch as issue,
        patch(
            "custom_components.opus_greennet.async_resolve_host",
            AsyncMock(return_value=[ipaddress.ip_address(a) for a in resolved]),
        ) as resolve,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)
    resolve.assert_awaited_once_with(hass, host)
    issue.assert_called_once_with(hass, "entry-id", non_local=warned)
    coordinator.async_setup.assert_awaited_once()
    assert ("not in a local network" in caplog.text) is warned
    assert host not in caplog.text
    assert "8.8.8.8" not in caplog.text


async def test_a_webui_2026_entry_does_not_resolve_its_host_name_at_setup() -> None:
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=OpusConnectionError("Offline"))
    coordinator.async_unload = AsyncMock()
    coordinator.async_restore_setting_guards = AsyncMock()
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={**ENTRY_DATA, "host": "opusshgw.example.org", "generation": "webui2026"},
    )
    coordinator_patch, session_patch = _mock_setup(coordinator)
    with (
        coordinator_patch,
        session_patch as issue,
        patch(
            "custom_components.opus_greennet.async_resolve_host", AsyncMock()
        ) as resolve,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(MagicMock(), entry)
    resolve.assert_not_awaited()
    issue.assert_called_once()
    assert issue.call_args.kwargs == {"non_local": False}


# ── networks Home Assistant is attached to ──────────────────────────────────


def _adapter(enabled: bool, ipv4: list, ipv6: list) -> dict:
    return {
        "name": "eth0",
        "index": 1,
        "enabled": enabled,
        "auto": True,
        "default": True,
        "ipv4": ipv4,
        "ipv6": ipv6,
    }


ADAPTERS = [
    _adapter(
        True,
        [{"address": "192.168.178.20", "network_prefix": 24}],
        [
            {
                "address": "2001:db8:1::20",
                "flowinfo": 0,
                "scope_id": 0,
                "network_prefix": 64,
            }
        ],
    ),
    _adapter(False, [{"address": "203.0.113.5", "network_prefix": 24}], []),
]


async def test_the_networks_of_enabled_adapters_count_as_local(
    hass: HomeAssistant,
) -> None:
    hass.config.components.add("network")
    with patch(
        f"{DISCOVERY}.network.async_get_adapters", AsyncMock(return_value=ADAPTERS)
    ):
        networks = await async_local_networks(hass)
    # A global IPv6 address on the same link, as on dual-stack home networks.
    assert is_local_address(ipaddress.ip_address("2001:db8:1::77"), networks)
    assert not is_local_address(ipaddress.ip_address("2001:db8:2::77"), networks)
    # A disabled adapter adds nothing.
    assert not is_local_address(ipaddress.ip_address("203.0.113.9"), networks)


async def test_without_the_network_integration_only_the_allowlist_counts(
    hass: HomeAssistant,
) -> None:
    assert await async_local_networks(hass) == LOCAL_NETWORKS


async def test_user_flow_accepts_a_name_with_an_on_link_global_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    hass.config.components.add("network")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    resolved = [
        ipaddress.ip_address("192.168.178.30"),
        ipaddress.ip_address("2001:db8:1::30"),
    ]
    with (
        patch(f"{CONFIG_FLOW}.async_resolve_host", AsyncMock(return_value=resolved)),
        patch(
            f"{DISCOVERY}.network.async_get_adapters",
            AsyncMock(return_value=ADAPTERS),
        ),
        patch(
            f"{CONFIG_FLOW}.async_read_gateway_id",
            AsyncMock(side_effect=OpusConnectionError("nothing there")),
        ) as read_gateway_id,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**gateway.config, "host": "opusshgw.fritz.box"}
        )
    # The local check passed; the connection itself was attempted.
    read_gateway_id.assert_awaited_once()
    assert result["errors"] == {"base": "cannot_connect"}
