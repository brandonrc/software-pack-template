# Contributing

Contributions are welcome, including code, documentation, issues, and review.

## Before you start

- The [Nebari governance](https://github.com/nebari-dev/governance/blob/main/GOVERNANCE.md) explains who decides what. Most decisions about this repository are made by its maintainers, listed in [MAINTAINERS.md](MAINTAINERS.md).
- The [community guidelines](https://www.nebari.dev/community) cover the contribution process in detail.
- The [Code of Conduct](CODE_OF_CONDUCT.md) applies to every space this project uses.

## Making a change

1. Open an issue first for anything substantial, so the approach can be discussed before you build it.
2. Work on a branch and open a pull request. Do not commit to the default branch.
3. A pull request needs one approving review before merge, and CI has to pass.

Commit and triage access on this repository is granted by its maintainers as trust is established. Ask if you have been contributing and want it.

## Changes that need an RFD

Some changes are not this repository's to make on its own. A change to the platform contract, the shared conventions, the security baseline, or the repository standards affects every pack, and goes through a [platform RFD](https://github.com/nebari-dev/governance/blob/main/GOVERNANCE.md#platform-rfds) in the governance repository.

If a maintainer tells you your change needs an RFD, that is what they mean.

## For packs specifically

- The [Nebari Pack Specification](https://github.com/nebari-dev/nebari-operator/blob/main/docs/pack-specification.md) is the contract a pack has to satisfy.
- The [release readiness checklist](docs/release-readiness-checklist.md) governs promotion between maturity levels.
- `pack-metadata.yaml` declares this pack's level, owner, and target specification version. Keep it accurate; the [pack dashboard](https://github.com/nebari-dev/software-pack-dashboard) reads it.
