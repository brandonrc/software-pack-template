# Security policy

## Reporting a vulnerability

Report it privately, through GitHub's security advisories for this repository:

**Security → Advisories → Report a vulnerability**

Please do not open a public issue for a vulnerability. Disclosing one publicly before maintainers can ship a fix puts every deployment at risk.

If advisories are not enabled here, or the issue affects the platform rather than this pack, report it against [nebari](https://github.com/nebari-dev/nebari/security/advisories/new) instead.

## What to include

Whatever you have. A rough report today is worth more than a polished one next month:

- what an attacker can do, and what access they need to start
- the versions or configurations affected
- steps to reproduce, if you have them

## What happens next

A maintainer will acknowledge the report and tell you whether it is accepted. If it is, you will hear when a fix is planned and released, and you will be credited unless you ask not to be.

## Supported versions

Only the latest release, unless this repository's README says otherwise.

Security requirements that every Nebari pack has to meet, such as not running containers as root and pinning image tags, are in the [security baseline](https://github.com/nebari-dev/nebari-operator/blob/main/docs/pack-specification.md#6-security-baseline).
