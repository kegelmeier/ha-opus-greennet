"""Config flow for Opus GreenNet Bridge integration."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    GENERATION_WEBUI_2026,
    OpusApiError,
    OpusAuthError,
    OpusCertificateError,
    OpusConnectionError,
    OpusResponseError,
)
from .const import (
    CONF_CERTIFICATE_FINGERPRINT,
    CONF_EAG_ID,
    CONF_GENERATION,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    WEBUI_2026_PORT,
)
from .discovery import (
    GatewayEndpoint,
    async_detect_webui_2026,
    async_local_networks,
    async_read_gateway_id,
    async_resolve_host,
    default_connection,
    is_local_address,
    parse_host_literal,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


async def _async_name_leads_to(
    hass: HomeAssistant,
    configured: object,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return whether the configured host name resolves to ``address``.

    An IP address, a missing host or a name that does not resolve is no match.
    """
    if not isinstance(configured, str) or parse_host_literal(configured) is not None:
        return False
    return address in await async_resolve_host(hass, configured)


def _is_host(host: str) -> bool:
    """Return whether ``host`` is a bare IP address or host name.

    URLs, paths, credentials and ports are rejected: they would end up in the
    request URL instead of selecting the gateway.
    """
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        pass
    else:
        return True
    labels = host.removesuffix(".").split(".")
    return len(host) <= 253 and all(_HOSTNAME_LABEL.match(label) for label in labels)


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the connection form, prefilled with ``defaults``."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)
            ): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): (
                cv.port
            ),
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): str,
            vol.Required(
                CONF_PASSWORD,
                description={"suggested_value": defaults.get(CONF_PASSWORD)},
            ): PASSWORD_SELECTOR,
        }
    )


def _connection_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: user_input[CONF_PORT],
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_PASSWORD: user_input[CONF_PASSWORD],
    }


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    _discovered_host: str | None = None
    # What a validated WebUI 2026 gateway adds to the connection data (port,
    # generation, certificate); empty for a gateway of the earlier generation.
    _extra: dict[str, Any]
    # A changed certificate waiting for the user's confirmation, and the one
    # the user already confirmed.
    _certificate_review: str | None = None
    # Whether a certificate was remembered before (shown differently from a
    # first contact), and the gateway it was remembered for.
    _certificate_changed: bool = False
    _certificate_gateway: str = ""
    _trusted_fingerprint: str | None = None
    _connection_step: str = ""
    _pending_input: dict[str, Any] | None = None

    async def _async_validate(
        self,
        user_input: Mapping[str, Any],
        errors: dict[str, str],
        known: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Return the gateway's EAG identifier, or None with ``errors`` set.

        ``known`` is the connection data of a configured gateway (reauthentication
        and reconfiguration): it keeps a WebUI 2026 gateway from being asked over
        plain HTTP and its remembered certificate from being replaced silently.
        """
        self._extra = {}
        self._certificate_review = None
        self._certificate_gateway = "" if known is None else known.get(CONF_EAG_ID, "")
        host = user_input[CONF_HOST].strip()
        if not _is_host(host):
            errors[CONF_HOST] = "invalid_host"
            return None
        # The gateway has no TLS: its credentials go only to the local network.
        # Every address of a host name counts, and a name that does not resolve
        # is as unreachable as before (cannot_connect).
        addresses = await async_resolve_host(self.hass, host)
        if not addresses:
            errors["base"] = "cannot_connect"
            return None
        networks = await async_local_networks(self.hass)
        if not all(is_local_address(address, networks) for address in addresses):
            errors[CONF_HOST] = "host_not_local"
            return None
        port = user_input[CONF_PORT]
        # The HTTPS port, and the port of a configured WebUI 2026 gateway, are
        # never asked over plain HTTP: the credentials would travel
        # unencrypted. Every other port, the default one included, is first
        # asked without credentials whether a WebUI 2026 gateway answers there
        # (the default port stands for the HTTPS port), so that a gateway that
        # was updated never receives a new password as Basic authentication.
        webui_port = port == WEBUI_2026_PORT or (
            known is not None
            and known.get(CONF_GENERATION) == GENERATION_WEBUI_2026
            and known.get(CONF_PORT) == port
        )
        endpoint: GatewayEndpoint | None = None
        if not webui_port:
            endpoint = await async_detect_webui_2026(self.hass, host, port)
            webui_port = endpoint is not None
        if not webui_port:
            try:
                return await async_read_gateway_id(self.hass, user_input)
            except OpusAuthError:
                errors["base"] = "invalid_auth"
                return None
            except OpusConnectionError:
                # Neither a WebUI 2026 gateway (asked above) nor an earlier one.
                errors["base"] = "cannot_connect"
                return None
            except OpusResponseError:
                errors["base"] = "unsupported_gateway"
                return None
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
                return None
        return await self._async_validate_webui_2026(
            user_input,
            errors,
            None if known is None else known.get(CONF_CERTIFICATE_FINGERPRINT),
            endpoint,
        )

    async def _async_validate_webui_2026(
        self,
        user_input: Mapping[str, Any],
        errors: dict[str, str],
        known_fingerprint: str | None,
        endpoint: GatewayEndpoint | None = None,
    ) -> str | None:
        """Read the EAG identifier of a WebUI 2026 gateway (HTTPS, user login).

        The certificate of the first contact is remembered, and so is a different
        one, but neither is used before the user confirmed it: no credentials are
        sent to it.
        """
        if endpoint is None:
            endpoint = await async_detect_webui_2026(
                self.hass, user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            )
        if endpoint is None:
            errors["base"] = "cannot_connect"
            return None
        if endpoint.fingerprint not in (known_fingerprint, self._trusted_fingerprint):
            # The first contact is confirmed like a changed certificate: the
            # fingerprint is shown before anything is sent to the gateway.
            self._certificate_review = endpoint.fingerprint
            self._certificate_changed = known_fingerprint is not None
            return None
        extra = {
            CONF_PORT: endpoint.port,
            CONF_GENERATION: GENERATION_WEBUI_2026,
            CONF_CERTIFICATE_FINGERPRINT: endpoint.fingerprint,
        }
        try:
            eag_id = await async_read_gateway_id(self.hass, {**user_input, **extra})
        except OpusCertificateError:
            errors["base"] = "cannot_connect"
        except OpusAuthError:
            errors["base"] = "invalid_auth"
        except OpusConnectionError:
            errors["base"] = "cannot_connect"
        except OpusResponseError:
            errors["base"] = "unsupported_gateway"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            self._extra = extra
            return eag_id
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a gateway by its address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # A gateway entered again at the address of a configured one keeps
            # its remembered certificate until the user confirms another.
            known = self._known_entry_data(user_input[CONF_HOST].strip())
            eag_id = await self._async_validate(user_input, errors, known)
            if self._certificate_review is not None:
                self._connection_step = "user"
                self._pending_input = user_input
                return await self._async_review_certificate()
            if eag_id is not None:
                data = {**_connection_data(user_input), **self._extra}
                await self.async_set_unique_id(eag_id)
                # Adding a known gateway again updates its address (this also
                # completes the move of an entry from the former MQTT setup).
                # The generation and the certificate are those of the gateway
                # just validated: what a newer generation had is dropped when
                # the gateway answers as an earlier one.
                if (
                    known_entry
                    := self.hass.config_entries.async_entry_for_domain_unique_id(
                        DOMAIN, eag_id
                    )
                ) is not None:
                    kept = {
                        key: value
                        for key, value in known_entry.data.items()
                        if key not in (CONF_GENERATION, CONF_CERTIFICATE_FINGERPRINT)
                    }
                    return self.async_update_reload_and_abort(
                        known_entry,
                        data={**kept, **data},
                        reason="already_configured",
                        reload_even_if_entry_is_unchanged=False,
                    )
                return self.async_create_entry(
                    title=f"Opus GreenNet ({eag_id})",
                    data={CONF_EAG_ID: eag_id, **data},
                )
        defaults: dict[str, Any] = {}
        if self._discovered_host is not None:
            defaults[CONF_HOST] = self._discovered_host
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema({**defaults, **(user_input or {})}),
            errors=errors,
        )

    def _known_entry_data(self, host: str) -> Mapping[str, Any] | None:
        """Return the data of the configured entry at ``host``, if there is one."""
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_HOST) == host:
                return entry.data
        return None

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a gateway announced in the local network.

        A configured gateway is recognised by its EURID: an entry without an
        address (former MQTT setup) or with an outdated address is updated and
        reloaded. Any other gateway is offered with its address filled in.
        """
        if discovery_info.ip_address.version != 4:
            return self.async_abort(reason="not_ipv4_address")
        if not is_local_address(
            discovery_info.ip_address, await async_local_networks(self.hass)
        ):
            # An announcement cannot make Home Assistant send credentials to
            # an address outside the local network.
            return self.async_abort(reason="not_local_address")
        host = discovery_info.host
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_HOST) == host:
                return self.async_abort(reason="already_configured")
            # A host name the user chose stays: it already leads to this
            # address. Only a name that leads elsewhere (or nowhere) is
            # replaced by the announced address below.
            if await _async_name_leads_to(
                self.hass, entry.data.get(CONF_HOST), discovery_info.ip_address
            ):
                return self.async_abort(reason="already_configured")
        for entry in self._async_current_entries(include_ignore=False):
            if await self._async_adopt_address(entry.entry_id, host):
                return self.async_abort(reason="already_configured")
        # The announcement carries no EURID; its serial number keeps an
        # ignored gateway ignored. The entry itself is keyed by the EURID.
        serial = discovery_info.properties.get("serial")
        if serial:
            await self.async_set_unique_id(f"serial-{serial}")
            self._abort_if_unique_id_configured()
        self._discovered_host = host
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_user()

    async def _async_adopt_address(self, entry_id: str, host: str) -> bool:
        """Point a configured entry to ``host`` if its gateway answers there."""
        entry = self.hass.config_entries.async_get_known_entry(entry_id)
        eag_id = entry.data[CONF_EAG_ID]
        # Entries of the former MQTT setup have no credentials yet: they get
        # the vendor defaults (password = EURID).
        connection = {
            **default_connection(host, eag_id),
            **{
                key: entry.data[key]
                for key in (
                    CONF_PORT,
                    CONF_USERNAME,
                    CONF_PASSWORD,
                    CONF_GENERATION,
                    CONF_CERTIFICATE_FINGERPRINT,
                )
                if key in entry.data
            },
        }
        try:
            found = await async_read_gateway_id(self.hass, connection)
        except OpusApiError:
            return False
        if found != eag_id:
            return False
        _LOGGER.info("OPUS gateway %s found at a new address", eag_id)
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, **connection}
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return True

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for the address and credentials of a configured gateway."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify that the entered address answers as the configured gateway."""
        entry = self._get_reauth_entry()
        return await self._async_step_connection(
            "reauth_confirm", entry.data, user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or credentials of a configured gateway."""
        entry = self._get_reconfigure_entry()
        return await self._async_step_connection("reconfigure", entry.data, user_input)

    async def _async_step_connection(
        self,
        step_id: str,
        entry_data: Mapping[str, Any],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        eag_id = entry_data[CONF_EAG_ID]
        errors: dict[str, str] = {}
        if user_input is not None:
            found = await self._async_validate(user_input, errors, entry_data)
            if self._certificate_review is not None:
                self._connection_step = step_id
                self._pending_input = user_input
                return await self._async_review_certificate()
            if found is not None and found != eag_id:
                errors["base"] = "wrong_gateway"
            elif found is not None:
                entry = (
                    self._get_reauth_entry()
                    if step_id == "reauth_confirm"
                    else self._get_reconfigure_entry()
                )
                # The entry keeps its other data; the generation and the
                # certificate are those of the gateway just validated, so a
                # gateway of the earlier generation drops what a newer one had.
                data = {
                    key: value
                    for key, value in entry.data.items()
                    if key not in (CONF_GENERATION, CONF_CERTIFICATE_FINGERPRINT)
                }
                return self.async_update_reload_and_abort(
                    entry, data={**data, **_connection_data(user_input), **self._extra}
                )
        # The vendor default password is the gateway's EURID.
        defaults = {CONF_PASSWORD: eag_id, **entry_data, **(user_input or {})}
        return self.async_show_form(
            step_id=step_id,
            data_schema=_connection_schema(defaults),
            errors=errors,
            description_placeholders={"eag_id": eag_id},
        )

    async def _async_review_certificate(self) -> ConfigFlowResult:
        """Show the step that fits the certificate waiting for the user."""
        if self._certificate_changed:
            return await self.async_step_certificate()
        return await self.async_step_certificate_new()

    async def async_step_certificate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether to trust a certificate that differs from the remembered one.

        The gateway presents another certificate than the one remembered at the
        first contact: after a factory reset or a replaced device, but also when
        something else answers at the address. Nothing was sent to it.
        """
        return await self._async_confirm_certificate("certificate", user_input)

    async def async_step_certificate_new(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether to remember the certificate of a gateway met the first time.

        The certificate is self-signed, so only the user can tell whether it
        belongs to the gateway. Nothing was sent to it.
        """
        return await self._async_confirm_certificate("certificate_new", user_input)

    async def _async_confirm_certificate(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        fingerprint = self._certificate_review
        # Only reachable after ``_async_validate``, which sets both.
        assert fingerprint is not None
        assert self._pending_input is not None
        if user_input is not None:
            self._trusted_fingerprint = fingerprint
            if self._connection_step == "user":
                return await self.async_step_user(self._pending_input)
            entry = (
                self._get_reauth_entry()
                if self._connection_step == "reauth_confirm"
                else self._get_reconfigure_entry()
            )
            return await self._async_step_connection(
                self._connection_step, entry.data, self._pending_input
            )
        pairs = [fingerprint[i : i + 2].upper() for i in range(0, len(fingerprint), 2)]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            description_placeholders={
                "fingerprint": ":".join(pairs),
                "eag_id": self._certificate_gateway,
                "host": self._pending_input[CONF_HOST].strip(),
            },
        )
