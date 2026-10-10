#!/usr/bin/env python3
"""Nebari software-pack auditor: deterministic checks.

Usage:
  audit.py scan <repo-path> [--ref <git-ref>] [--name <pack-name>]
                [--values <file> ...] [--set k=v ...] [--out <dir>]
      Inspect a pack repository, run helm/kubeconform where available, and write
      <out>/<name>.json (machine-readable) and <out>/<name>.md (human-readable).
      Items marked `judgment` in checklist.yaml are left as JUDGMENT for the
      auditor agent to decide.

  audit.py report <results.json> [--out <file.md>]
      Re-score a results file (after the agent has filled JUDGMENT items and
      optionally overridden statuses) and regenerate the markdown report.

  audit.py summary <results.json> [<results.json> ...] [--out SUMMARY.md]
      Cross-pack comparison table, shared gaps, and gate blockers per pack.

  audit.py verify <results.json> [--chart <dir>] [--cluster NAME] [--dry-run] [--keep]
      Install the pack on a local kind cluster running the Nebari stack (created
      with the template's dev/Makefile), wait for the NebariApp Ready condition,
      probe the hostname through the gateway, and write NA-01 / NA-04 back.
      Turns "Runs on Nebari: likely" into "verified" or "no" with the operator's reason.

  audit.py export <results.json> --format sarif|junit|csv [--out FILE]
      SARIF 2.1.0 (GitHub code scanning, VS Code), JUnit XML (GitLab/Jenkins test
      reports), or CSV, from the same results JSON.

Statuses: PASS, PARTIAL, FAIL, JUDGMENT (pending), MANUAL (needs cluster/person),
NA (does not apply).

Only the standard library plus PyYAML (and jsonschema when installed) are needed.
helm and kubeconform are used when present on PATH.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CHECKLIST = HERE / "checklist.yaml"
SCHEMA = HERE / "reference" / "pack-metadata.schema.json"

AUDIT_HOST = "audit.nebari.example.com"
MUTABLE_TAGS = {"latest", "prod", "production", "main", "master", "dev", "stable", "edge", "nightly", "local"}
NEBARI_VALUE_KEYS = ("nebariapp", "nebariApp", "nebari", "nebari_app", "nebariApp")
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
JOB_KINDS = {"Job", "CronJob"}
PLACEHOLDER_RE = re.compile(r"(<[^>]{1,60}>|REPLACE[_A-Z-]*|CHANGEME|changeme|your-|example\.(com|mil|org)|xxxx|TODO)")

# NebariApp CRD surface: nebari-operator api/v1/nebariapp_types.go at v0.1.1 / main (2026-10-09).
# The template's docs/nebariapp-crd-reference.md (alpha.19) lacks tls.secretName and landingPage.iconLight/iconDark.
CRD = {
    "spec": {"hostname", "service", "routing", "auth", "gateway", "serviceAccountName", "landingPage"},
    "service": {"name", "port", "namespace"},
    "routing": {"routes", "publicRoutes", "tls", "annotations"},
    "route": {"pathPrefix", "pathType"},
    "tls": {"enabled", "secretName"},
    "auth": {"enabled", "provider", "provisionClient", "enforceAtGateway", "forwardAccessToken", "denyRedirect",
             "redirectURI", "clientSecretRef", "scopes", "groups", "issuerURL", "spaClient", "deviceFlowClient",
             "keycloakConfig", "tokenExchange"},
    "landingPage": {"enabled", "displayName", "description", "icon", "iconLight", "iconDark", "category", "priority", "externalUrl", "healthCheck"},
    "healthCheck": {"enabled", "path", "port", "intervalSeconds", "timeoutSeconds"},
}
HOSTNAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
MINIMAL_SCOPES = {"openid", "profile", "email", "groups"}

# README/doc heading heuristics: item id -> regexes (any match on a heading line).
HEADING_PATTERNS = {
    "prerequisites": r"prerequisite|requirements|before you (install|begin)|what we need",
    "limitations": r"known (limitation|issue|gap|limit)|limitations|not (yet )?(supported|established)|caveat",
    "troubleshooting": r"troubleshoot|diagnos|common (failure|problem|error)|debugging|recovery",
    "auth": r"\bauth|keycloak|oidc|\bsso\b|login|identity",
    "values": r"values|configuration|configur|settings|parameters|reference",
    "sizing": r"sizing|performance|resource (requirements|profiles|limits)|capacity|hardware|throughput|node (types?|groups?)",
    "upgrade": r"upgrad|migrat|rollback|rebuild",
}

# ----------------------------------------------------------------------------- helpers


def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(cmd)}: timed out after {timeout}s"


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def read(p: Path, limit: int = 2_000_000) -> str:
    try:
        return p.read_text(errors="replace")[:limit]
    except Exception:
        return ""


def load_yaml(p: Path):
    try:
        return yaml.safe_load(read(p))
    except Exception as e:  # noqa: BLE001
        return {"__error__": str(e)}


def load_all_yaml(text: str) -> list:
    docs = []
    try:
        for d in yaml.safe_load_all(text):
            if isinstance(d, dict) and d.get("kind"):
                docs.append(d)
    except Exception as e:  # noqa: BLE001
        docs.append({"kind": "__parse_error__", "error": str(e)})
    return docs


def rglob(root: Path, patterns: tuple[str, ...], skip=("/.git/", "/node_modules/", "/charts/", "/.tools/")) -> list[Path]:
    out = []
    for pat in patterns:
        for p in root.rglob(pat):
            s = str(p)
            if any(k in s for k in skip):
                continue
            out.append(p)
    return sorted(set(out))


def headings(md: str) -> list[str]:
    return [ln.lstrip("#").strip() for ln in md.splitlines() if ln.startswith("#")]


def has_heading(mds: dict[str, str], key: str) -> list[str]:
    rx = re.compile(HEADING_PATTERNS[key], re.I)
    hits = []
    for name, text in mds.items():
        for h in headings(text):
            if rx.search(h):
                hits.append(f"{name}: '{h}'")
                break
    return hits


def deep_get(d, path: str, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ----------------------------------------------------------------------------- data model


class Result:
    def __init__(self):
        self.checks: dict[str, dict] = {}
        self.observations: list[dict] = []
        self.facts: dict = {}

    def set(self, cid: str, status: str, evidence: str, detail: list[str] | None = None):
        self.checks[cid] = {"status": status, "evidence": evidence, "detail": detail or []}

    def observe(self, severity: str, title: str, detail: str = "", where: str = ""):
        self.observations.append({"severity": severity, "title": title, "detail": detail, "where": where})


# ----------------------------------------------------------------------------- repo prep


def prepare_repo(path: Path, ref: str | None) -> tuple[Path, dict]:
    """Return (tree_to_audit, git_info). With --ref, export that ref into a temp dir."""
    info = {"path": str(path), "ref": ref, "is_git": (path / ".git").exists(), "tags": [], "head": None,
            "default_branch_files": None, "remote": None}
    git_dir = path
    if info["is_git"]:
        rc, out, _ = sh(["git", "-C", str(git_dir), "tag", "--list"])
        info["tags"] = [t for t in out.split() if t]
        info["shallow"] = (path / ".git" / "shallow").exists()
        rc, out, _ = sh(["git", "-C", str(git_dir), "rev-parse", "--short", ref or "HEAD"])
        info["head"] = out.strip() or None
        rc, out, _ = sh(["git", "-C", str(git_dir), "remote", "get-url", "origin"])
        info["remote"] = out.strip() or None
        rc, out, _ = sh(["git", "-C", str(git_dir), "log", "-1", "--format=%cs", ref or "HEAD"])
        info["last_commit_date"] = out.strip() or None
        rc, out, _ = sh(["git", "-C", str(git_dir), "ls-tree", "-r", "--name-only", "HEAD"])
        info["default_branch_files"] = len(out.split()) if rc == 0 else None
    if ref:
        tmp = Path(tempfile.mkdtemp(prefix="pack-audit-"))
        rc, out, err = sh(["bash", "-c", f"git -C {git_dir} archive {ref} | tar -x -C {tmp}"])
        if rc != 0:
            sys.exit(f"could not export {ref}: {err}")
        return tmp, info
    return path, info


# ----------------------------------------------------------------------------- discovery


def find_charts(root: Path) -> list[Path]:
    """Top-level charts: Chart.yaml files not nested under another chart's charts/ dir."""
    found = []
    for p in root.rglob("Chart.yaml"):
        rel = p.relative_to(root).parts
        if ".git" in rel or "node_modules" in rel:
            continue
        if "charts" in rel[:-2]:  # inside a charts/ subdir (vendored dependency)
            # keep only if it is the pack's own charts/<name> layout at the repo root
            idx = rel.index("charts")
            if idx != 0:
                continue
        found.append(p.parent)
    return sorted(found, key=lambda p: (len(p.relative_to(root).parts), str(p)))


def chart_has_nebariapp(chart: Path) -> dict:
    tpl = chart / "templates"
    hits = {"template": None, "library_dep": False, "values_key": None, "hostname_key": None}
    for p in tpl.rglob("*.yaml") if tpl.exists() else []:
        t = read(p)
        if "kind: NebariApp" in t or "nebari-app.nebariApp" in t:
            hits["template"] = str(p.relative_to(chart))
            break
    for p in tpl.rglob("*.tpl") if tpl.exists() else []:
        if "kind: NebariApp" in read(p):
            hits["template"] = hits["template"] or str(p.relative_to(chart))
    cy = load_yaml(chart / "Chart.yaml") or {}
    for d in cy.get("dependencies") or []:
        if isinstance(d, dict) and d.get("name") == "nebari-app":
            hits["library_dep"] = True
    vals = load_yaml(chart / "values.yaml") or {}
    if isinstance(vals, dict):
        for k in vals:
            if k.lower().replace("_", "") in ("nebariapp", "nebari") and isinstance(vals[k], dict) and "enabled" in vals[k]:
                hits["values_key"] = k
                hits["hostname_key"] = "hostname" if "hostname" in vals[k] else None
                break
    return hits


def candidate_values_files(root: Path, chart: Path) -> list[Path]:
    cands = []
    for pat in ("values-*.yaml", "values.*.yaml", "ci/*values*.yaml", "ci/*.yaml"):
        cands += list(chart.glob(pat))
    for d in ("examples", "values", "config/values", "dev", "ci"):
        dd = root / d
        if dd.is_dir():
            cands += [p for p in dd.glob("*.yaml") if "values" in p.name or p.parent.name in ("examples", "values")]
    # Exclude ArgoCD apps and non-values files.
    # Prefer the template's examples/ convention, and Nebari-flavoured names,
    # over local/dev profiles so EX-01 is judged on the file installers are told to use.
    def rank(p: Path):
        parts = {q.name for q in p.parents}
        return (0 if "examples" in parts else 1 if "values" in parts else 2,
                0 if "nebari" in p.name else 1,
                1 if "dev" in parts or "local" in p.name else 0,
                str(p))
    out = []
    for p in sorted(set(cands), key=rank):
        if p.name == "values.yaml" and p.parent == chart:
            continue
        t = read(p, 4000)
        if "kind: Application" in t or "apiVersion:" in t.split("\n", 3)[0]:
            continue
        out.append(p)
    return out


# ----------------------------------------------------------------------------- helm


class HelmRun:
    def __init__(self, root: Path, chart: Path, nk: dict, user_values: list[Path], user_sets: list[str], workdir: Path):
        self.root, self.chart, self.nk = root, chart, nk
        self.user_values, self.user_sets = user_values, user_sets
        self.workdir = workdir
        self.log: list[str] = []
        self.dep_ok = None
        self.lint = None
        self.renders: dict[str, dict] = {}
        self.package_ok = None

    def _base(self, release: str) -> list[str]:
        return ["helm", "template", release, str(self.chart), "--namespace", "audit", "--include-crds"]

    def deps(self):
        if not (self.chart / "Chart.yaml").exists():
            return
        cy = load_yaml(self.chart / "Chart.yaml") or {}
        if not cy.get("dependencies"):
            self.dep_ok = True
            return
        rc, out, err = sh(["helm", "dependency", "build", str(self.chart)], timeout=600)
        if rc != 0:
            rc, out, err = sh(["helm", "dependency", "update", str(self.chart)], timeout=600)
        self.dep_ok = rc == 0
        self.log.append(f"helm dependency {'ok' if self.dep_ok else 'FAILED: ' + err.strip()[-400:]}")

    def _try(self, label: str, values: list[Path], sets: list[str]) -> dict:
        cmd = self._base("audit")
        for v in values:
            cmd += ["-f", str(v)]
        for s in sets:
            cmd += ["--set", s]
        rc, out, err = sh(cmd, timeout=180)
        return {"ok": rc == 0, "cmd": " ".join(cmd), "stdout": out if rc == 0 else "", "stderr": err.strip()[-1500:],
                "values": [str(v) for v in values], "sets": sets, "label": label}

    def render(self, label: str, sets: list[str]):
        """Render with user-provided values first; otherwise search candidate values files."""
        attempts = []
        if self.user_values or self.user_sets:
            r = self._try(label, self.user_values, self.user_sets + sets)
            attempts.append(r)
        if not attempts or not attempts[-1]["ok"]:
            r = self._try(label, [], sets)
            attempts.append(r)
            if not r["ok"]:
                cands = candidate_values_files(self.root, self.chart)
                combos = [[c] for c in cands] + [list(c) for c in itertools.permutations(cands, 2)]
                for combo in combos[:60]:
                    r = self._try(label, combo, sets)
                    attempts.append(r)
                    if r["ok"]:
                        break
        r["attempts"] = len(attempts)
        r["first_error"] = attempts[0]["stderr"]
        self.renders[label] = r
        self.log.append(f"render {label}: {'ok' if r['ok'] else 'FAILED'} after {len(attempts)} attempt(s)"
                        + (f" using {[str(Path(v).resolve()).replace(str(self.root.resolve()) + '/', '') for v in r['values']]}" if r["values"] else ""))
        return r

    def run_lint(self, values: list[str], sets: list[str]):
        cmd = ["helm", "lint", str(self.chart)]
        for v in values:
            cmd += ["-f", v]
        for s in sets:
            cmd += ["--set", s]
        rc, out, err = sh(cmd, timeout=180)
        self.lint = {"ok": rc == 0, "output": (out + err).strip()[-2000:], "cmd": " ".join(cmd)}
        self.log.append(f"helm lint: {'ok' if rc == 0 else 'FAILED'}")

    def run_package(self):
        rc, out, err = sh(["helm", "package", str(self.chart), "-d", str(self.workdir)], timeout=180)
        self.package_ok = rc == 0
        self.log.append(f"helm package: {'ok' if rc == 0 else 'FAILED: ' + err.strip()[-300:]}")


# ----------------------------------------------------------------------------- manifest analysis


def containers_of(doc: dict) -> list[tuple[str, dict, dict]]:
    """Yield (workload-name, pod-spec, container) for workloads and jobs."""
    kind = doc.get("kind")
    name = deep_get(doc, "metadata.name", "?")
    if kind in WORKLOAD_KINDS or kind == "Job":
        pod = deep_get(doc, "spec.template.spec", {}) or {}
    elif kind == "CronJob":
        pod = deep_get(doc, "spec.jobTemplate.spec.template.spec", {}) or {}
    else:
        return []
    out = []
    for c in (pod.get("containers") or []) + (pod.get("initContainers") or []):
        out.append((f"{kind}/{name}", pod, c))
    return out


def analyze_manifests(docs: list[dict]) -> dict:
    a = {"kinds": {}, "images": [], "unpinned": [], "mutable": [], "probes_missing": [], "probes_ok": 0,
         "containers": 0, "nonroot_missing": [], "hardening_missing": [], "resources_missing": [],
         "networkpolicy": [], "monitors": [], "ingress": [], "services": {}, "nebariapps": [], "secrets_literal": [],
         "grafana_dashboards": [], "parse_errors": [], "workloads": [], "scrape_annotated": [], "otlp_env": []}
    for d in docs:
        kind = d.get("kind")
        if kind == "__parse_error__":
            a["parse_errors"].append(d.get("error"))
            continue
        a["kinds"][kind] = a["kinds"].get(kind, 0) + 1
        name = deep_get(d, "metadata.name", "?")
        if kind == "Service":
            ports = [p.get("port") for p in (deep_get(d, "spec.ports") or []) if isinstance(p, dict)]
            a["services"][name] = ports
        elif kind == "NebariApp":
            a["nebariapps"].append(d)
        elif kind == "NetworkPolicy":
            a["networkpolicy"].append(name)
        elif kind in ("ServiceMonitor", "PodMonitor"):
            a["monitors"].append(f"{kind}/{name}")
        elif kind == "Ingress":
            a["ingress"].append(name)
        elif kind == "ConfigMap" and (deep_get(d, "metadata.labels.grafana_dashboard") is not None):
            a["grafana_dashboards"].append(name)
        elif kind == "Secret":
            data = {**(d.get("stringData") or {}), **(d.get("data") or {})}
            for k, v in data.items():
                if isinstance(v, str) and v and re.search(r"pass|secret|token|key|dsn|url|credential", k, re.I):
                    a["secrets_literal"].append(f"Secret/{name}:{k}={v[:24]}{'…' if len(v) > 24 else ''}")
        if kind in WORKLOAD_KINDS:
            a["workloads"].append(f"{kind}/{name}")
            ann = deep_get(d, "spec.template.metadata.annotations") or {}
            if str(ann.get("prometheus.io/scrape", "")).lower() == "true":
                a["scrape_annotated"].append(f"{kind}/{name}")
        if kind == "Service" and str(deep_get(d, "metadata.annotations.prometheus.io/scrape", "")).lower() == "true":
            a["scrape_annotated"].append(f"Service/{name}")
        for wl, pod, c in containers_of(d):
            is_init = c in (pod.get("initContainers") or [])
            img = str(c.get("image", ""))
            if re.search(r"/pause(:|@|$)|image-puller|placeholder", img + " " + str(c.get("name", "")), re.I):
                continue  # infrastructure filler containers (z2jh image pullers, pause) are not the application
            if any(str(e.get("name", "")).startswith("OTEL_EXPORTER_OTLP") for e in (c.get("env") or []) if isinstance(e, dict)):
                a["otlp_env"].append(f"{wl}/{c.get('name')}")
            a["containers"] += 1
            a["images"].append(img)
            if "@sha256:" not in img:
                tag = img.rsplit(":", 1)[1] if ":" in img.rsplit("/", 1)[-1] else None
                if tag is None:
                    a["unpinned"].append(f"{wl} {img}")
                elif tag in MUTABLE_TAGS:
                    a["mutable"].append(f"{wl} {img}")
            psc, csc = pod.get("securityContext") or {}, c.get("securityContext") or {}
            nonroot = csc.get("runAsNonRoot", psc.get("runAsNonRoot"))
            uid = csc.get("runAsUser", psc.get("runAsUser"))
            if not (nonroot is True or (isinstance(uid, int) and uid > 0)):
                a["nonroot_missing"].append(f"{wl}/{c.get('name')}")
            if not (csc.get("readOnlyRootFilesystem") is True and csc.get("allowPrivilegeEscalation") is False
                    and (nonroot is True or (isinstance(uid, int) and uid > 0))):
                a["hardening_missing"].append(f"{wl}/{c.get('name')}")
            if not deep_get(c, "resources.requests"):
                a["resources_missing"].append(f"{wl}/{c.get('name')}")
            if d.get("kind") in WORKLOAD_KINDS and not is_init:
                if c.get("livenessProbe") and c.get("readinessProbe"):
                    a["probes_ok"] += 1
                else:
                    missing = [p for p in ("livenessProbe", "readinessProbe") if not c.get(p)]
                    a["probes_missing"].append(f"{wl}/{c.get('name')} (no {', '.join(missing)})")
    return a


def validate_nebariapp(na: dict, services: dict) -> list[str]:
    issues = []
    spec = na.get("spec") or {}

    def unknown(obj, allowed, where):
        if isinstance(obj, dict):
            for k in obj:
                if k not in allowed:
                    issues.append(f"unknown field {where}.{k} (not in the NebariApp CRD at operator v0.1.1)")

    unknown(spec, CRD["spec"], "spec")
    host = spec.get("hostname")
    if not host:
        issues.append("spec.hostname missing")
    elif not HOSTNAME_RE.match(str(host)):
        issues.append(f"spec.hostname '{host}' violates the CRD hostname pattern")
    svc = spec.get("service") or {}
    unknown(svc, CRD["service"], "spec.service")
    if not svc.get("name"):
        issues.append("spec.service.name missing")
    elif services and svc["name"] not in services:
        issues.append(f"spec.service.name '{svc['name']}' does not match any rendered Service ({', '.join(sorted(services))})")
    elif services and svc.get("port") not in (services.get(svc["name"]) or []):
        issues.append(f"spec.service.port {svc.get('port')} is not a port on Service {svc['name']} ({services.get(svc['name'])})")
    if not isinstance(svc.get("port"), int):
        issues.append(f"spec.service.port must be an integer, got {svc.get('port')!r}")
    routing = spec.get("routing")
    if routing is not None:
        unknown(routing, CRD["routing"], "spec.routing")
        unknown(routing.get("tls") or {}, CRD["tls"], "spec.routing.tls")
        for i, r in enumerate(routing.get("routes") or []):
            unknown(r, CRD["route"], f"spec.routing.routes[{i}]")
    else:
        issues.append("spec.routing omitted: the operator creates no HTTPRoute and treats TLS as disabled (set routing.routes and routing.tls explicitly)")
    auth = spec.get("auth") or {}
    unknown(auth, CRD["auth"], "spec.auth")
    if auth.get("forwardAccessToken") and auth.get("enforceAtGateway") is False:
        issues.append("auth.forwardAccessToken requires enforceAtGateway: true")
    scopes = set(auth.get("scopes") or ["openid", "profile", "email"])
    if auth.get("enabled") and auth.get("groups") and "groups" not in scopes:
        issues.append("auth.groups is set but 'groups' is not in auth.scopes: the operator only attaches the Keycloak groups scope/mapper when it is requested, "
                      "so the token carries no groups claim and a group-gated SecurityPolicy denies every login (verified on a NIC cluster 2026-10-09); add groups to auth.scopes")
    if spec.get("gateway") not in (None, "public", "internal"):
        issues.append(f"spec.gateway must be public|internal, got {spec.get('gateway')!r}")
    lp = spec.get("landingPage") or {}
    unknown(lp, CRD["landingPage"], "spec.landingPage")
    unknown(lp.get("healthCheck") or {}, CRD["healthCheck"], "spec.landingPage.healthCheck")
    if lp.get("enabled") and not lp.get("displayName"):
        issues.append("landingPage.enabled requires displayName")
    if lp.get("displayName") and len(str(lp["displayName"])) > 64:
        issues.append("landingPage.displayName exceeds 64 chars")
    if lp.get("description") and len(str(lp["description"])) > 256:
        issues.append("landingPage.description exceeds 256 chars")
    return issues


# ----------------------------------------------------------------------------- CI analysis


def ci_files(root: Path) -> dict[str, str]:
    out = {}
    for p in list((root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github" / "workflows").is_dir() else []:
        out[str(p.relative_to(root))] = read(p)
    for name in (".gitlab-ci.yml", ".gitlab-ci.yaml", "Jenkinsfile", ".circleci/config.yml", "azure-pipelines.yml"):
        if (root / name).exists():
            out[name] = read(root / name)
    for p in (root / ".gitlab").rglob("*.yml") if (root / ".gitlab").is_dir() else []:
        out[str(p.relative_to(root))] = read(p)
    return out


def ci_grep(ci: dict[str, str], pattern: str) -> list[str]:
    rx = re.compile(pattern, re.I | re.S)
    return [f for f, t in ci.items() if rx.search(t)]


# ----------------------------------------------------------------------------- the scan


def scan(args) -> dict:
    checklist = yaml.safe_load(read(CHECKLIST))
    src = Path(args.repo).resolve()
    root, git = prepare_repo(src, args.ref)
    name = args.name or src.name
    workdir = Path(tempfile.mkdtemp(prefix="pack-audit-work-"))
    R = Result()
    R.facts["name"] = name
    R.facts["git"] = git
    R.facts["tools"] = {t: have(t) for t in ("helm", "kubeconform", "kubectl", "check-jsonschema")}
    R.facts["scanned_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # ---- inventory
    top = sorted(p.name for p in root.iterdir() if p.name != ".git")
    R.facts["top_level"] = top
    charts = find_charts(root)
    kustomizations = rglob(root, ("kustomization.yaml", "kustomization.yml"))
    plain_nebariapps = [p for p in rglob(root, ("*.yaml", "*.yml")) if "kind: NebariApp" in read(p, 20000)
                        and "templates" not in p.parts]
    R.facts["charts"] = [str(c.relative_to(root)) or "." for c in charts]
    R.facts["kustomizations"] = [str(p.relative_to(root)) for p in kustomizations]
    R.facts["plain_nebariapp_manifests"] = [str(p.relative_to(root)) for p in plain_nebariapps]

    # primary chart = chart with a NebariApp template, else the shallowest
    primary, nk = None, {}
    app_charts = [c for c in charts if (load_yaml(c / "Chart.yaml") or {}).get("type") != "library"] or charts
    for c in app_charts:
        h = chart_has_nebariapp(c)
        if h["template"] or h["library_dep"]:
            primary, nk = c, h
            break
    if primary is None and app_charts:
        # no NebariApp anywhere: audit the chart with the most templates (the application, not a helper)
        primary = max(app_charts, key=lambda c: (len(list((c / "templates").rglob("*.yaml"))) if (c / "templates").exists() else 0, -len(c.parts), -len(c.name)))
        nk = chart_has_nebariapp(primary)
    R.facts["primary_chart"] = (str(primary.relative_to(root)) or ".") if primary else None
    R.facts["nebariapp_wiring"] = nk
    if len(charts) > 1:
        R.observe("info", f"{len(charts)} top-level charts found; auditing '{R.facts['primary_chart']}' as the pack chart",
                  ", ".join(R.facts["charts"]))

    # ---- static files
    readme = next((root / n for n in ("README.md", "README.rst", "README") if (root / n).exists()), None)
    readme_txt = read(readme) if readme else ""
    docs_md = {str(p.relative_to(root)): read(p) for p in rglob(root, ("*.md",)) if "docs/site" not in str(p)
               and "node_modules" not in str(p)}
    all_md = {"README.md": readme_txt, **docs_md} if readme else docs_md
    chart_readme = read(primary / "README.md") if primary and (primary / "README.md").exists() else ""
    if chart_readme:
        all_md.setdefault(f"{R.facts['primary_chart']}/README.md", chart_readme)
    codeowners = next((root / n for n in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS") if (root / n).exists()), None)
    meta_p = root / "pack-metadata.yaml"
    meta = load_yaml(meta_p) if meta_p.exists() else None
    ci = ci_files(root)
    R.facts["ci_files"] = sorted(ci)
    license_ = any((root / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"))
    security_md = (root / "SECURITY.md").exists()
    changelog = next((root / n for n in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md") if (root / n).exists()), None)

    # ---- OI-01 template layout
    missing = []
    if not (charts or kustomizations or plain_nebariapps):
        missing.append("no Helm chart / kustomization / manifests")
    if not readme:
        missing.append("README.md")
    if not license_:
        missing.append("LICENSE")
    if not meta_p.exists():
        missing.append("pack-metadata.yaml")
    # CI wiring is not an Experimental-gate requirement (IN-06/IN-07/RE-07 score it; an observation notes its absence).
    if not (nk.get("template") or nk.get("library_dep") or plain_nebariapps):
        missing.append("NebariApp resource")
    layout_ok = primary is not None and (R.facts["primary_chart"] in (".", "chart") or (primary / "templates").exists())
    if not missing and layout_ok:
        R.set("OI-01", "PASS", "chart + NebariApp + README + LICENSE + pack-metadata present" + ("" if ci else "; no CI workflow (noted)"))
    elif len(missing) <= 2 and (charts or kustomizations):
        R.set("OI-01", "PARTIAL", "deployable unit present but template files missing: " + ", ".join(missing))
    else:
        R.set("OI-01", "FAIL", "missing: " + ", ".join(missing) if missing else "non-standard layout")
    if not security_md:
        R.observe("low", "No SECURITY.md (template ships one with a private vulnerability-reporting path)")
    if not (root / ".editorconfig").exists():
        R.observe("info", "No .editorconfig (template convention)")

    # ---- OI-02 CODEOWNERS
    if codeowners:
        owners = [ln for ln in read(codeowners).splitlines() if ln.strip() and not ln.strip().startswith("#") and "@" in ln]
        R.set("OI-02", "PASS" if owners else "FAIL", f"{codeowners.relative_to(root)}: {len(owners)} owner line(s)", owners[:5])
    else:
        R.set("OI-02", "FAIL", "no CODEOWNERS file")

    # ---- OI-03 / OI-06 / PS-03 pack-metadata
    if meta is None:
        R.set("OI-03", "FAIL", "pack-metadata.yaml missing at repo root")
        R.set("OI-06", "FAIL", "pack-metadata.yaml missing")
        R.set("PS-03", "MANUAL", "pre-sales gate (unscored); pack-metadata.yaml missing")
        R.set("NA-03", "FAIL", "pack-metadata.yaml missing (nebariapp_integration undeclared)")
    else:
        errs = []
        try:
            import jsonschema  # type: ignore
            schema = json.loads(read(SCHEMA))
            v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
            errs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in v.iter_errors(meta)]
        except ImportError:
            errs = ["jsonschema module unavailable; schema not validated"]
        declared = {k: meta.get(k) for k in ("level", "owner", "nebariapp_integration", "scope", "product_owner", "docs_site")}
        R.facts["pack_metadata"] = declared
        req_missing = [k for k in ("level", "owner") if not meta.get(k)] + (
            [] if deep_get(meta, "scope.standalone-supported") is not None else ["scope.standalone-supported"])
        if not errs and not req_missing:
            R.set("OI-03", "PASS", f"valid; level={meta.get('level')} owner={meta.get('owner')} integration={meta.get('nebariapp_integration')}")
        elif errs:
            R.set("OI-03", "FAIL", "schema errors: " + "; ".join(errs[:4]), errs)
        else:
            R.set("OI-03", "PARTIAL", "valid but missing: " + ", ".join(req_missing))
        R.set("OI-06", "PASS" if meta.get("product_owner") else "FAIL", f"product_owner={meta.get('product_owner')!r}")
        R.set("PS-03", "MANUAL", "pre-sales gate (unscored); demo_notes " + ("present" if "demo_notes" in meta else "absent"))

    # ---- helm work
    helm = None
    enabled_docs, disabled_docs, enabled_a, disabled_a = [], [], None, None
    if primary and have("helm"):
        helm = HelmRun(root, primary, nk, [Path(v).resolve() for v in (args.values or [])], list(args.set or []), workdir)
        helm.deps()
        vk = nk.get("values_key")
        on_sets = [f"{vk}.enabled=true"] + ([f"{vk}.hostname={AUDIT_HOST}"] if nk.get("hostname_key") or vk else []) if vk else []
        off_sets = [f"{vk}.enabled=false"] if vk else []
        r_on = helm.render("nebariapp-enabled", on_sets)
        r_off = helm.render("nebariapp-disabled", off_sets)
        lint_values = r_on["values"] if r_on["ok"] else (r_off["values"] if r_off["ok"] else [])
        helm.run_lint(lint_values, r_on["sets"] if r_on["ok"] else [])
        helm.run_package()
        enabled_docs = load_all_yaml(r_on["stdout"]) if r_on["ok"] else []
        disabled_docs = load_all_yaml(r_off["stdout"]) if r_off["ok"] else []
        enabled_a = analyze_manifests(enabled_docs) if enabled_docs else None
        disabled_a = analyze_manifests(disabled_docs) if disabled_docs else None
        R.facts["helm"] = {"log": helm.log, "dep_ok": helm.dep_ok, "lint_ok": helm.lint["ok"] if helm.lint else None,
                           "package_ok": helm.package_ok,
                           "renders": {k: {kk: vv for kk, vv in v.items() if kk != "stdout"} for k, v in helm.renders.items()}}
        if (workdir / "rendered").exists() or True:
            (workdir / "rendered").mkdir(exist_ok=True)
            for k, v in helm.renders.items():
                if v["ok"]:
                    (workdir / "rendered" / f"{k}.yaml").write_text(v["stdout"])
        R.facts["rendered_dir"] = str(workdir / "rendered")
        # schema validation: kubeconform binary, else the pure-Python kubernetes-validate package, else skipped
        target = workdir / "rendered" / ("nebariapp-enabled.yaml" if r_on["ok"] else "nebariapp-disabled.yaml")
        if target.exists() and have("kubeconform"):
            rc, out, err = sh(["kubeconform", "-strict", "-ignore-missing-schemas", "-summary", str(target)], timeout=300)
            R.facts["kubeconform"] = {"tool": "kubeconform", "ok": rc == 0, "output": (out + err).strip()[-1500:]}
        elif target.exists():
            try:
                import kubernetes_validate  # type: ignore
                errs = []
                for d in load_all_yaml(target.read_text()):
                    if d.get("kind") in ("NebariApp", "__parse_error__") or "." in str(d.get("apiVersion", "")).split("/")[0] and "k8s.io" not in str(d.get("apiVersion")):
                        continue  # CRDs have no bundled schema
                    try:
                        kubernetes_validate.validate(d, "1.30", strict=True)
                    except kubernetes_validate.ValidationError as e:  # noqa: PERF203
                        errs.append(f"{d.get('kind')}/{deep_get(d, 'metadata.name')}: {str(e)[:160]}")
                    except kubernetes_validate.SchemaNotFoundError:
                        pass
                R.facts["kubeconform"] = {"tool": "kubernetes-validate", "ok": not errs, "output": "\n".join(errs[-20:])}
            except ImportError:
                R.facts["kubeconform"] = {"tool": None, "ok": None, "output": "no kubeconform on PATH and kubernetes-validate not installed; schema validation skipped"}
    elif primary:
        R.observe("warn", "helm not on PATH: render-based checks were skipped")

    analysis = enabled_a or disabled_a
    R.facts["analysis_mode"] = "nebariapp-enabled" if enabled_a else ("nebariapp-disabled" if disabled_a else None)
    R.facts["analysis"] = None
    if analysis:
        R.facts["analysis"] = {k: v for k, v in analysis.items() if k != "nebariapps"}
        R.facts["analysis"]["nebariapp_specs"] = [n.get("spec") for n in analysis["nebariapps"]]

    # ---- IN-03 lint / IN-04 template
    lint_ci = ci_grep(ci, r"helm\s+lint")
    if helm and helm.lint:
        if helm.lint["ok"]:
            R.set("IN-03", "PASS", "helm lint ok" + (f"; CI runs it in {', '.join(lint_ci)}" if lint_ci else "; not wired to CI (noted)"))
        else:
            R.set("IN-03", "FAIL", "helm lint fails: " + helm.lint["output"][-300:].replace("\n", " | "))
    else:
        R.set("IN-03", "PARTIAL" if lint_ci else "FAIL", "could not run helm lint" + ("; CI references it" if lint_ci else ""))
    if helm:
        on, off = helm.renders["nebariapp-enabled"], helm.renders["nebariapp-disabled"]
        na_on = bool(enabled_a and enabled_a["nebariapps"])
        na_off = bool(disabled_a and disabled_a["nebariapps"])
        det = [f"enabled: {'ok' if on['ok'] else 'FAIL ' + on['stderr'][-200:]}", f"disabled: {'ok' if off['ok'] else 'FAIL ' + off['stderr'][-200:]}",
               f"NebariApp rendered when enabled: {na_on}; when disabled: {na_off}"]
        if on["values"]:
            det.append("rendered with values: " + ", ".join(str(Path(v).resolve()).replace(str(root.resolve()) + '/', '') for v in on["values"]))
        if not nk.get("values_key"):
            R.set("IN-04", "FAIL", "no nebariapp toggle in values.yaml (NebariApp cannot be enabled/disabled)", det)
        elif on["ok"] and off["ok"] and na_on and not na_off:
            R.set("IN-04", "PASS", "both modes render; NebariApp gated correctly", det)
        elif on["ok"] and off["ok"]:
            R.set("IN-04", "PARTIAL", "both render but NebariApp gating is wrong", det)
        elif on["ok"] or off["ok"]:
            R.set("IN-04", "PARTIAL", "only one mode renders", det)
        else:
            R.set("IN-04", "FAIL", "helm template fails in both modes", det)
        if on["values"] or off["values"]:
            R.observe("info", "Default values.yaml does not render on its own; a profile values file was needed",
                      ", ".join(sorted({str(Path(v).resolve()).replace(str(root.resolve()) + '/', '') for v in on["values"] + off["values"]})))
    else:
        R.set("IN-04", "FAIL" if primary else "NA", "no helm render performed" if primary else "no Helm chart")

    # ---- IN-05 kubeconform in CI
    scripts = {str(p.relative_to(root)): read(p) for d in ("scripts", "ci", "hack", "Makefile", "Taskfile.yml", "justfile")
               for p in ((root / d).rglob("*") if (root / d).is_dir() else ([root / d] if (root / d).is_file() else [])) if p.is_file()}
    def script_grep(pattern):
        rx = re.compile(pattern, re.I | re.S)
        return [f for f, t in scripts.items() if rx.search(t)]
    kc = ci_grep(ci, r"kubeconform|kubeval|--dry-run=server|pluto|kube-score|datree")
    kc_s = script_grep(r"kubeconform|kubeval|--dry-run=server|pluto|kube-score|datree")
    kcf = R.facts.get("kubeconform") or {}
    ci_note = ("; CI also runs it in " + ", ".join(kc)) if kc else ("; scripts run it: " + ", ".join(kc_s[:2]) if kc_s else "; no CI/script runs it (noted)")
    if kcf.get("ok") is True:
        R.set("IN-05", "PASS", f"{kcf['tool']}: {kcf['output'].splitlines()[-1] if kcf.get('output') else 'valid'}" + ci_note)
    elif kcf.get("ok") is False:
        R.set("IN-05", "FAIL", f"{kcf['tool']} reports invalid manifests" + ci_note, kcf.get("output", "").splitlines()[-8:])
    else:
        R.set("IN-05", "MANUAL", "no kubeconform/kubernetes-validate available to the auditor; run kubeconform -strict over helm template output" + ci_note)
    kcf = R.facts.get("kubeconform")
    if kcf and kcf["ok"] is False:
        R.observe("warn", f"{kcf['tool']} reports errors on the rendered manifests", kcf["output"][-600:])
    elif kcf and kcf["ok"] is None:
        R.observe("info", "Schema validation skipped", kcf["output"])

    # ---- IN-06 / IN-07 integration & standalone tests
    integ = ci_grep(ci, r"nebari-operator/releases|install\.yaml.*nebari-operator|action-nebari-sandbox[\s\S]{0,2000}?(nebariapp|NebariApp)|wait[^\n]*nebariapp|nebariapp[^\n]*Ready")
    integ_s = script_grep(r"nebari-operator|action-nebari-sandbox|nebariapp.*ready")
    R.set("IN-06", "PASS" if integ else ("PARTIAL" if integ_s else "FAIL"),
          ("NIC integration test in " + ", ".join(integ)) if integ else (f"operator integration only in local scripts, not CI: {integ_s[:3]}" if integ_s else "no CI job exercises the nebari-operator / NebariApp Ready condition"))
    standalone_declared = str(deep_get(meta or {}, "scope.standalone-supported", "")).lower() in ("yes", "true")
    off_ok = bool(helm and helm.renders["nebariapp-disabled"]["ok"])
    declared_no = str(deep_get(meta or {}, "scope.standalone-supported", "")).lower() in ("no", "false")
    standalone = False if declared_no else (standalone_declared or off_ok)
    R.facts["standalone_supported"] = standalone
    st = ci_grep(ci, r"(kind-action|kind create|k3d|minikube).*?(helm install|kubectl apply)|helm install.*--wait")
    if not standalone:
        R.set("IN-07", "NA", "standalone-supported: no")
    else:
        R.set("IN-07", "PASS" if st else "FAIL", ("standalone kind install in " + ", ".join(st)) if st else "no CI job installs the chart on a throwaway cluster")

    # ---- NA-01 precheck, NA-03, NA-06
    na_specs = (enabled_a or {}).get("nebariapps", []) if enabled_a else []
    if na_specs:
        issues = []
        for n in na_specs:
            issues += validate_nebariapp(n, enabled_a["services"])
        spec = na_specs[0].get("spec") or {}
        auth_on = bool(deep_get(spec, "auth.enabled"))
        R.facts["nebariapp_auth_enabled"] = auth_on
        R.facts["nebariapp_enforce_gateway"] = deep_get(spec, "auth.enforceAtGateway", True)
        det = [f"hostname={spec.get('hostname')} service={deep_get(spec, 'service.name')}:{deep_get(spec, 'service.port')} "
               f"gateway={spec.get('gateway', 'public')} auth={auth_on} enforceAtGateway={deep_get(spec, 'auth.enforceAtGateway', True)} "
               f"landingPage={deep_get(spec, 'landingPage.enabled', False)}"] + issues
        if issues:
            R.set("NA-01", "MANUAL", f"NebariApp renders but pre-check found {len(issues)} issue(s); cluster verification still required", det)
            for i in issues:
                R.observe("high" if "unknown field" in i or "does not match" in i else "warn", "NebariApp spec issue", i, "NebariApp")
        else:
            R.set("NA-01", "MANUAL", "NebariApp renders cleanly and the service reference resolves; needs cluster verification", det)
        routed = spec.get("routing") is not None
        depth = "full" if (auth_on and routed) else "partial"
        hard = [i for i in issues if "unknown field" in i or "does not match" in i or "omitted" in i or "missing" in i or "must be" in i or "not in auth.scopes" in i]
        if not issues:
            R.set("NA-07", "PASS", "NebariApp spec uses only CRD fields, routing is explicit, and service name/port resolve to a rendered Service")
        elif hard:
            R.set("NA-07", "FAIL", f"{len(hard)} blocking spec issue(s)", hard)
        else:
            R.set("NA-07", "PARTIAL", f"{len(issues)} advisory issue(s)", issues)
    else:
        R.set("NA-07", "FAIL", "no NebariApp rendered")
        depth = "none"
        why = "no NebariApp in the rendered output" if helm else "no NebariApp resource found"
        R.set("NA-01", "FAIL", why + "; the pack cannot get a route, TLS, or OIDC from the operator")
        R.facts["nebariapp_auth_enabled"] = False
    R.facts["detected_integration"] = depth
    if meta is not None:
        declared_i = meta.get("nebariapp_integration", "na")
        R.set("NA-03", "PASS" if declared_i == depth else "FAIL", f"declared={declared_i} detected={depth}")
    if analysis and analysis["workloads"]:
        if not analysis["probes_missing"]:
            R.set("NA-06", "PASS", f"liveness+readiness on all {analysis['probes_ok']} workload container(s)")
        elif analysis["probes_ok"]:
            R.set("NA-06", "PARTIAL", f"{len(analysis['probes_missing'])} container(s) lack probes", analysis["probes_missing"])
        else:
            R.set("NA-06", "FAIL", "no workload container has both probes", analysis["probes_missing"])
    elif analysis:
        R.set("NA-06", "NA", "no long-running workloads rendered")
    else:
        R.set("NA-06", "FAIL", "no render available to inspect")

    # ---- auth-dependent manual/judgment items
    auth_on = R.facts.get("nebariapp_auth_enabled", False)
    if na_specs and auth_on:
        enforce = R.facts.get("nebariapp_enforce_gateway", True)
        R.set("NA-04", "MANUAL", f"auth.enabled=true enforceAtGateway={enforce}; verify a 302 to Keycloak for anonymous requests"
              + ("" if enforce is not False else " (app-native OAuth: verify the app itself rejects anonymous calls)"))
        groups = deep_get(na_specs[0], "spec.auth.groups") or []
        R.set("NA-05", "MANUAL", f"groups={groups or 'none (any realm user)'}; verify with an in-group and an out-of-group user")
        scopes = set(deep_get(na_specs[0], "spec.auth.scopes") or ["openid", "profile", "email"])
        extra = scopes - MINIMAL_SCOPES
        R.set("SEC-03", "PASS" if not extra else "PARTIAL", f"scopes={sorted(scopes)}" + (f"; non-minimal: {sorted(extra)} need a documented reason" if extra else ""))
    elif na_specs:
        R.set("NA-04", "NA", "auth.enabled=false in the default render")
        R.set("NA-05", "NA", "auth.enabled=false in the default render")
        R.set("SEC-03", "NA", "auth disabled")
    else:
        R.set("NA-04", "FAIL", "no NebariApp; no gateway auth")
        R.set("NA-05", "FAIL", "no NebariApp; no gateway auth")
        R.set("SEC-03", "NA", "no NebariApp auth block")

    # ---- documentation
    deploy_cmd = re.search(r"helm (install|upgrade)|kubectl apply|argocd app|kind: Application", readme_txt)
    if readme and len(readme_txt) > 400 and deploy_cmd:
        R.set("DOC-01", "PASS", f"README ({len(readme_txt)} chars) with a deploy command ('{deploy_cmd.group(0)}')")
    elif readme and len(readme_txt) > 400:
        R.set("DOC-01", "PARTIAL", "README present but no helm install / kubectl apply / ArgoCD snippet")
    elif readme:
        R.set("DOC-01", "FAIL", f"README is a stub ({len(readme_txt)} chars)")
    else:
        R.set("DOC-01", "FAIL", "no README")
    pre, lim = has_heading(all_md, "prerequisites"), has_heading(all_md, "limitations")
    pre_readme = has_heading({"README.md": readme_txt}, "prerequisites")
    lim_readme = has_heading({"README.md": readme_txt}, "limitations")
    if pre_readme and lim_readme:
        R.set("DOC-02", "PASS", "README has both sections", pre_readme + lim_readme)
    elif pre and lim:
        R.set("DOC-02", "PARTIAL", "both sections exist but not in the README itself", pre[:2] + lim[:2])
    elif pre or lim:
        R.set("DOC-02", "PARTIAL", "only one of Prerequisites / Known Limitations found", pre[:2] + lim[:2])
    else:
        R.set("DOC-02", "FAIL", "neither Prerequisites nor Known Limitations section found")
    R.set("IN-02", "JUDGMENT", "auto pre-check: " + ("Prerequisites heading found" if pre else "no Prerequisites heading"), pre[:3])
    prose = [ln for ln in readme_txt.splitlines() if ln.strip() and not ln.lstrip().startswith(("<", "[![", "![", "#"))]
    R.set("OI-05", "JUDGMENT", f"README first prose line: {prose[0][:140] if prose else '(none)'}")
    auth_doc = has_heading(all_md, "auth")
    if na_specs and auth_on or (meta and meta.get("nebariapp_integration") == "full"):
        R.set("DOC-03", "JUDGMENT", "auto pre-check: " + (f"auth-related headings: {auth_doc[:3]}" if auth_doc else "no auth heading found"))
    else:
        R.set("DOC-03", "NA" if not na_specs and not auth_doc else "JUDGMENT", "no NebariApp auth" if not na_specs and not auth_doc else f"headings: {auth_doc[:3]}")
    ts = has_heading(all_md, "troubleshooting")
    R.set("DOC-04", "PASS" if ts else "FAIL", ("found: " + "; ".join(ts[:3])) if ts else "no Troubleshooting section")
    vals_doc = has_heading(all_md, "values")
    schema_json = primary and (primary / "values.schema.json").exists()
    R.set("DOC-05", "JUDGMENT", "auto pre-check: " + (f"values/config headings: {vals_doc[:3]}" if vals_doc else "no values reference heading")
          + ("; values.schema.json present" if schema_json else ""))
    sz = has_heading(all_md, "sizing")
    R.set("DOC-06", "PASS" if sz else "FAIL", ("found: " + "; ".join(sz[:3])) if sz else "no sizing/performance section")
    up = has_heading(all_md, "upgrade")
    R.set("DOC-07", "PASS" if up else "FAIL", ("found: " + "; ".join(up[:3])) if up else "no upgrade/rollback section")
    if changelog:
        entries = len(re.findall(r"^##+\s", read(changelog), re.M))
        R.set("DOC-08", "PASS" if entries else "PARTIAL", f"{changelog.name} with {entries} entries")
    else:
        R.set("DOC-08", "FAIL", "no CHANGELOG.md")
    astro = [p for p in rglob(root, ("astro.config.*",), skip=("/.git/", "/node_modules/"))]
    starlight = any("@nebari/starlight" in read(p) or "@nebari/starlight" in read(p.parent / "package.json") for p in astro)
    if astro and starlight:
        R.set("DOC-09", "PASS", f"Astro + @nebari/starlight at {astro[0].parent.relative_to(root)}")
    elif astro:
        R.set("DOC-09", "PARTIAL", "Astro docs site present but not on the shared @nebari/starlight theme")
    else:
        R.set("DOC-09", "FAIL", "no docs site (docs/site with Astro + @nebari/starlight)")

    # ---- examples
    ex_dir = next((root / d for d in ("examples", "values", "config/values") if (root / d).is_dir()), None)
    example_values = [p for p in (candidate_values_files(root, primary) if primary else [])]
    R.facts["example_values"] = [str(p.relative_to(root)) for p in example_values]
    rendered_with = set()
    if helm:
        for v in helm.renders.values():
            rendered_with.update(str(Path(x).resolve()).replace(str(root.resolve()) + '/', '') for x in v["values"])
    clean = [p for p in example_values if not PLACEHOLDER_RE.search(read(p))]
    if helm and helm.renders["nebariapp-enabled"]["ok"] and not helm.renders["nebariapp-enabled"]["values"]:
        R.set("EX-01", "PASS", "default values.yaml renders with only the hostname set")
    elif clean and helm and helm.renders["nebariapp-enabled"]["ok"]:
        used = [Path(v) for v in helm.renders["nebariapp-enabled"]["values"]]
        local_only = [str(v) for v in used if re.search(r"pullPolicy:\s*Never|LOCAL[- ]ONLY|kind proof|do not use in", read(v), re.I)]
        if local_only:
            R.set("EX-01", "PARTIAL", "the only example that renders is a local/kind-only profile: " + ", ".join(sorted(rendered_with)))
        else:
            R.set("EX-01", "PASS", "renders with example values: " + ", ".join(sorted(rendered_with)))
    elif example_values:
        R.set("EX-01", "PARTIAL", f"{len(example_values)} example values file(s) but they carry placeholders or did not render",
              [str(p.relative_to(root)) for p in example_values[:8]])
    else:
        R.set("EX-01", "FAIL", "no example values file")
    neb_ex = [p for p in example_values if re.search(r"nebari|prod|site", p.name, re.I)]
    R.set("EX-02", "PASS" if any("nebari" in p.name for p in neb_ex) else ("PARTIAL" if neb_ex else "FAIL"),
          ("Nebari example: " + ", ".join(p.name for p in neb_ex[:3])) if neb_ex else "no nebari-values.yaml example")
    if standalone:
        st_ex = [p for p in example_values if re.search(r"standalone|kind|local|bootstrap|test", p.name, re.I)]
        R.set("EX-03", "PASS" if any("standalone" in p.name for p in st_ex) else ("PARTIAL" if st_ex else "FAIL"),
              ("standalone example: " + ", ".join(p.name for p in st_ex[:3])) if st_ex else "no standalone-values.yaml example")
    else:
        R.set("EX-03", "NA", "standalone not supported")
    argo = [p for p in rglob(root, ("*.yaml", "*.yml")) if "kind: Application" in read(p, 20000) and "argoproj.io" in read(p, 20000)]
    if argo:
        t = read(argo[0])
        m = re.search(r"repoURL:\s*(\S+)", t)
        url = m.group(1) if m else ""
        placeholder = not url or url.startswith("<") or PLACEHOLDER_RE.search(url) or "YOUR-" in url or "example" in url
        R.set("EX-04", "PARTIAL" if placeholder else "PASS", f"{argo[0].relative_to(root)} repoURL={url or '(none)'}"
              + (" (placeholder: fill in the published repo/chart)" if placeholder else ""))
    else:
        R.set("EX-04", "FAIL", "no ArgoCD Application example")

    # ---- telemetry
    if analysis:
        mons = analysis["monitors"]
        scrape = analysis.get("scrape_annotated", [])
        otlp = analysis.get("otlp_env", [])
        just = any(re.search(r"servicemonitor|podmonitor|prometheus|/metrics|otlp|opentelemetry", t, re.I) for t in all_md.values())
        pod_scrape = [x for x in scrape if not x.startswith("Service/")]
        svc_scrape = [x for x in scrape if x.startswith("Service/")]
        if pod_scrape or otlp:
            R.set("TEL-01", "PASS", "metrics discoverable by NIC's OTel collector: " + (f"prometheus.io/scrape on pods {pod_scrape[:4]}" if pod_scrape else "") + (f" OTLP export env on {otlp[:4]}" if otlp else ""))
        elif svc_scrape:
            R.set("TEL-01", "PARTIAL", f"prometheus.io/scrape only on {svc_scrape[:3]}; NIC's collector discovers by pod (role: pod), so add the annotation to the pod template")
        else:
            R.set("TEL-01", "JUDGMENT", (f"ServiceMonitor/PodMonitor rendered ({mons}) but NIC's collector discovers via prometheus.io/scrape pod annotations or OTLP, not monitor CRDs" if mons
                                         else "no prometheus.io/scrape annotations, OTLP env, or monitor CRDs rendered")
                  + ("; docs mention metrics" if just else "; docs do not mention metrics"))
        R.set("TEL-03", "PASS" if analysis["grafana_dashboards"] or rglob(root, ("*dashboard*.json",)) else "FAIL",
              f"dashboards: {analysis['grafana_dashboards'] or [str(p.relative_to(root)) for p in rglob(root, ('*dashboard*.json',))][:3]}")
    else:
        R.set("TEL-01", "JUDGMENT", "no render; inspect templates for ServiceMonitor/PodMonitor")
        R.set("TEL-03", "FAIL", "no render / no dashboard found")
    logdoc = any(re.search(r"json\s*log|structured\s*log|log(ging)?\s*format|stdout", t, re.I) for t in all_md.values())
    R.set("TEL-02", "JUDGMENT", "docs mention structured/stdout logging" if logdoc else "docs do not mention log format")

    # ---- security
    if analysis and analysis["containers"]:
        nm = analysis["nonroot_missing"]
        R.set("SEC-01", "PASS" if not nm else ("PARTIAL" if len(nm) < analysis["containers"] else "FAIL"),
              f"{analysis['containers'] - len(nm)}/{analysis['containers']} containers declare non-root", nm[:12])
        hm = analysis["hardening_missing"]
        R.set("SEC-05", "PASS" if not hm else ("PARTIAL" if len(hm) < analysis["containers"] else "FAIL"),
              f"{analysis['containers'] - len(hm)}/{analysis['containers']} containers fully hardened", hm[:12])
        bad = analysis["unpinned"] + analysis["mutable"]
        # Tags injected on the audit command line (--set image.tag=...) are not repo state: also read the chart defaults.
        default_tags = re.findall(r"^[ \t]*tag:[ \t]*[\"']?([^\s\"'#]*)", read(primary / "values.yaml") if primary else "", re.M)
        weak_defaults = [t for t in default_tags if t == "" or t in MUTABLE_TAGS or PLACEHOLDER_RE.search(t)]
        injected = any(x.split("=")[0].endswith(("tag", ".digest")) for x in (args.set or []))
        if weak_defaults:
            bad = bad + [f"values.yaml default tag {t or '(empty)'!r}" for t in weak_defaults]
        R.set("SEC-04", "PASS" if not bad else ("PARTIAL" if len(bad) < len(analysis["images"]) + len(weak_defaults) else "FAIL"),
              f"{len(analysis['images']) - len(analysis['unpinned']) - len(analysis['mutable'])}/{len(analysis['images'])} rendered images pinned"
              + (f"; {len(weak_defaults)} weak default tag(s) in values.yaml" if weak_defaults else "")
              + ("; note: a tag was injected via --set for this audit" if injected else ""), bad[:12])
        R.set("SEC-06", "PASS" if analysis["networkpolicy"] else "FAIL",
              f"NetworkPolicy: {analysis['networkpolicy'][:4]}" if analysis["networkpolicy"] else "no NetworkPolicy rendered")
        if analysis["resources_missing"]:
            R.observe("warn", f"{len(analysis['resources_missing'])} container(s) without resource requests", ", ".join(analysis["resources_missing"][:8]))
        if analysis["ingress"] and na_specs:
            R.observe("warn", "Both an Ingress and a NebariApp render: two front doors for one app", ", ".join(analysis["ingress"]))
        elif analysis["ingress"] and not na_specs and R.facts["analysis_mode"] == "nebariapp-enabled":
            R.observe("high", "Pack fronts itself with an Ingress instead of a NebariApp; routing/TLS/OIDC bypass the operator", ", ".join(analysis["ingress"]))
        elif analysis["ingress"] and not na_specs:
            R.observe("warn", "Only the NebariApp-disabled profile rendered, and it uses an Ingress; the Nebari path could not be inspected", ", ".join(analysis["ingress"]))
    else:
        for cid in ("SEC-01", "SEC-04", "SEC-05", "SEC-06"):
            R.set(cid, "FAIL", "no rendered manifests to inspect")
    # hardcoded secrets: rendered Secret literals + values.yaml defaults
    lit = (analysis or {}).get("secrets_literal", []) if analysis else []
    if enabled_a and disabled_a:
        other = set(disabled_a["secrets_literal"] if analysis is enabled_a else enabled_a["secrets_literal"])
        other_keys = {x.split("=")[0] for x in other}
        generated = [x for x in lit if x.split("=")[0] in other_keys and x not in other]
        if generated:
            lit = [x for x in lit if x not in generated]
            R.observe("info", f"{len(generated)} Secret value(s) differ between renders: generated at render time (lookup/rand), not literal",
                      "Under ArgoCD, lookup is empty at render, so these regenerate on every sync unless guarded")
    vals_txt = read(primary / "values.yaml") if primary else ""
    vd = []
    for m in re.finditer(r"^[ \t]*([A-Za-z0-9_]*(password|secret|token|apikey|api_key|accesskey|secretkey)[A-Za-z0-9_]*):[ \t]*[\"']?([^\s\"'#{]+)", vals_txt, re.I | re.M):
        val = m.group(3)
        if val.lower() in ("", "~", "null", "false", "true", "{}", "[]") or re.match(r"^[A-Z][A-Z0-9_]+$", val):
            continue  # empty, or an env-var NAME rather than a value
        if not m.group(1).lower().endswith(("name", "ref", "key", "file", "existingsecret", "secretname", "mountpath", "path", "secret")) or m.group(1).lower().endswith(("password", "secretkey", "accesskey", "token")):
            vd.append(f"values.yaml {m.group(1)}={m.group(3)[:20]}")
    if not lit and not vd:
        R.set("SEC-02", "PASS", "no literal credentials in rendered Secrets or values.yaml defaults")
    elif all("CHANGEME" in x or "changeme" in x or "placeholder" in x.lower() for x in lit + vd):
        R.set("SEC-02", "PARTIAL", "placeholder credentials are baked into default Secrets (should fail the render or use existingSecret)", (lit + vd)[:10])
    else:
        R.set("SEC-02", "FAIL", f"{len(lit) + len(vd)} literal credential value(s) in defaults", (lit + vd)[:10])

    # ---- release engineering
    rel = ci_grep(ci, r"pack-release\.yaml|helm package|chart-releaser|helm push|oci://.*push|gh release create|helm repo index")
    pkg_ok = helm.package_ok if helm else None
    if pkg_ok:
        R.set("RE-01", "PASS", "helm package ok" + (f"; publish workflow in {', '.join(rel)}" if rel else "; no publish automation (noted)"))
    elif pkg_ok is False:
        R.set("RE-01", "FAIL", "helm package failed")
    else:
        R.set("RE-01", "FAIL" if primary else "NA", "helm package not run" if primary else "no chart")
    vtags = [t for t in git["tags"] if re.match(r"^v?\d+\.\d+\.\d+", t)]
    if git.get("shallow") and not vtags:
        R.set("RE-02", "MANUAL", "tags unknown (shallow clone); check the repo's releases" + (f"; release workflow in {', '.join(rel)}" if rel else ""))
    elif vtags:
        R.set("RE-02", "PASS", f"release tags {vtags[:5]}" + (f"; workflow in {', '.join(rel)}" if rel else ""))
    elif not git["is_git"]:
        R.set("RE-02", "FAIL", "not a git repository; no release history at all")
    else:
        R.set("RE-02", "FAIL", "no release tags" + ("; a release workflow exists but has never cut one" if rel else ""))
    cy = load_yaml(primary / "Chart.yaml") if primary else {}
    cy = cy if isinstance(cy, dict) else {}
    R.facts["chart_yaml"] = {k: cy.get(k) for k in ("name", "version", "appVersion", "description", "home", "maintainers", "dependencies")}
    R.set("RE-03", "JUDGMENT", f"version={cy.get('version')} appVersion={cy.get('appVersion')!r}; confirm appVersion equals the wrapped app release")
    ver = str(cy.get("version", "0"))
    ga_ver = re.match(r"^v?([1-9]\d*)\.", ver) is not None
    bad_tags = [t for t in git["tags"] if not re.match(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$", t)]
    if ga_ver and vtags and not bad_tags:
        R.set("RE-04", "PASS", f"chart {ver}; tags {vtags[:3]}")
    elif ga_ver or vtags:
        R.set("RE-04", "PARTIAL", f"chart {ver}; tags {git['tags'][:5] or 'none'}" + ("; non-conforming tags: " + ", ".join(bad_tags[:3]) if bad_tags else ""))
    else:
        R.set("RE-04", "FAIL", f"chart {ver}, no release tags")
    if ver.startswith("v"):
        R.observe("warn", f"Chart version '{ver}' carries a v prefix; Helm SemVer should be bare (tags get the v)")
    if not cy.get("appVersion"):
        R.observe("warn", "Chart.yaml has no appVersion")
    build = ci_grep(ci, r"pack-build-image|docker/build-push-action|docker build|buildx|kaniko|podman build")
    dockerfiles = rglob(root, ("Dockerfile", "Containerfile"))
    R.set("RE-05", "JUDGMENT", (f"image build workflow in {build}" if build else "no image build workflow")
          + (f"; {len(dockerfiles)} Dockerfile(s)" if dockerfiles else "; no Dockerfile (wraps upstream images)"))
    R.set("RE-06", "NA", "first-party only (nebari-dev helm-repository sync)")
    upg = ci_grep(ci, r"helm upgrade(?!\s+--install)(?!\s+-i\b)")
    upg_s = script_grep(r"helm upgrade(?!\s+--install)(?!\s+-i\b)")
    R.set("RE-07", "PASS" if upg else ("PARTIAL" if upg_s else "FAIL"),
          ("helm upgrade step in " + ", ".join(upg)) if upg else (f"helm upgrade only in scripts, not wired to CI: {upg_s[:3]}" if upg_s else "no install-then-upgrade smoke test in CI"))

    # ---- first-party / manual
    R.set("OI-04", "NA", "first-party only; vendor packs register with the platform team's catalog instead")
    R.set("IN-01", "MANUAL", "install on a NIC dev cluster from the README alone")
    for cid in ("PS-01", "PS-02", "PS-04", "SO-01", "SO-02", "SO-03", "SO-04", "SO-05"):
        R.set(cid, "MANUAL", "process gate; not a repository property")

    # ---- NA-02 judgment evidence
    if primary and nk.get("values_key"):
        vk = nk["values_key"]
        block = re.search(rf"^{re.escape(vk)}:\n((?:[ \t]+.*\n?)+)", vals_txt, re.M)
        comment_lines = len([ln for ln in (block.group(1).splitlines() if block else []) if ln.strip().startswith("#")])
        key_lines = len([ln for ln in (block.group(1).splitlines() if block else []) if re.match(r"\s+[A-Za-z]", ln) and ":" in ln])
        values_table = any(re.search(r"^\|[^\n]*(\bkey\b|\bvalue\b|\bparameter\b)[^\n]*(default|description)[^\n]*\|", t, re.I | re.M) for t in all_md.values())
        R.set("NA-02", "JUDGMENT", f"values.yaml '{vk}:' block has {key_lines} keys and {comment_lines} comment lines; a values reference table exists in docs: {values_table}")
    else:
        R.set("NA-02", "FAIL" if primary else "NA", "no nebariapp block in values.yaml")

    # ---- structural observations
    if git["is_git"] and args.ref:
        R.observe("high", f"Audited ref '{args.ref}'; the default branch holds only {git['default_branch_files']} file(s)",
                  "Merge the pack to main (or point the platform at the branch) before anyone consumes it")
    if (root / "charts").is_dir() and primary and primary.relative_to(root).parts[:1] == ("charts",):
        n = len([p for p in (root / "charts").iterdir() if p.is_dir()])
        R.observe("warn", f"Repo vendors {n} charts under charts/ instead of declaring upstream dependencies in one pack chart",
                  "Template pattern: one chart at chart/ with upstream charts as Chart.yaml dependencies")
    if ".gitlab-ci.yml" in ci and not any(k.startswith(".github/") for k in ci):
        R.observe("info", "CI is GitLab-only; the nebari-dev reusable workflows (pack-build-image, pack-release) are GitHub Actions",
                  "Fine for a vendor pack hosted on GitLab, but the equivalent lint/template/kubeconform/release steps must be reproduced")
    if not ci:
        R.observe("high", "No CI pipeline definition at all (no .github/workflows, no .gitlab-ci.yml)")
    if any("GENERATED" in t or "Do not edit" in t for t in (readme_txt,)) or (root / "GENERATED.md").exists():
        R.observe("info", "Repository is generated from another source tree; fixes must land upstream in the generator",
                  str(root / "GENERATED.md") if (root / "GENERATED.md").exists() else "")
    if readme_txt and PLACEHOLDER_RE.search(readme_txt) and "PROPOSAL" in readme_txt:
        R.observe("info", "README is marked PROPOSAL")

    # ---- assemble
    out = {"name": name, "source": str(src), "ref": args.ref, "facts": R.facts, "checks": R.checks,
           "observations": R.observations, "checklist_version": CHECKLIST.name}
    out["score"] = score(out, checklist)
    return out


# ----------------------------------------------------------------------------- scoring + report

VALUE = {"PASS": 1.0, "PARTIAL": 0.5, "FAIL": 0.0}
LEVEL_ORDER = ["E", "A", "B", "GA"]


def score(result: dict, checklist: dict) -> dict:
    items = {i["id"]: i for i in checklist["items"]}
    weights = {k: v["weight"] for k, v in checklist["levels"].items()}
    tot = got = 0.0
    per_level = {lv: {"scorable": 0, "earned": 0.0, "pass": 0, "partial": 0, "fail": 0, "judgment": 0, "manual": 0, "na": 0} for lv in LEVEL_ORDER}
    per_cat: dict[str, dict] = {}
    for cid, item in items.items():
        if item.get("enabled", True) is False:
            continue
        st = result["checks"].get(cid, {}).get("status", "JUDGMENT")
        lv = item["level"]
        cat = item["category"]
        pc = per_cat.setdefault(cat, {"scorable": 0, "earned": 0.0, "fail": [], "partial": []})
        pl = per_level[lv]
        if st in VALUE:
            w = weights[lv]
            tot += w
            got += w * VALUE[st]
            pl["scorable"] += 1
            pl["earned"] += VALUE[st]
            pc["scorable"] += 1
            pc["earned"] += VALUE[st]
            pl[st.lower()] += 1
            if st == "FAIL":
                pc["fail"].append(cid)
            elif st == "PARTIAL":
                pc["partial"].append(cid)
        else:
            pl[st.lower() if st.lower() in pl else "manual"] += 1
    achieved = None
    blockers: dict[str, list[str]] = {lv: [] for lv in LEVEL_ORDER}
    pending: dict[str, list[str]] = {lv: [] for lv in LEVEL_ORDER}
    for lv in LEVEL_ORDER:
        for cid, item in items.items():
            if item["level"] != lv or item.get("enabled", True) is False:
                continue
            st = result["checks"].get(cid, {}).get("status", "JUDGMENT")
            if st in ("FAIL", "PARTIAL"):
                blockers[lv].append(cid)
            elif st in ("JUDGMENT", "MANUAL"):
                pending[lv].append(cid)
    cum_ok = True
    for lv in LEVEL_ORDER:
        if blockers[lv]:
            cum_ok = False
        if cum_ok:
            achieved = lv
    return {"overall_pct": round(100 * got / tot, 1) if tot else None, "weighted_points": round(got, 2), "weighted_total": round(tot, 2),
            "runs_on_nebari": runs_on_nebari(result),
            "per_level": per_level, "per_category": {k: {**v, "pct": round(100 * v["earned"] / v["scorable"], 0) if v["scorable"] else None} for k, v in per_cat.items()},
            "repo_level": achieved, "blockers": blockers, "pending": pending}


def runs_on_nebari(result: dict) -> dict:
    """Three-state answer to 'will this run on Nebari?'.

    verified: installed on a cluster with the operator; NebariApp Ready (NA-01 PASS)
    likely:   NebariApp renders with only CRD fields, explicit routing, resolvable service (NA-07 PASS)
    no:       no NebariApp, or the spec fails the CRD pre-check
    A verified-but-failed cluster run reports 'no' with the operator's reason.
    """
    c = result["checks"]
    na01 = c.get("NA-01", {}).get("status")
    na07 = c.get("NA-07", {}).get("status")
    v = result["facts"].get("verify") or {}
    if na01 == "PASS":
        return {"state": "verified", "reason": c["NA-01"]["evidence"][:200]}
    if na01 == "FAIL" and v.get("ran"):
        return {"state": "no", "reason": "cluster run failed: " + c["NA-01"]["evidence"][:200]}
    if na07 == "PASS":
        return {"state": "likely", "reason": "static checks pass (NA-07); not yet installed on a cluster with the operator"}
    if na07 == "PARTIAL":
        return {"state": "likely", "reason": "advisory NebariApp spec issues (NA-07); not yet verified on a cluster"}
    return {"state": "no", "reason": c.get("NA-07", {}).get("evidence", "no NebariApp")[:200]}


def level_name(checklist, lv):
    return checklist["levels"][lv]["name"] if lv else "none (Experimental gate not met)"


def render_report(result: dict, checklist: dict) -> str:
    items = {i["id"]: i for i in checklist["items"]}
    cats = checklist["categories"]
    sc = result["score"]
    f = result["facts"]
    L = []
    L.append(f"# Software-pack audit: {result['name']}\n")
    L.append(f"- Source: `{result['source']}`" + (f" @ `{result['ref']}`" if result.get("ref") else "") + (f" (commit {f['git'].get('head')})" if f["git"].get("head") else ""))
    L.append(f"- Scanned: {f['scanned_at']}  |  tools: " + ", ".join(k for k, v in f["tools"].items() if v)
             + (f"  |  schema validation: {f['kubeconform'].get('tool') or 'skipped'}" if f.get("kubeconform") else ""))
    L.append(f"- Primary chart: `{f.get('primary_chart')}`  version {deep_get(f, 'chart_yaml.version')}  appVersion {deep_get(f, 'chart_yaml.appVersion')!r}")
    L.append(f"- NebariApp integration detected: **{f.get('detected_integration')}**  |  declared: {deep_get(f, 'pack_metadata.nebariapp_integration', '(no pack-metadata.yaml)')}  |  manifests inspected from the {f.get('analysis_mode') or 'no'} render")
    L.append("")
    L.append("## Readiness score\n")
    ron = sc.get("runs_on_nebari") or runs_on_nebari(result)
    L.append(f"**Overall: {sc['overall_pct']}%**  (weighted {sc['weighted_points']}/{sc['weighted_total']})  |  "
             f"**Repo-readiness level: {level_name(checklist, sc['repo_level'])}**  |  "
             f"**Runs on Nebari: {ron['state']}** ({ron['reason']})\n")
    L.append("| Level | Scorable | Pass | Partial | Fail | Pending judgment | Manual | Gate |")
    L.append("|---|---|---|---|---|---|---|---|")
    cum = True
    for lv in LEVEL_ORDER:
        p = sc["per_level"][lv]
        gate = "open" if cum and not sc["blockers"][lv] else ("blocked" if sc["blockers"][lv] else "blocked upstream")
        if sc["blockers"][lv]:
            cum = False
        L.append(f"| {level_name(checklist, lv)} | {p['scorable']} | {p['pass']} | {p['partial']} | {p['fail']} | {p['judgment']} | {p['manual']} | {gate} |")
    L.append("")
    L.append("| Category | Score | Failing | Partial |")
    L.append("|---|---|---|---|")
    for cid, c in sc["per_category"].items():
        if c["scorable"]:
            L.append(f"| {cats[cid]} | {c['pct']:.0f}% | {', '.join(c['fail']) or '-'} | {', '.join(c['partial']) or '-'} |")
    L.append("")
    L.append("## Blockers by level\n")
    for lv in LEVEL_ORDER:
        b = sc["blockers"][lv]
        pend = sc["pending"][lv]
        if b or pend:
            L.append(f"**{level_name(checklist, lv)}**")
            for cid in b:
                ch = result["checks"][cid]
                L.append(f"- [{ch['status']}] {cid} {items[cid]['title']}: {ch['evidence']}")
            for cid in pend:
                ch = result["checks"][cid]
                L.append(f"- [{ch['status']}] {cid} {items[cid]['title']}: {ch['evidence']}")
            L.append("")
    L.append("## Observations (outside the checklist)\n")
    if result["observations"]:
        for o in sorted(result["observations"], key=lambda o: {"high": 0, "warn": 1, "low": 2, "info": 3}.get(o["severity"], 4)):
            L.append(f"- **{o['severity']}** {o['title']}" + (f": {o['detail']}" if o["detail"] else ""))
    else:
        L.append("- none")
    L.append("")
    L.append("## Full checklist\n")
    L.append("| ID | Lvl | Status | Item | Evidence | Source |")
    L.append("|---|---|---|---|---|---|")
    for cid, item in items.items():
        if item.get("enabled", True) is False:
            continue
        ch = result["checks"].get(cid, {"status": "JUDGMENT", "evidence": ""})
        ev = ch["evidence"].replace("|", "\\|").replace("\n", " ")
        src = (f"[rule]({item['source']})" if item.get("source") else "") + (f" · [example]({item['example']})" if item.get("example") else "")
        L.append(f"| {cid} | {item['level']} | {ch['status']} | {item['title']} | {ev} | {src} |")
    excluded = [cid for cid, it in items.items() if it.get("enabled", True) is False]
    if excluded:
        L.append("\nExcluded from scoring (disabled in checklist.yaml): " + ", ".join(f"{cid} {items[cid]['title']} [{result['checks'].get(cid, {}).get('status', '-')}]" for cid in excluded))
    L.append("")
    L.append("## Evidence details\n")
    for cid, ch in result["checks"].items():
        if ch.get("detail"):
            L.append(f"- **{cid}**")
            for d in ch["detail"][:12]:
                L.append(f"  - {str(d).replace(chr(10), ' ')}")
    if f.get("helm"):
        L.append("\n### helm log\n")
        for ln in f["helm"]["log"]:
            L.append(f"- {ln}")
        for k, v in f["helm"]["renders"].items():
            if not v["ok"]:
                L.append(f"- {k} first error: `{v['first_error'][-300:]}`")
    if f.get("analysis"):
        a = f["analysis"]
        L.append("\n### rendered inventory\n")
        L.append(f"- kinds: {a['kinds']}")
        L.append(f"- images: {sorted(set(a['images']))[:20]}")
        L.append(f"- services: {a['services']}")
        for s in a.get("nebariapp_specs") or []:
            L.append(f"- NebariApp spec: `{json.dumps(s)[:900]}`")
    return "\n".join(L) + "\n"


def render_summary(results: list[dict], checklist: dict) -> str:
    items = {i["id"]: i for i in checklist["items"]}
    cats = checklist["categories"]
    L = ["# Software-pack readiness summary\n",
         f"Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} from {len(results)} audit(s). "
         "Scores weight items by the level at which they block promotion (E=4, A=3, B=2, GA=1); "
         "the repo-readiness level is the highest level with no FAIL/PARTIAL at or below it. "
         "MANUAL (cluster/person) and NA items are excluded.\n"]
    L.append("| Pack | Score | Repo-readiness level | Runs on Nebari | Integration | E blockers | A blockers | B blockers | GA blockers | Pending judgment |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda r: -(r["score"]["overall_pct"] or 0)):
        sc = r["score"]
        b = {lv: len(sc["blockers"][lv]) for lv in LEVEL_ORDER}
        pend = sum(len([c for c in sc["pending"][lv] if r["checks"].get(c, {}).get("status") == "JUDGMENT"]) for lv in LEVEL_ORDER)
        L.append(f"| {r['name']} | {sc['overall_pct']}% | {level_name(checklist, sc['repo_level'])} | {(sc.get('runs_on_nebari') or runs_on_nebari(r))['state']} | {r['facts'].get('detected_integration')} | "
                 f"{b['E']} | {b['A']} | {b['B']} | {b['GA']} | {pend} |")
    L.append("")
    L.append("## Category scores\n")
    L.append("| Category | " + " | ".join(r["name"] for r in results) + " |")
    L.append("|---|" + "---|" * len(results))
    for cid, cname in cats.items():
        row = []
        for r in results:
            c = r["score"]["per_category"].get(cid)
            row.append(f"{c['pct']:.0f}%" if c and c.get("pct") is not None else "-")
        if any(x != "-" for x in row):
            L.append(f"| {cname} | " + " | ".join(row) + " |")
    L.append("")
    L.append("## Shared gaps (failing or partial in every pack)\n")
    common = [cid for cid in items if items[cid].get("enabled", True) is not False and all(r["checks"].get(cid, {}).get("status") in ("FAIL", "PARTIAL") for r in results)]
    for cid in common:
        L.append(f"- {cid} [{items[cid]['level']}] {items[cid]['title']}")
    if not common:
        L.append("- none")
    L.append("")
    L.append("## Experimental-gate blockers per pack\n")
    for r in results:
        L.append(f"**{r['name']}**")
        for lv in ("E", "A"):
            for cid in r["score"]["blockers"][lv]:
                ch = r["checks"][cid]
                L.append(f"- [{lv}] [{ch['status']}] {cid} {items[cid]['title']}: {ch['evidence'][:160]}")
        L.append("")
    L.append("## High-severity observations\n")
    for r in results:
        hi = [o for o in r["observations"] if o["severity"] in ("high", "warn")]
        if hi:
            L.append(f"**{r['name']}**")
            for o in hi:
                L.append(f"- {o['severity']}: {o['title']}" + (f" ({o['detail'][:140]})" if o["detail"] else ""))
            L.append("")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------- verify (kind + operator)


def verify(args, checklist: dict) -> dict:
    """Install the pack on a local kind cluster that runs the Nebari stack and record NA-01 / NA-04.

    The cluster comes from the template's dev/Makefile `cluster` target (MetalLB, Envoy Gateway,
    cert-manager, Keycloak, nebari-operator). Nothing here touches the pack repository.
    """
    res = json.loads(read(Path(args.results)))
    f = res["facts"]
    name = res["name"]
    host = args.hostname or f"{re.sub(r'[^a-z0-9-]', '-', name.lower())[:40]}.nebari.local"
    ns = args.namespace or f"{re.sub(r'[^a-z0-9-]', '-', name.lower())[:40]}-verify"
    release = "verify"
    chart = Path(res["source"]) / (f.get("primary_chart") or ".")
    if res.get("ref"):
        chart = None  # branch content lives in a temp export; the caller must pass --chart
    if args.chart:
        chart = Path(args.chart)
    if chart is None or not (chart / "Chart.yaml").exists():
        sys.exit("verify: pass --chart <dir> pointing at the pack chart (branch audits export to a temp dir)")
    vk = (f.get("nebariapp_wiring") or {}).get("values_key")
    if not vk:
        sys.exit("verify: this pack has no nebariapp toggle in values.yaml; nothing to verify (runs_on_nebari=no)")
    ren = ((f.get("helm") or {}).get("renders") or {}).get("nebariapp-enabled") or {}
    values = [v for v in ren.get("values", [])] + list(args.values or [])
    sets = [x for x in ren.get("sets", []) if not x.startswith(f"{vk}.hostname=")] + list(args.set or [])
    sets = [x for x in sets if not x.startswith(f"{vk}.enabled=")] + [f"{vk}.enabled=true", f"{vk}.hostname={host}"]
    dev_dir = Path(args.template_dev or (HERE.parent.parent / "dev"))
    cluster = args.cluster
    plan = []

    def step(desc, cmd, timeout=600, check=True):
        plan.append((desc, cmd))
        if args.dry_run:
            print(f"[dry-run] {desc}\n    $ {' '.join(cmd)}")
            return 0, "", ""
        print(f"==> {desc}")
        rc, out, err = sh(cmd, timeout=timeout)
        if rc != 0 and check:
            print(err[-800:] or out[-800:])
        return rc, out, err

    for tool in ("docker", "kind", "kubectl", "helm", "make"):
        if not have(tool) and not args.dry_run:
            sys.exit(f"verify: {tool} is required on PATH")
    rc, out, _ = step("list kind clusters", ["kind", "get", "clusters"], check=False)
    if cluster not in out.split() or args.dry_run:
        if not (dev_dir / "Makefile").exists() and not args.dry_run:
            sys.exit(f"verify: template dev/Makefile not found at {dev_dir}; pass --template-dev")
        step("create the Nebari dev cluster (MetalLB, Envoy Gateway, cert-manager, Keycloak, operator); first run ~5-10 min",
             ["make", "-C", str(dev_dir), "cluster", f"CLUSTER_NAME={cluster}"], timeout=1800)
    step("use the kind context", ["kubectl", "config", "use-context", f"kind-{cluster}"])
    step("create namespace", ["kubectl", "create", "namespace", ns, "--dry-run=client", "-o", "yaml"], check=False)
    if not args.dry_run:
        sh(["bash", "-c", f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -"])
    step("opt the namespace in", ["kubectl", "label", "namespace", ns, "nebari.dev/managed=true", "--overwrite"])
    step("fetch chart dependencies", ["helm", "dependency", "build", str(chart)], check=False)
    cmd = ["helm", "upgrade", "--install", release, str(chart), "-n", ns, "--wait", "--timeout", args.timeout]
    for v in values:
        cmd += ["-f", v]
    for x in sets:
        cmd += ["--set", x]
    rc, out, err = step("install the pack with the NebariApp enabled", cmd, timeout=1500)
    result = {"ran": not args.dry_run, "cluster": cluster, "namespace": ns, "hostname": host, "install_ok": rc == 0,
              "install_error": err[-600:] if rc else "", "conditions": [], "nebariapps": [], "redirect": None}
    if not args.dry_run and rc != 0:
        result["conditions_note"] = "helm install failed"
    rc2, out2, _ = step("wait for the NebariApp Ready condition",
                        ["kubectl", "wait", "-n", ns, "nebariapp", "--all", "--for=condition=Ready", f"--timeout={args.timeout}"], timeout=900, check=False)
    rc3, out3, _ = step("collect NebariApp status", ["kubectl", "get", "nebariapp", "-n", ns, "-o", "json"], check=False)
    if not args.dry_run and rc3 == 0:
        try:
            items = json.loads(out3).get("items", [])
            for it in items:
                conds = {c["type"]: {"status": c.get("status"), "reason": c.get("reason"), "message": (c.get("message") or "")[:160]}
                         for c in (it.get("status", {}).get("conditions") or [])}
                result["nebariapps"].append({"name": it["metadata"]["name"], "conditions": conds})
        except Exception as e:  # noqa: BLE001
            result["conditions_note"] = f"could not parse NebariApp status: {e}"
    rc4, ip, _ = step("find the gateway IP",
                      ["kubectl", "get", "svc", "-n", "envoy-gateway-system", "-l", "gateway.envoyproxy.io/owning-gateway-name=nebari-gateway",
                       "-o", "jsonpath={.items[0].status.loadBalancer.ingress[0].ip}"], check=False)
    ip = ip.strip()
    if ip or args.dry_run:
        rc5, out5, _ = step("request the hostname through the gateway (expect 302 to Keycloak when auth is enabled, 200 otherwise)",
                            ["curl", "-k", "-sS", "-o", "/dev/null", "--max-time", "20", "--resolve", f"{host}:443:{ip or '<gateway-ip>'}",
                             "-w", "%{http_code} %{redirect_url}", f"https://{host}/"], timeout=60, check=False)
        if not args.dry_run and rc5 == 0:
            code, _, loc = out5.strip().partition(" ")
            result["redirect"] = {"code": code, "location": loc}
    if not args.keep and not args.dry_run:
        step("uninstall the release", ["helm", "uninstall", release, "-n", ns, "--wait"], check=False)
        step("delete the namespace", ["kubectl", "delete", "namespace", ns, "--wait=false"], check=False)
    if args.dry_run:
        print(f"[dry-run] would write NA-01 / NA-04 into {args.results} and rescore")
        return res
    # ---- write back
    ready = bool(result["nebariapps"]) and all(n["conditions"].get("Ready", {}).get("status") == "True" for n in result["nebariapps"])
    detail = [f"{n['name']}: " + ", ".join(f"{k}={v['status']}" + (f" ({v['reason']})" if v['status'] != 'True' and v.get('reason') else "") for k, v in n["conditions"].items()) for n in result["nebariapps"]]
    if ready:
        res["checks"]["NA-01"] = {"status": "PASS", "evidence": f"verified on kind cluster '{cluster}' with nebari-operator: NebariApp Ready; conditions " + "; ".join(detail)[:300], "detail": detail}
    else:
        why = result["install_error"][-200:] if not result["install_ok"] else ("; ".join(detail) or result.get("conditions_note") or "no NebariApp reached Ready")
        res["checks"]["NA-01"] = {"status": "FAIL", "evidence": f"cluster run on '{cluster}' did not reach Ready: {why}"[:400], "detail": detail}
    auth_on = bool(res["facts"].get("nebariapp_auth_enabled"))
    r = result["redirect"]
    if r and auth_on:
        ok = r["code"].startswith("3") and ("keycloak" in r["location"] or "/realms/" in r["location"] or "openid" in r["location"])
        res["checks"]["NA-04"] = {"status": "PASS" if ok else "FAIL", "evidence": f"anonymous GET https://{host}/ -> {r['code']} {r['location'][:120]}", "detail": []}
    elif r:
        res["checks"]["NA-04"] = {"status": "NA", "evidence": f"auth disabled; anonymous GET -> {r['code']}", "detail": []}
    res["facts"]["verify"] = result
    res["score"] = score(res, checklist)
    Path(args.results).write_text(json.dumps(res, indent=2, default=str))
    outp = Path(args.results).with_suffix(".md")
    md = render_report(res, checklist)
    if outp.exists() and "\n# Software-pack audit:" in outp.read_text():
        narrative = outp.read_text().split("\n# Software-pack audit:", 1)[0].rstrip()
        md = (narrative + "\n\n" + md) if narrative else md
    outp.write_text(md)
    print(f"{name}: runs_on_nebari={res['score']['runs_on_nebari']['state']}  NA-01={res['checks']['NA-01']['status']}  NA-04={res['checks'].get('NA-04', {}).get('status')}  -> {outp}")
    return res


# ----------------------------------------------------------------------------- export (SARIF / JUnit / CSV)

SARIF_LEVEL = {"FAIL": "error", "PARTIAL": "warning", "JUDGMENT": "note", "MANUAL": "note"}


def export(res: dict, checklist: dict, fmt: str) -> str:
    items = {i["id"]: i for i in checklist["items"] if i.get("enabled", True) is not False}
    cats = checklist["categories"]
    name = res["name"]
    if fmt == "csv":
        import csv, io
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(["pack", "id", "level", "category", "status", "title", "evidence", "source", "example"])
        for cid, it in items.items():
            ch = res["checks"].get(cid, {"status": "JUDGMENT", "evidence": ""})
            w.writerow([name, cid, it["level"], cats[it["category"]], ch["status"], it["title"], ch["evidence"], it.get("source", ""), it.get("example", "")])
        return buf.getvalue()
    if fmt == "junit":
        import xml.etree.ElementTree as ET
        suites = ET.Element("testsuites", name=f"pack-audit {name}")
        for lv in LEVEL_ORDER:
            lv_items = [(cid, it) for cid, it in items.items() if it["level"] == lv]
            suite = ET.SubElement(suites, "testsuite", name=f"{name} {checklist['levels'][lv]['name']}", tests=str(len(lv_items)))
            fails = skips = 0
            for cid, it in lv_items:
                ch = res["checks"].get(cid, {"status": "JUDGMENT", "evidence": ""})
                tc = ET.SubElement(suite, "testcase", classname=f"{name}.{cats[it['category']]}", name=f"{cid} {it['title']}")
                if ch["status"] in ("FAIL", "PARTIAL"):
                    fails += 1
                    fe = ET.SubElement(tc, "failure", message=f"{ch['status']}: {ch['evidence'][:200]}", type=ch["status"])
                    fe.text = ch["evidence"] + ("\n" + "\n".join(map(str, ch.get("detail") or [])) if ch.get("detail") else "") + (f"\nrule: {it.get('source', '')}" if it.get("source") else "")
                elif ch["status"] in ("MANUAL", "NA", "JUDGMENT"):
                    skips += 1
                    ET.SubElement(tc, "skipped", message=f"{ch['status']}: {ch['evidence'][:200]}")
                else:
                    ET.SubElement(tc, "system-out").text = ch["evidence"]
            suite.set("failures", str(fails)); suite.set("skipped", str(skips))
        ET.indent(suites)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")
    if fmt == "sarif":
        rules, results = [], []
        for cid, it in items.items():
            rules.append({"id": cid, "name": cid.replace("-", ""), "shortDescription": {"text": it["title"]},
                          "fullDescription": {"text": (it.get("expect") or it["title"]).strip()},
                          "helpUri": it.get("source", ""),
                          "properties": {"level": it["level"], "category": cats[it["category"]], "example": it.get("example", "")}})
            ch = res["checks"].get(cid)
            if not ch or ch["status"] not in SARIF_LEVEL:
                continue
            msg = f"[{ch['status']}] {it['title']}: {ch['evidence']}"
            r = {"ruleId": cid, "level": SARIF_LEVEL[ch["status"]], "message": {"text": msg},
                 "locations": [{"physicalLocation": {"artifactLocation": {"uri": (res["facts"].get("primary_chart") or ".") + "/Chart.yaml", "uriBaseId": "SRCROOT"}}}],
                 "properties": {"status": ch["status"], "maturityLevel": it["level"], "detail": [str(x) for x in (ch.get("detail") or [])][:12]}}
            results.append(r)
        sc = res["score"]
        return json.dumps({"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{
            "tool": {"driver": {"name": "pack-audit", "informationUri": "https://github.com/nebari-dev/software-pack-template/tree/main/tools/pack-audit",
                                "version": "0.1.0", "rules": rules}},
            "originalUriBaseIds": {"SRCROOT": {"uri": "file://" + res["source"].rstrip("/") + "/"}},
            "properties": {"pack": name, "overall_pct": sc["overall_pct"], "repo_level": sc["repo_level"], "runs_on_nebari": sc.get("runs_on_nebari")},
            "results": results}]}, indent=2)
    sys.exit(f"unknown format {fmt}")


# ----------------------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("repo")
    s.add_argument("--ref")
    s.add_argument("--name")
    s.add_argument("--values", action="append", help="values file(s) to render with (overrides auto-search)")
    s.add_argument("--set", action="append", help="extra --set for helm")
    s.add_argument("--out", default=str(HERE / "reports"))
    s.add_argument("--carry-from", help="previous results JSON: carry over judgment verdicts and 'override:' statuses")
    r = sub.add_parser("report")
    r.add_argument("results")
    r.add_argument("--out")
    m = sub.add_parser("summary", help="cross-pack SUMMARY.md from several results JSON files")
    m.add_argument("results", nargs="+")
    m.add_argument("--out")
    v = sub.add_parser("verify", help="install on a local kind cluster running the Nebari stack; records NA-01/NA-04 (needs docker, kind, kubectl, helm, make)")
    v.add_argument("results")
    v.add_argument("--chart", help="chart directory (required for --ref audits, otherwise taken from the results)")
    v.add_argument("--cluster", default="pack-audit-verify")
    v.add_argument("--template-dev", help="path to software-pack-template/dev (default: ../../dev relative to audit.py)")
    v.add_argument("--hostname", help="default <pack>.nebari.local")
    v.add_argument("--namespace")
    v.add_argument("--values", action="append")
    v.add_argument("--set", action="append")
    v.add_argument("--timeout", default="5m")
    v.add_argument("--keep", action="store_true", help="leave the release and namespace installed")
    v.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    e = sub.add_parser("export", help="export a results JSON as sarif, junit, or csv")
    e.add_argument("results")
    e.add_argument("--format", choices=["sarif", "junit", "csv"], required=True)
    e.add_argument("--out")
    args = ap.parse_args()
    checklist = yaml.safe_load(read(CHECKLIST))
    if args.cmd == "scan":
        res = scan(args)
        if args.carry_from:
            old = json.loads(read(Path(args.carry_from)))
            items = {i["id"]: i for i in checklist["items"]}
            carried = 0
            for cid, och in old.get("checks", {}).items():
                is_judgment = items.get(cid, {}).get("check") == "judgment"
                if (is_judgment and och["status"] != "JUDGMENT") or "override:" in och.get("evidence", ""):
                    res["checks"][cid] = {"status": och["status"], "evidence": och["evidence"] if och["evidence"].startswith("carried:") else "carried: " + och["evidence"], "detail": och.get("detail", [])}
                    carried += 1
            res["carried_from"] = args.carry_from
            res["score"] = score(res, checklist)
            print(f"carried {carried} agent verdict(s) from {args.carry_from}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{res['name']}.json").write_text(json.dumps(res, indent=2, default=str))
        (out / f"{res['name']}.md").write_text(render_report(res, checklist))
        sc = res["score"]
        print(f"{res['name']}: {sc['overall_pct']}%  repo-level={sc['repo_level']}  "
              f"pending-judgment={sum(len(v) for v in sc['pending'].values())}  -> {out / (res['name'] + '.md')}")
    elif args.cmd == "verify":
        verify(args, checklist)
    elif args.cmd == "export":
        res = json.loads(read(Path(args.results)))
        res["score"] = score(res, checklist)
        text = export(res, checklist, args.format)
        ext = {"sarif": ".sarif", "junit": ".junit.xml", "csv": ".csv"}[args.format]
        outp = Path(args.out) if args.out else Path(args.results).with_suffix(ext)
        outp.write_text(text)
        print(f"{res['name']}: {args.format} -> {outp}")
    elif args.cmd == "summary":
        results = []
        for f in args.results:
            res = json.loads(read(Path(f)))
            res["score"] = score(res, checklist)
            results.append(res)
        outp = Path(args.out) if args.out else Path(args.results[0]).parent / "SUMMARY.md"
        outp.write_text(render_summary(results, checklist))
        print(f"summary of {len(results)} pack(s) -> {outp}")
    else:
        res = json.loads(read(Path(args.results)))
        res["score"] = score(res, checklist)
        Path(args.results).write_text(json.dumps(res, indent=2, default=str))
        md = render_report(res, checklist)
        outp = Path(args.out) if args.out else Path(args.results).with_suffix(".md")
        if outp.exists() and "\n# Software-pack audit:" in outp.read_text():
            narrative = outp.read_text().split("\n# Software-pack audit:", 1)[0].rstrip()
            if narrative:
                md = narrative + "\n\n" + md
        outp.write_text(md)
        print(f"{res['name']}: {res['score']['overall_pct']}%  repo-level={res['score']['repo_level']}  -> {outp}")


if __name__ == "__main__":
    main()
