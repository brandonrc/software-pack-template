# Governance

This repository is governed by the Nebari project governance, which lives in one place:

**[nebari-dev/governance](https://github.com/nebari-dev/governance/blob/main/GOVERNANCE.md)**

That document covers stewardship, the teams and what each one decides, the two decision lanes, and how the governance itself is amended. This file exists so you do not have to know that before you can find it.

## This repository

| | |
| --- | --- |
| **Tier** | Official. It lives in the `nebari-dev` organization, so it carries the Nebari name and must satisfy the [Pack Specification](https://github.com/nebari-dev/nebari-operator/blob/main/docs/pack-specification.md) and the [repository standards](https://github.com/nebari-dev/governance/blob/main/repository-standards.md) |
| **Target Pack Specification version** | `0.1.0`, also declared in `pack-metadata.yaml` as `target_pack_spec_version` |
| **Maintainers** | [MAINTAINERS.md](MAINTAINERS.md) |

Update the table when either changes. A stub that lies is worse than no stub.

## Who decides what

Day-to-day decisions about this repository, its features, releases, dependencies, and maturity level, are made by its maintainers.

A change that crosses repository boundaries or alters a shared contract is not this repository's to make. That includes the Pack Specification, the authentication, routing, and TLS conventions, the security baseline, and the repository standards themselves. Those go through a [platform RFD](https://github.com/nebari-dev/governance/blob/main/GOVERNANCE.md#platform-rfds), decided by the Core team.

## Community packs

A pack outside the `nebari-dev` organization is not governed by this. It needs to satisfy the Pack Specification to work, and to follow the [naming rules](https://github.com/nebari-dev/governance/blob/main/trademark-and-naming.md) so it does not imply official endorsement. Beyond that it is yours.
