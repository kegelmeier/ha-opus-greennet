# Security policy

## Supported versions

Security fixes are made for the latest stable release only. Beta releases
receive fixes in the next beta or stable release. Older releases are not
patched: please update first and check whether the problem still exists.

| Version               | Supported |
| --------------------- | --------- |
| Latest stable release | Yes       |
| Current beta          | Best effort, fixed in the next release |
| Older releases        | No        |

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue,
discussion or pull request.

Open the **Security** tab of this repository and choose
**Report a vulnerability** (GitHub private vulnerability reporting):
<https://github.com/kegelmeier/ha-opus-greennet/security/advisories/new>

The report is visible only to you and the maintainers. Helpful details:

- the version of the integration and of Home Assistant,
- the gateway model and firmware version,
- what an attacker needs (for example access to the local network) and what
  they gain,
- steps to reproduce.

This is a volunteer project. Reports are answered as soon as possible, but no
response time is guaranteed. Please allow time for a fix and a release before
publishing details.

## Keep secrets out of public places

Do not post any of the following in issues, discussions, pull requests or
screenshots, and leave them out of a private report unless they are needed:

- gateway passwords, user names or access tokens,
- EURIDs, in particular the EURID of the gateway: on gateways before
  WebUI 2026 it is the default password of the gateway,
- unedited diagnostics downloads and complete Home Assistant logs. The
  diagnostics download redacts known secrets, but it still describes your
  installation; share only the part that is needed,
- IP addresses, host names and serial numbers of your devices.

If one of these was published by mistake, treat the credential as exposed,
change it where the gateway allows that, and ask a maintainer to remove the
post.

## Scope

In scope is the code in this repository:

- the integration in `custom_components/opus_greennet` (handling of
  credentials, redaction in logs and diagnostics, the connection to the
  gateway, and how data sent by the gateway is processed),
- the blueprints in `blueprints/`,
- the workflows and scripts used to build and release the integration.

Out of scope:

- the firmware, web interface and REST API of the OPUS gateway, the OPUS apps
  and the devices themselves. Please report problems in these products to the
  manufacturer,
- Home Assistant itself and HACS. Please report those to the respective
  projects,
- installations in which the gateway or Home Assistant is reachable from the
  internet. The gateway interface is meant for the local network only.

Ordinary bugs without a security impact belong in the public issue tracker.
