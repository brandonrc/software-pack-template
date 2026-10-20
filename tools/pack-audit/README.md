# pack-audit: Nebari software-pack readiness auditor

Scores a software-pack repository against this repo's
[release-readiness checklist](../../docs/release-readiness-checklist.md) and
lists what blocks the next maturity level.

Three parts, each optional beyond the first:

| Part | What it does | Needs |
|---|---|---|
| `audit.py scan` + `checklist.yaml` | Deterministic scan. Renders the chart with `helm`, inspects manifests, greps docs and CI, validates `pack-metadata.yaml`, scores, writes JSON + Markdown. 34 of 58 items. | Python, helm |
| `audit.py judge` | Decides the 9 documentation-quality items with a language model, one request per audit, through any OpenAI-compatible endpoint or any runner that can answer a prompt (GitHub Copilot in Actions, a chat window, an agent). Provider-neutral. | a model, anywhere |
| `.claude/agents/software-pack-auditor.md` | Optional Claude Code agent that drives scan + judge interactively, reads the pack, corrects scanner heuristics with evidence, and writes a narrative fix list. | Claude Code |

The remaining 15 items need a cluster (`audit.py verify`) or a person.

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

### Judging the documentation items

The scanner leaves nine items as JUDGMENT (README purpose, prerequisites, NebariApp
values reference, auth docs, must-set values, metrics, logs, appVersion, image
publishing). `judge` builds one prompt holding each item's expectation, the
scanner's pre-check, and the relevant excerpts (README and docs sections, the
`nebariApp` values block, Chart.yaml, rendered-manifest facts), and asks for a
JSON verdict per item. Roughly 9k input tokens and under 1k output per pack.

Any OpenAI-compatible endpoint:

```bash
export PACK_AUDIT_LLM_BASE_URL=https://openrouter.ai/api/v1   # or Ollama, vLLM, LiteLLM, Azure, OpenAI, https://api.anthropic.com/v1 ...
export PACK_AUDIT_LLM_API_KEY=...
export PACK_AUDIT_LLM_MODEL=openai/gpt-4.1
python3 -I tools/pack-audit/audit.py judge tools/pack-audit/reports/my-pack.json --repo ../my-pack
```

Any other runner (paste into a chat, hand to an agent, run in CI):

```bash
python3 -I tools/pack-audit/audit.py judge tools/pack-audit/reports/my-pack.json --repo ../my-pack --prompt-out judge-prompt.md
# ... answer judge-prompt.md with the model of your choice into answer.txt ...
python3 -I tools/pack-audit/audit.py judge tools/pack-audit/reports/my-pack.json --repo ../my-pack --ingest answer.txt --decided-by gpt-4.1
```

Verdicts are written as `judge(<model>): <evidence>` with the scanner's pre-check
kept in `detail`, so a reader can see both. Where the scanner had already set
FAIL or NA, the judge is told to keep it unless the excerpts show what the
scanner missed. Edit any status in the JSON and rerun `report` to override.

With the optional Claude Code agent:

```
Use the software-pack-auditor agent to audit ../my-pack
```

## Runs on Nebari

Next to the score, every report answers "will this run on Nebari?" in three
states, because the score alone does not say so:

| State | Meaning |
|---|---|
| verified | installed on a cluster with the nebari-operator and the NebariApp reached `Ready` (`audit.py verify`) |
| likely | the NebariApp renders with only CRD fields, explicit `routing`, and a service reference that resolves to a rendered Service (static check NA-07) |
| no | no NebariApp renders, the spec fails the CRD pre-check, or a cluster run failed (with the operator's reason) |

### Verifying on a cluster

```bash
python3 -I tools/pack-audit/audit.py verify tools/pack-audit/reports/my-pack.json --dry-run     # print the plan
python3 -I tools/pack-audit/audit.py verify tools/pack-audit/reports/my-pack.json               # run it
```

`verify` creates (or reuses) a local kind cluster through the template's
`dev/Makefile` `cluster` target, which installs MetalLB, Envoy Gateway,
cert-manager, Keycloak, and the nebari-operator (first run takes 5 to 10
minutes). It then installs the pack with the NebariApp enabled at
`<pack>.nebari.local`, waits for the `Ready` condition, requests the hostname
through the gateway (expecting a redirect to Keycloak when auth is enabled),
writes NA-01 and NA-04 back into the results, and uninstalls the release.
Needs `docker`, `kind`, `kubectl`, `helm`, and `make`. Pass `--chart` for
branch audits (their content lives in a temporary export) and `--keep` to
leave the install in place for a look.

## In CI

`.github/workflows/pack-audit.yaml` in this repository is a reusable workflow.
A pack repository adds:

```yaml
permissions:
  contents: read
  security-events: write   # SARIF to code scanning (skipped where not enabled)
  pull-requests: write     # summary comment on PRs
  copilot-requests: write  # only when judging with Copilot
jobs:
  audit:
    uses: nebari-dev/software-pack-template/.github/workflows/pack-audit.yaml@main
    with:
      set: my-chart.secrets.password=ci-only   # whatever the chart needs to render
      judge: ${{ github.event_name == 'push' }} # decide judgment items on main only
      min-level: alpha                          # optional gate
    secrets: inherit
```

The scan job is free and needs no secrets. The judge job is one model request
and runs only when `judge` is true. By default it uses GitHub Copilot CLI with
the workflow token, billed to the organization's AI credits (organization
policy "Allow use of Copilot CLI billed to the organization"; set a budget on
the org). Pass `judge-endpoint` plus the `PACK_AUDIT_LLM_API_KEY` secret and
`judge-model` to use any OpenAI-compatible provider instead. A failed judge
never hides the scan result.

Gate with `audit.py gate <results.json> --min-level E|A|B|GA` anywhere else;
it exits 1 and prints the blockers.

## Exporting findings for other tools

```bash
python3 -I tools/pack-audit/audit.py export tools/pack-audit/reports/my-pack.json --format sarif    # GitHub code scanning, VS Code, security dashboards
python3 -I tools/pack-audit/audit.py export tools/pack-audit/reports/my-pack.json --format junit    # GitLab/Jenkins/Azure test reports
python3 -I tools/pack-audit/audit.py export tools/pack-audit/reports/my-pack.json --format csv      # spreadsheets
```

FAIL and PARTIAL become SARIF `error`/`warning` results (rule id = checklist
id, `helpUri` = the rule link) or JUnit failures grouped by maturity level;
MANUAL, NA, and pending items are SARIF notes or JUnit skips. The JSON stays
the source of truth.

## Statuses and scoring

| Status | Meaning | Scored |
|---|---|---|
| PASS | expectation met | 1 |
| PARTIAL | present but incomplete or wrong for the Nebari path | 0.5 |
| FAIL | missing | 0 |
| JUDGMENT | scanner gathered evidence; `judge` (or a person) decides | excluded until decided |
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

Heuristics are deliberately simple and transparent; the judge or a reviewer is
expected to correct them with evidence. See the `observations` block in the JSON for
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
