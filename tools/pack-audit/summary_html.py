#!/usr/bin/env python3
"""Render a self-contained HTML readiness page from reports/*.json and the
auditor narratives at the top of reports/*.md.

  python3 -I summary_html.py reports/a.json reports/b.json ... --out reports/readiness.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
LEVELS = ["E", "A", "B", "GA"]
LEVEL_NAME = {"E": "Experimental", "A": "Alpha", "B": "Beta", "GA": "GA"}


def md_inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def md_block(md: str) -> str:
    """Tiny markdown subset: ##/### headings, -/1. lists (one level of nesting), paragraphs."""
    out, stack = [], []  # stack of open list tags
    def close(to=0):
        while len(stack) > to:
            out.append(f"</{stack.pop()}>")
    for raw in md.splitlines():
        if not raw.strip():
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", raw)
        if m:
            close()
            lvl = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", raw)
        if m:
            depth = 1 + (len(m.group(1)) // 2 if m.group(1) else 0)
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            if depth > len(stack):
                out.append(f"<{tag}>"); stack.append(tag)
            elif depth < len(stack):
                close(depth)
            out.append(f"<li>{md_inline(m.group(3))}</li>")
            continue
        close()
        out.append(f"<p>{md_inline(raw.strip())}</p>")
    close()
    return "\n".join(out)


def narrative(md_path: Path) -> dict[str, str]:
    """Split the agent-written narrative (above the generated '# Software-pack audit:' line) into sections."""
    text = md_path.read_text() if md_path.exists() else ""
    head = text.split("\n# Software-pack audit:", 1)[0]
    sections: dict[str, str] = {}
    cur, buf = "intro", []
    for ln in head.splitlines():
        m = re.match(r"^##\s+(.*)", ln)
        if m:
            sections[cur] = "\n".join(buf).strip(); cur, buf = m.group(1).strip().lower(), []
        elif ln.startswith("# "):
            continue
        else:
            buf.append(ln)
    sections[cur] = "\n".join(buf).strip()
    return sections


def pick(sections: dict[str, str], *keys: str) -> str:
    for k, v in sections.items():
        if any(key in k for key in keys):
            return v
    return ""


def badge(status: str) -> str:
    return f'<span class="st st-{status.lower()}">{status}</span>'


def build(results: list[dict], checklist: dict, narratives: dict[str, dict[str, str]], title: str, lede: str) -> str:
    items = {i["id"]: i for i in checklist["items"]}
    cats = checklist["categories"]
    results = sorted(results, key=lambda r: -(r["score"]["overall_pct"] or 0))
    baseline = [r for r in results if r["name"].startswith("baseline")]
    packs = [r for r in results if not r["name"].startswith("baseline")]

    def level_cell(r):
        lv = r["score"]["repo_level"]
        return LEVEL_NAME[lv] if lv else "below Experimental"

    rows = []
    for r in packs + baseline:
        sc = r["score"]
        b = {lv: len(sc["blockers"][lv]) for lv in LEVELS}
        rows.append(
            f"<tr class='{'baseline' if r in baseline else ''}'><th scope='row'><a href='#pack-{r['name']}'>{html.escape(r['name'])}</a>"
            + ("<span class='tag'>baseline, first-party</span>" if r in baseline else "") + "</th>"
            f"<td class='num'><div class='meter'><i style='width:{sc['overall_pct']}%'></i></div><b>{sc['overall_pct']}%</b></td>"
            f"<td>{level_cell(r)}</td><td>{html.escape(str(r['facts'].get('detected_integration')))}</td>"
            + "".join(f"<td class='num'>{b[lv]}</td>" for lv in LEVELS) + "</tr>")

    cat_head = "".join(f"<th>{html.escape(r['name'])}</th>" for r in packs + baseline)
    cat_rows = []
    for cid, cname in cats.items():
        cells = []
        any_ = False
        for r in packs + baseline:
            c = r["score"]["per_category"].get(cid)
            if c and c.get("pct") is not None:
                any_ = True
                cells.append(f"<td class='num'><span class='pct p{int(c['pct'] // 25)}'>{c['pct']:.0f}%</span></td>")
            else:
                cells.append("<td class='num muted'>n/a</td>")
        if any_:
            cat_rows.append(f"<tr><th scope='row'>{cname}</th>{''.join(cells)}</tr>")

    shared = [cid for cid in items if packs and all(r["checks"].get(cid, {}).get("status") in ("FAIL", "PARTIAL") for r in packs)]
    shared_html = "".join(f"<li><code>{cid}</code> <span class='lvl'>{LEVEL_NAME[items[cid]['level']]}</span> {html.escape(items[cid]['title'])}"
                          + (f" <a href='{html.escape(items[cid]['source'])}'>rule</a>" if items[cid].get("source") else "") + "</li>" for cid in shared)

    pack_sections = []
    for r in packs + baseline:
        sc, f = r["score"], r["facts"]
        n = narratives.get(r["name"], {})
        verdict = pick(n, "verdict")
        fixes = pick(n, "top fixes", "what still", "blocks")
        risks = pick(n, "risk")
        verify = pick(n, "cluster verification", "needs")
        fp = pick(n, "false positive", "scanner")
        gate_rows = []
        for lv in LEVELS:
            p = sc["per_level"][lv]
            blk = sc["blockers"][lv]
            gate_rows.append(f"<tr><th scope='row'>{LEVEL_NAME[lv]}</th><td class='num'>{p['pass']}</td><td class='num'>{p['partial']}</td>"
                             f"<td class='num'>{p['fail']}</td><td class='num'>{p['manual']}</td><td>{'' if not blk else ', '.join(blk)}</td></tr>")
        check_rows = []
        for cid, it in items.items():
            if it.get("enabled", True) is False:
                continue
            ch = r["checks"].get(cid, {"status": "JUDGMENT", "evidence": ""})
            src = (f"<a href='{html.escape(it['source'])}'>rule</a>" if it.get("source") else "") + \
                  (f" · <a href='{html.escape(it['example'])}'>example</a>" if it.get("example") else "")
            check_rows.append(f"<tr><td><code>{cid}</code></td><td>{it['level']}</td><td>{badge(ch['status'])}</td>"
                              f"<td>{html.escape(it['title'])}</td><td class='ev'>{html.escape(ch['evidence'])}</td><td class='src-cell'>{src}</td></tr>")
        src = f"{html.escape(r['source'])}" + (f" @ <code>{html.escape(r['ref'])}</code>" if r.get("ref") else "")
        pack_sections.append(f"""
<section class="pack" id="pack-{html.escape(r['name'])}">
  <header class="pack-head">
    <div>
      <p class="eyebrow">{'Baseline, first-party pack' if r in baseline else 'Audited pack'}</p>
      <h2>{html.escape(r['name'])}</h2>
      <p class="src">{src}<br>chart <code>{html.escape(str(f.get('primary_chart')))}</code> {html.escape(str((f.get('chart_yaml') or {}).get('version')))}
      · NebariApp integration <b>{html.escape(str(f.get('detected_integration')))}</b> · scanned {html.escape(f['scanned_at'][:10])}</p>
    </div>
    <div class="score"><span class="big">{sc['overall_pct']}%</span><span class="lvl-big">{level_cell(r)}</span></div>
  </header>
  <div class="cols">
    <div class="col">
      <h3>Verdict</h3>{md_block(verdict) or '<p class="muted">No narrative yet.</p>'}
      <h3>Gates</h3>
      <div class="tbl"><table><thead><tr><th>Level</th><th>Pass</th><th>Partial</th><th>Fail</th><th>Manual</th><th>Blocking items</th></tr></thead><tbody>{''.join(gate_rows)}</tbody></table></div>
    </div>
    <div class="col">
      <h3>Top fixes</h3>{md_block(fixes) or '<p class="muted">See the checklist below.</p>'}
    </div>
  </div>
  <details><summary>Risks outside the checklist</summary>{md_block(risks) or '<p class="muted">none recorded</p>'}</details>
  <details><summary>Needs cluster verification</summary>{md_block(verify) or '<p class="muted">none recorded</p>'}</details>
  {f'<details><summary>Scanner corrections</summary>{md_block(fp)}</details>' if fp else ''}
  <details><summary>Full checklist ({len(items)} items)</summary>
    <div class="tbl"><table class="checks"><thead><tr><th>ID</th><th>Lvl</th><th>Status</th><th>Item</th><th>Evidence</th><th>Source</th></tr></thead><tbody>{''.join(check_rows)}</tbody></table></div>
  </details>
</section>""")

    return f"""<title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: one column, summary first, then one section per pack; tables scroll inside their own box. */
:root {{
  --bg:#f6f7f9; --panel:#ffffff; --fg:#17202a; --muted:#5b6673; --line:#d9dee5;
  --accent:#0b7a75; --accent-ink:#ffffff;
  --ok:#1f7a3a; --ok-bg:#e3f3e8; --warn:#9a6300; --warn-bg:#fff3d6; --bad:#b3261e; --bad-bg:#fde7e5;
  --neutral:#4a5563; --neutral-bg:#e9edf2;
  --font-body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --font-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#111518; --panel:#1a2024; --fg:#e7ebef; --muted:#9aa5b1; --line:#2b343c;
  --accent:#4cc3bb; --accent-ink:#0d1a19;
  --ok:#7ad39a; --ok-bg:#15301f; --warn:#f0c15b; --warn-bg:#3a2d0c; --bad:#f08a82; --bad-bg:#3d1613;
  --neutral:#b4bec8; --neutral-bg:#262f37; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg:#111518; --panel:#1a2024; --fg:#e7ebef; --muted:#9aa5b1; --line:#2b343c;
  --accent:#4cc3bb; --accent-ink:#0d1a19;
  --ok:#7ad39a; --ok-bg:#15301f; --warn:#f0c15b; --warn-bg:#3a2d0c; --bad:#f08a82; --bad-bg:#3d1613;
  --neutral:#b4bec8; --neutral-bg:#262f37; color-scheme: dark }}
body {{ background:var(--bg); color:var(--fg); font-family:var(--font-body); font-size:15px; line-height:1.5; }}
.wrap {{ max-width:1100px; margin:0 auto; padding-block:28px 64px; padding-inline:16px; }}
h1 {{ font-size:clamp(26px,4vw,34px); font-weight:600; letter-spacing:-0.01em; margin:0 0 4px; text-wrap:balance; }}
h2 {{ font-size:22px; font-weight:600; margin:0; }}
h3 {{ font-size:13px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:18px 0 6px; }}
h4 {{ font-size:14px; font-weight:600; margin:12px 0 4px; }}
p {{ margin:6px 0; max-width:72ch; }}
.lede {{ color:var(--muted); max-width:80ch; }}
.eyebrow {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--accent); margin:0; font-weight:600; }}
code {{ font-family:var(--font-mono); font-size:.88em; background:var(--neutral-bg); padding:1px 5px; border-radius:4px; }}
.tbl {{ overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--panel); }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
thead th {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); background:var(--panel); position:sticky; top:0; }}
tbody tr:last-child td, tbody tr:last-child th {{ border-bottom:0; }}
th[scope=row] {{ font-weight:500; white-space:nowrap; }}
td.num, th.num {{ font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }}
.meter {{ display:inline-block; width:90px; height:8px; background:var(--neutral-bg); border-radius:4px; vertical-align:middle; margin-right:8px; overflow:hidden; }}
.meter i {{ display:block; height:100%; background:var(--accent); }}
.tag {{ display:inline-block; margin-left:8px; font-size:11px; font-weight:500; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:0 8px; white-space:nowrap; }}
tr.baseline th, tr.baseline td {{ background:color-mix(in srgb, var(--accent) 7%, var(--panel)); }}
.pct {{ font-variant-numeric:tabular-nums; padding:1px 7px; border-radius:4px; font-weight:500; }}
.p0 {{ background:var(--bad-bg); color:var(--bad); }} .p1 {{ background:var(--warn-bg); color:var(--warn); }}
.p2 {{ background:var(--neutral-bg); color:var(--fg); }} .p3, .p4 {{ background:var(--ok-bg); color:var(--ok); }}
.muted {{ color:var(--muted); }}
.lvl {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:0 6px; }}
.pack {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:20px; margin-top:24px; }}
.pack-head {{ display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; align-items:flex-start; border-bottom:1px solid var(--line); padding-bottom:14px; }}
.src {{ color:var(--muted); font-size:13px; max-width:none; word-break:break-word; }}
.score {{ text-align:right; min-width:150px; }}
.big {{ display:block; font-size:40px; font-weight:600; line-height:1; font-variant-numeric:tabular-nums; color:var(--accent); }}
.lvl-big {{ display:block; font-size:13px; color:var(--muted); margin-top:4px; }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
.col {{ min-width:0; }}
@media (max-width:760px) {{ .cols {{ grid-template-columns:1fr; }} }}
.col ol, .col ul, details ol, details ul {{ padding-left:22px; margin:4px 0; }}
.col li, details li {{ margin:4px 0; max-width:72ch; }}
details {{ border-top:1px solid var(--line); margin-top:14px; padding-top:10px; }}
summary {{ cursor:pointer; font-weight:500; }}
summary:focus-visible, a:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.st {{ display:inline-block; font-size:11px; font-weight:600; letter-spacing:.04em; padding:1px 7px; border-radius:4px; }}
.st-pass {{ background:var(--ok-bg); color:var(--ok); }} .st-partial {{ background:var(--warn-bg); color:var(--warn); }}
.st-fail {{ background:var(--bad-bg); color:var(--bad); }} .st-manual, .st-na, .st-judgment {{ background:var(--neutral-bg); color:var(--neutral); }}
.checks td.ev {{ font-size:13px; color:var(--muted); min-width:260px; }}
.checks td.src-cell {{ white-space:nowrap; font-size:13px; }}
.legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:13px; color:var(--muted); margin:10px 0 0; }}
a {{ color:var(--accent); }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>
<div class="wrap">
  <p class="eyebrow">Nebari software-pack audit</p>
  <h1>{html.escape(title)}</h1>
  <p class="lede">{html.escape(lede)}
  Items are weighted by the level at which they block promotion (E=4, A=3, B=2, GA=1). The repo-readiness level is the highest
  level with no failing or partial item at or below it. Items that need a cluster or a person are listed, not scored.</p>

  <h3>Overview</h3>
  <div class="tbl"><table>
    <thead><tr><th>Pack</th><th class="num">Score</th><th>Repo-readiness level</th><th>NebariApp</th>
    <th class="num">E blockers</th><th class="num">A blockers</th><th class="num">B blockers</th><th class="num">GA blockers</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
  <p class="legend"><span>NebariApp: full = routing + gateway auth render · partial = NebariApp without auth · none = no NebariApp</span></p>

  <h3>Category scores</h3>
  <div class="tbl"><table><thead><tr><th>Category</th>{cat_head}</tr></thead><tbody>{''.join(cat_rows)}</tbody></table></div>

  <h3>Gaps shared by every audited pack</h3>
  <ul>{shared_html or '<li>none</li>'}</ul>

  {''.join(pack_sections)}

  <p class="muted" style="margin-top:28px;font-size:13px">Generated by <code>tools/pack-audit</code> (audit.py + the software-pack-auditor agent). Audit mode is read-only on the pack repositories.</p>
</div>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Pack Readiness")
    ap.add_argument("--lede", default="Software packs scored against the nebari-dev software-pack-template release-readiness checklist (Experimental, Alpha, Beta, GA).")
    args = ap.parse_args()
    checklist = yaml.safe_load((HERE / "checklist.yaml").read_text())
    results, narratives = [], {}
    for f in args.results:
        p = Path(f)
        r = json.loads(p.read_text())
        results.append(r)
        narratives[r["name"]] = narrative(p.with_suffix(".md"))
    Path(args.out).write_text(build(results, checklist, narratives, args.title, args.lede))
    print(f"wrote {args.out} ({Path(args.out).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
