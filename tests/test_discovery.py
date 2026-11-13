"""Finding the gateway in the local network (mDNS) for setup and migration."""

from __future__ import annotations

from collections.abc import Iterator
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from zeroconf import IPVersion, ServiceStateChange

from custom_components.opus_greennet.const import DOMAIN
from custom_components.opus_greennet.coordinator import OpusGreenNetCoordinator
from custom_components.opus_greennet.discovery import (
    ZEROCONF_TYPE,
    LocatedGateway,
    async_browse_gateway_hosts,
    async_locate_gateway,
)
from tests.fake_gateway import FakeGateway, device_object
from tests.ha_helpers import setup_bridge

DISCOVERY = "custom_components.opus_greennet.discovery"
CONFIG_FLOW = "custom_components.opus_greennet.config_flow"


async def _legacy_entry(hass: HomeAssistant, eag_id: str) -> config_entries.ConfigEntry:
    """Add an entry as the former MQTT transport created it, and set it up."""
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title=f"Opus GreenNet ({eag_id})",
        data={"eag_id": eag_id},
        source=config_entries.SOURCE_USER,
        options={},
        unique_id=eag_id,
        discovery_keys={},
        subentries_data=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def found_hosts() -> Iterator[list[str]]:
    """Addresses the mDNS search reports; the REST port is the fake's port."""
    hosts: list[str] = []
    with patch(
        f"{DISCOVERY}.async_browse_gateway_hosts", AsyncMock(return_value=hosts)
    ):
        yield hosts


@pytest.fixture(autouse=True)
def rest_port(gateway: FakeGateway) -> Iterator[None]:
    """Default credentials use the fake gateway's port instead of 8080."""
    with patch(f"{DISCOVERY}.DEFAULT_PORT", gateway.port):
        yield


def _announcement(host: str, **properties: str) -> ZeroconfServiceInfo:
    address = ip_address(host)
    return ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=80,
        hostname="opusshgw.local.",
        type=ZEROCONF_TYPE,
        name=f"opusshgw.{ZEROCONF_TYPE}",
        properties=properties,
    )


async def _discover(hass: HomeAssistant, host: str, **properties: str) -> Any:
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_announcement(host, **properties),
    )


# ── mDNS search ─────────────────────────────────────────────────────────────


async def test_search_needs_the_zeroconf_integration(hass: HomeAssistant) -> None:
    assert await async_browse_gateway_hosts(hass) == []


async def test_search_collects_ipv4_addresses_of_announced_gateways(
    hass: HomeAssistant,
) -> None:
    hass.config.components.add("zeroconf")
    aiozc = MagicMock()

    def browser(zc: Any, types: list[str], handlers: list[Any]) -> MagicMock:
        assert types == [ZEROCONF_TYPE]
        for name, change in (
            ("a._enocean._tcp.local.", ServiceStateChange.Added),
            ("b._enocean._tcp.local.", ServiceStateChange.Updated),
            ("c._enocean._tcp.local.", ServiceStateChange.Added),
            ("gone._enocean._tcp.local.", ServiceStateChange.Removed),
        ):
            handlers[0](
                zeroconf=zc, service_type=ZEROCONF_TYPE, name=name, state_change=change
            )
        return MagicMock(async_cancel=AsyncMock())

    addresses = {
        "a._enocean._tcp.local.": ["192.168.2.10"],
        "b._enocean._tcp.local.": ["192.168.2.10", "192.168.2.11"],
    }

    def service_info(service_type: str, name: str) -> MagicMock:
        info = MagicMock()
        # c does not answer the resolution request.
        info.async_request = AsyncMock(return_value=name in addresses)

        def parsed(version: IPVersion) -> list[str]:
            assert version is IPVersion.V4Only
            return addresses[name]

        info.parsed_addresses = parsed
        return info

    with (
        patch(
            f"{DISCOVERY}.ha_zeroconf.async_get_async_instance",
            AsyncMock(return_value=aiozc),
        ),
        patch(f"{DISCOVERY}.AsyncServiceBrowser", side_effect=browser),
        patch(f"{DISCOVERY}.AsyncServiceInfo", side_effect=service_info),
        patch(f"{DISCOVERY}.BROWSE_SECONDS", 0),
    ):
        assert await async_browse_gateway_hosts(hass) == [
            "192.168.2.10",
            "192.168.2.11",
        ]


async def test_locate_accepts_the_gateway_with_the_same_eurid(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    other = FakeGateway(eurid="11223344", password="11223344")
    await other.start()
    try:
        with patch(f"{DISCOVERY}.DEFAULT_PORT", other.port):
            found_hosts[:] = ["192.168.2.1", other.host]
            # Unreachable candidates and other gateways are skipped.
            assert await async_locate_gateway(hass, gateway.eurid) is None
            assert await async_locate_gateway(hass, "11223344") == LocatedGateway(
                other.host, verified=True
            )
    finally:
        await other.stop()


async def test_locate_skips_a_gateway_that_reports_another_eurid(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    """A candidate that accepts the credentials but is another gateway."""
    other = FakeGateway(eurid="11223344", password=gateway.eurid)
    await other.start()
    try:
        with patch(f"{DISCOVERY}.DEFAULT_PORT", other.port):
            found_hosts[:] = [other.host]
            assert await async_locate_gateway(hass, gateway.eurid) is None
    finally:
        await other.stop()


async def test_locate_keeps_a_single_gateway_that_rejects_the_default_password(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    gateway.password = "changed"
    found_hosts[:] = [gateway.host]
    assert await async_locate_gateway(hass, gateway.eurid) == LocatedGateway(
        gateway.host, verified=False
    )
    # With several candidates the rejecting one cannot be attributed.
    found_hosts[:] = [gateway.host, "192.168.2.1"]
    assert await async_locate_gateway(hass, gateway.eurid) is None


# ── migration of former MQTT entries at setup ───────────────────────────────


async def test_legacy_entry_finds_its_gateway_without_asking(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    found_hosts[:] = [gateway.host]
    entry = await _legacy_entry(hass, gateway.eurid)
    await gateway.wait_for_stream()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {"eag_id": gateway.eurid, **gateway.config}
    assert entry.runtime_data.coordinator.get_device("DEV1") is not None
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_legacy_entry_asks_only_for_the_password_when_it_was_changed(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    gateway.password = "changed"
    found_hosts[:] = [gateway.host]
    entry = await _legacy_entry(hass, gateway.eurid)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.data["host"] == gateway.host
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    form = await hass.config_entries.flow.async_configure(flow_id)
    host = next(key for key in form["data_schema"].schema if str(key) == "host")
    assert host.default() == gateway.host
    result = await hass.config_entries.flow.async_configure(flow_id, gateway.config)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_legacy_entry_asks_for_the_address_when_nothing_is_found(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    entry = await _legacy_entry(hass, gateway.eurid)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "host" not in entry.data
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


# ── zeroconf discovery flow ─────────────────────────────────────────────────


async def test_discovery_completes_a_legacy_entry_and_ends_its_reauth(
    hass: HomeAssistant, gateway: FakeGateway, found_hosts: list[str]
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    entry = await _legacy_entry(hass, gateway.eurid)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    result = await _discover(hass, gateway.host, serial="2109201800000128")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {"eag_id": gateway.eurid, **gateway.config}
    # The reload ended the reauthentication that asked for the address.
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_discovery_updates_a_changed_address_of_an_unreachable_gateway(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    with patch.object(gateway, "host", "localhost"):
        entry = await setup_bridge(hass, gateway)
    assert entry.data["host"] == "localhost"
    # The entry is not running (for example after a failed setup).
    await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    # The configured name no longer leads to the announced address.
    with patch(
        f"{CONFIG_FLOW}.async_resolve_host",
        AsyncMock(return_value=[ip_address("192.0.2.9")]),
    ):
        result = await _discover(hass, "127.0.0.1")
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    assert entry.data["host"] == "127.0.0.1"
    assert entry.state is ConfigEntryState.LOADED


async def test_discovery_updates_the_address_of_a_loaded_gateway_that_is_down(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    with patch.object(gateway, "host", "localhost"):
        entry = await setup_bridge(hass, gateway)
    with (
        patch.object(
            OpusGreenNetCoordinator,
            "available",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch(
            f"{CONFIG_FLOW}.async_resolve_host",
            AsyncMock(return_value=[ip_address("192.0.2.9")]),
        ),
    ):
        result = await _discover(hass, "127.0.0.1")
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    await gateway.wait_for_stream()
    assert entry.data["host"] == "127.0.0.1"
    assert entry.state is ConfigEntryState.LOADED


async def test_discovery_sends_no_credentials_while_the_gateway_answers(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """Any device in the LAN can announce the service; the password stays."""
    with patch.object(gateway, "host", "localhost"):
        entry = await setup_bridge(hass, gateway)
    assert entry.runtime_data.coordinator.available
    requests = len(gateway.requests)
    data = dict(entry.data)
    # The configured name leads elsewhere, so the announcement looks like a
    # new address of the gateway.
    with (
        patch(
            f"{CONFIG_FLOW}.async_resolve_host",
            AsyncMock(return_value=[ip_address("192.0.2.9")]),
        ),
        patch(f"{CONFIG_FLOW}.async_read_gateway_id") as read_gateway_id,
    ):
        result = await _discover(hass, "127.0.0.1")
    read_gateway_id.assert_not_called()
    assert len(gateway.requests) == requests
    # Offered as a gateway of its own, which the person has to confirm.
    assert result["type"] is FlowResultType.FORM
    await hass.async_block_till_done()
    assert dict(entry.data) == data
    assert entry.state is ConfigEntryState.LOADED


async def test_discovery_keeps_a_host_name_that_leads_to_the_announced_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    with patch.object(gateway, "host", "localhost"):
        entry = await setup_bridge(hass, gateway)
    requests = len(gateway.requests)
    with patch(
        f"{CONFIG_FLOW}.async_resolve_host",
        AsyncMock(return_value=[ip_address("127.0.0.1")]),
    ):
        result = await _discover(hass, "127.0.0.1")
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    # The chosen name stays, nothing is asked of the announced address and the
    # entry is not reloaded.
    assert entry.data["host"] == "localhost"
    assert entry.state is ConfigEntryState.LOADED
    assert len(gateway.requests) == requests


async def test_discovery_of_the_known_address_changes_nothing(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    entry = await setup_bridge(hass, gateway)
    requests = len(gateway.requests)
    result = await _discover(hass, gateway.host)
    assert result["reason"] == "already_configured"
    assert entry.state is ConfigEntryState.LOADED
    # No request: the address alone identifies the entry.
    assert len(gateway.requests) == requests


async def test_discovery_offers_a_new_gateway_with_its_address(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await _discover(hass, gateway.host, serial="2109201800000128")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    host = next(key for key in result["data_schema"].schema if str(key) == "host")
    assert host.default() == gateway.host
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["title_placeholders"] == {"host": gateway.host}
    assert flow["context"]["unique_id"] == "serial-2109201800000128"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], gateway.config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == gateway.eurid
    await hass.async_block_till_done()


async def test_discovery_of_another_gateway_leaves_entries_alone(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    """A gateway that is not the configured one is offered as a new one."""
    unreachable = await _legacy_entry(hass, "55667788")
    # The default credentials of entry 11223344 work, but the EURID differs.
    gateway.password = "11223344"
    entry = await _legacy_entry(hass, "11223344")
    result = await _discover(hass, gateway.host)
    assert result["type"] is FlowResultType.FORM
    assert "host" not in entry.data
    assert "host" not in unreachable.data


async def test_ignored_gateway_stays_ignored(
    hass: HomeAssistant, gateway: FakeGateway
) -> None:
    result = await _discover(hass, gateway.host, serial="2109201800000128")
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": "serial-2109201800000128", "title": "OPUS"},
    )
    result = await _discover(hass, gateway.host, serial="2109201800000128")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_ignores_ipv6_announcements(hass: HomeAssistant) -> None:
    result = await _discover(hass, "fe80::1")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_ipv4_address"
