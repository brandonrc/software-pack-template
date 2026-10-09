# pack-audit: Nebari software-pack readiness auditor

Scores a software-pack repository against this repo's
[release-readiness checklist](../../docs/release-readiness-checklist.md) and
lists what blocks the next maturity level.

Two parts:

| Part | What it does |
|---|---|
| `audit.py` + `checklist.yaml` | Deterministic scan. Renders the chart with `helm`, inspects manifests, greps docs and CI, validates `pack-metadata.yaml`, scores, writes JSON + Markdown. |
| `.claude/agents/software-pack-auditor.md` | Claude Code agent that runs the scan, reads the pack, decides the judgment items the scanner cannot, rescores, and writes the verdict and fix list. |

Design notes: [docs/superpowers/specs/2026-10-09-pack-audit-design.md](../../docs/superpowers/specs/2026-10-09-pack-audit-design.md).

## Requirements

- Python 3.10+ with PyYAML; `jsonschema` for pack-metadata validation.
- `helm` 3.8+ on PATH (OCI dependency support). Without it, render-based
  checks are skipped and reported as such.
- Optional: `kubeconform` on PATH, or `pip install kubernetes-validate` (pure Python) as a fallback; without either, schema validation is reported as skipped.
- Network access to fetch chart dependencies (`helm dependency build`).

## Usage

Scan a repo (working tree):

```bash
python3 -I tools/pack-audit/audit.py scan ../my-pack --name my-pack
```

Scan a branch without touching the working tree (uses `git archive`):

```bash
python3 -I tools/pack-audit/audit.py scan ../my-pack \
  --ref origin/feature-branch --name my-pack
```

If the chart needs a profile to render, pass it explicitly:

```bash
python3 -I tools/pack-audit/audit.py scan ../my-pack --name my-pack \
  --values ../my-pack/examples/nebari-values.yaml
```

Outputs land in `tools/pack-audit/reports/<name>.json` and `.md` (the
`reports/` directory is gitignored).

Self-test on this repo's own example:

```bash
python3 -I tools/pack-audit/audit.py scan examples/auth-fastapi --name auth-fastapi
```

Rescore after editing statuses in the JSON (the agent does this for the
judgment items):

```bash
python3 -I tools/pack-audit/audit.py report tools/pack-audit/reports/my-pack.json
```

With the agent, in Claude Code:

```
Use the software-pack-auditor agent to audit ../my-pack
```

## Statuses and scoring

| Status | Meaning | Scored |
|---|---|---|
| PASS | expectation met | 1 |
| PARTIAL | present but incomplete or wrong for the Nebari path | 0.5 |
| FAIL | missing | 0 |
| JUDGMENT | scanner gathered evidence; agent decides | excluded until decided |
| MANUAL | needs a cluster or a person | excluded |
| NA | does not apply (with reason) | excluded |

Items are weighted by the level at which they become blockers
(E=4, A=3, B=2, GA=1), so foundational gaps cost more than GA polish. The
**repo-readiness level** is the highest level whose items (and every lower
level's) are all PASS or NA. MANUAL items do not block the repo-readiness
level but are listed as "needs cluster verification"; a pack is not actually
promotable until those are done too.

## What the scanner checks

- Layout: chart, NebariApp, README, LICENSE, CODEOWNERS, SECURITY.md, CI, `pack-metadata.yaml` (schema-validated).
- Helm: dependency build, `helm lint`, `helm template` with the NebariApp toggle on and off, `helm package`, optional `kubeconform`.
- NebariApp spec: only CRD fields (operator v0.1.1), explicit `routing`, hostname pattern, `service.name`/`port` resolve to a rendered Service, auth scopes, landing-page limits.
- Workloads: liveness+readiness probes, non-root, `readOnlyRootFilesystem` / `allowPrivilegeEscalation`, resource requests, image pinning, NetworkPolicy, ServiceMonitor, Ingress alongside NebariApp.
- Secrets: literal credentials in default-rendered Secrets or `values.yaml`.
- CI: lint, template both modes, kubeconform, kind install, nebari-operator integration, release/publish, image build, `helm upgrade` smoke.
- Docs: deploy command, Prerequisites, Known Limitations, Troubleshooting, auth, values reference, sizing, upgrade, CHANGELOG, Astro + `@nebari/starlight` docs site.
- Examples: example values that render, Nebari and standalone values, ArgoCD Application with a real `repoURL`.
- Release: git tags (`vX.Y.Z`), chart version shape, `appVersion`.

Heuristics are deliberately simple and transparent; the agent is expected to
correct them with evidence. See the `observations` block in the JSON for
findings outside the checklist (double ingress, vendored charts, unmerged
branch, generated repo, placeholder READMEs).

## Modes

The audit is **read-only on the pack**: the agent and the scanner write only
under `reports/`, and branch content is read through `git archive`, never a
checkout. The agent may offer to apply fixes at the end; it enters fix mode
only after an explicit yes, applies the approved items one at a time, re-runs
the scanner after each, and never commits or pushes unless asked.

## Comparing several packs

```bash
python3 -I tools/pack-audit/audit.py summary tools/pack-audit/reports/*.json   # -> SUMMARY.md
python3 -I tools/pack-audit/summary_html.py tools/pack-audit/reports/*.json --out readiness.html
```
