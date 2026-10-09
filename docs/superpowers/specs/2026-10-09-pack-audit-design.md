# Software-pack readiness auditor (`tools/pack-audit/`)

- **Date:** 2026-10-09
- **Repo:** nebari-dev/software-pack-template
- **Status:** Proposed (design)

This document records how the auditor was derived, so the PR explains the
design rather than only dropping files.

## Goal

Give pack authors and the platform team a repeatable, evidence-backed answer to
"how ready is this pack, and what exactly do we fix next?", using the same
rubric the org already promotes packs with. The output is a score, an achieved
maturity level, blockers per level, and a fix list that names files.

## Sources of truth (in priority order)

1. `software-pack-template/docs/release-readiness-checklist.md`. The rubric.
   Every scored item in `checklist.yaml` maps to one line of it and keeps its
   level tag ([E]/[A]/[B]/[GA]).
2. `software-pack-template/README.md`, `docs/nebariapp-crd-reference.md`,
   `docs/pack-docs-requirements.md`, the five `examples/`, and the CI
   workflows. These define the expected layout, the `nebariapp:` values
   contract, the lint/template-both-modes/kubeconform CI pattern, and the
   docs-site stack.
3. `nebari-dev/software-pack-dashboard/schema/pack-metadata.schema.json`
   (vendored) and `tracked-packs.yaml`. Metadata validation and the list of
   first-party packs.
4. `nebari-dev/nebari-operator` `api/v1/nebariapp_types.go` and
   `internal/controller/nebariapp_controller.go` at v0.1.1/main. Used for the
   NebariApp field set and for runtime semantics the docs understate.
5. Eight first-party packs (llm-serving, mlflow, superset, harbor, langfuse,
   data-science, lgtm, nebari-nexus) read for the conventions that mature packs
   actually follow: `chart/` or root chart, `examples/argocd-application.yaml`,
   `standalone-values.yaml`, `dev/` kind Makefile, `SECURITY.md`,
   `CODEOWNERS`, docs on `@nebari/starlight`, hand-written NebariApp templates
   with explicit `routing`, `check-jsonschema` on `pack-metadata.yaml` in lint.

Nothing in nebari-dev already does this: a `gh search code` over the org on
2026-10-09 found no `.claude/agents`, skills, or audit tooling for packs, and no
`software-pack-tools` repo. `skillsctl` is a skills registry, not a pack tool.

## Design decisions

**Split deterministic from judgment.** Most checklist rows are either
mechanical (file exists, helm lint passes, probes present) or need a reader
(does the README actually explain who the pack is for?). `audit.py` owns the
first class and leaves the second as `JUDGMENT` with gathered evidence; the
agent decides them and rescoring is deterministic again. This keeps scores
reproducible and lets a human run the scanner without Claude.

**Encode the rubric as data.** `checklist.yaml` carries id, level, category,
check type, applicability (`first-party`, `standalone`, `auth`) and the
expectation text. Changing the rubric upstream means editing the YAML, not the
code. One item was added that the upstream list lacks, `NA-07` (NebariApp spec
validates and resolves), because it is the cheapest predictor of the
cluster-only `NA-01` and caught real defects in the first run.

**Score by gate, not just by percentage.** The percentage (level-weighted
E=4/A=3/B=2/GA=1) gives a trend line, but promotion is a gate: the report's
headline is the highest level with no FAIL/PARTIAL at or below it. MANUAL and
NA items are excluded from the score so a vendor pack is not penalized for
first-party-only rows (dashboard listing, helm-repository sync, pre-sales
sign-off); they are listed as "needs verification" instead.

**Render, do not just grep.** Security, probe, image and NebariApp checks run
on `helm template` output in both NebariApp modes. When the default
`values.yaml` cannot render, the scanner searches the repo's own values files
(singles, then pairs) the way the pack's CI would, and records which profile
it needed, because "does not render from defaults" is itself a finding.

**Validate the NebariApp against the operator, not the docs.** The template's
CRD reference tracks operator alpha.19 and lacks fields the operator now
accepts (`routing.tls.secretName`, `landingPage.iconLight/iconDark`). The
field set in `audit.py` was taken from `nebariapp_types.go` at v0.1.1.
Reading `nebariapp_controller.go` also showed that an omitted `spec.routing`
means no HTTPRoute at all; two template examples omit it, which is a template
bug to raise alongside this PR.

**Accept equivalent deviations.** Hand-written NebariApp templates (harbor),
GitLab CI (vendor repos), and vendored upstream charts are scored on whether
they deliver the checklist's guarantee, with the deviation recorded under
Observations. The agent prompt spells out the equivalence rules so two auditors
reach the same verdict.

**Keep the agent read-only on the pack; fixes are a separate, opt-in mode.**
Auditing writes only under `reports/`; branch content is read through
`git archive --ref`, never a checkout. The agent may offer fixes at the end but
enters fix mode only after the user explicitly agrees, applies approved items
one checklist id at a time with a rescan after each, and never commits or
pushes unless asked. Keeping the verdict and the edits in separate steps is what
makes the audit trustworthy to a vendor whose repo it is.

**Pre-sales and sign-off are reported, never scored.** The upstream checklist
lists pre-sales verification and promotion sign-off as blockers, but they are
process gates owned by people, not properties of the repository. Scoring them
would punish every pack by the same constant and tell the owner nothing to
fix. They remain in the report so a promotion PR has the full list (decision
2026-10-09).

**Telemetry is judged against what the platform actually provides.** NIC runs
an OpenTelemetry collector DaemonSet whose Prometheus receiver discovers pods
through `prometheus.io/scrape` / `prometheus.io/port` annotations (role: pod)
and accepts OTLP push; the LGTM pack supplies Loki/Tempo/Mimir, ships every
container's stdout to Loki via promtail, and its Grafana sidecar loads any
`grafana_dashboard`-labelled ConfigMap from any namespace. So a pack owes an
annotated `/metrics` endpoint (or a documented reason), a structured log
format, and optionally a dashboard ConfigMap. The upstream checklist's
"ServiceMonitor or PodMonitor" wording describes a mechanism the NIC collector
does not read; the auditor accepts annotations/OTLP as PASS and a bare
ServiceMonitor as PARTIAL.

**"Runs on Nebari" is a separate three-state answer, not the score.** A pack
can score well on documentation and security while having no NebariApp at
all, and a pack with a perfect NebariApp can still fail on a cluster. So every
report also says `verified` (installed on a cluster with the operator and the
NebariApp reached Ready), `likely` (static NA-07 pre-check passes), or `no`.
`audit.py verify` reuses the template's `dev/Makefile` cluster recipe to move
packs from likely to verified without a person, and writes the operator's own
condition reasons (`NamespaceNotOptedIn`, `ServiceNotFound`,
`CertificateNotReady`) into the report when it fails.

**Findings export in standard formats.** The results JSON is the source of
truth, and `audit.py export` renders it as SARIF 2.1.0 (rule id = checklist
id, `helpUri` = the rule link), JUnit XML (one suite per maturity level), or
CSV, so GitHub code scanning, GitLab test reports, and spreadsheets can
consume the same findings without a custom parser.

## Known limits

- Heading regexes are crude; the agent is expected to correct false
  positives/negatives and record `override:` reasons.
- `NA-05` (group membership) and `IN-01` (README-only install) still need a
  person; `verify` closes `NA-01` and `NA-04` only.
- `verify` was written against this repo's `dev/Makefile` and the operator's
  dev scripts but has not yet been exercised end to end on a kind cluster from
  this tool; its `--dry-run` plan has.
- Multi-chart repos with no pack chart (one chart per upstream component)
  score against the first chart that fronts the user-facing app; the structural
  problem is reported but not scored per chart.
- `kubeconform` is optional; without it, schema validation is only checked as
  a CI step.

## Validation

Smoke-tested against `examples/wrap-existing-chart` from the template (renders,
lints, packages, gates correctly; fails the org-level items as expected because
an example is not a pack) and against four vendor packs with very different
shapes: a GitLab-hosted generated chart, a multi-chart Istio/KServe bundle, an
EKS/ALB chart with no NebariApp, and a generated GPU inference pack.

## Layout

```
.claude/agents/software-pack-auditor.md
tools/pack-audit/
  README.md
  audit.py                 scanner + scorer + report/summary
  summary_html.py          shareable HTML page from several reports
  checklist.yaml           the rubric as data (with source/example links per item)
  reference/
    pack-metadata.schema.json   vendored from software-pack-dashboard; refresh on change
    template-conventions.md     what a healthy pack looks like
  reports/                 gitignored output
```

Possible follow-ups: run `audit.py scan examples/auth-fastapi` in `lint.yaml`
as a self-test; drive `dev/Makefile` or `action-nebari-sandbox` to close the
MANUAL cluster items.
