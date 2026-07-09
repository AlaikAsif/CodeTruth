"""Standalone HTML report for a scan — a single self-contained file, no
external assets, so it opens anywhere and can be attached to a PR or CI run."""
from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Status

if TYPE_CHECKING:
    from .scanner import ScanResult

_STATUS_META = {
    Status.SAFE_TO_DELETE: ("safe to delete", "#1a7f37"),
    Status.LIKELY_DEAD: ("likely dead", "#9a6700"),
    Status.UNCERTAIN_DYNAMIC_RISK: ("uncertain / dynamic risk", "#bc4c00"),
    Status.DEFINITELY_USED: ("definitely used", "#57606a"),
}


def _esc(x) -> str:
    return html.escape(str(x))


def render_html(result: "ScanResult") -> str:
    s = result.summary()
    c = s["status_counts"]
    rows = []
    for r in result.candidates():
        label, color = _STATUS_META[r.status]
        ev_for = "".join(f"<li>{_esc(e)}</li>" for e in r.evidence_for_deletion)
        ev_against = "".join(f"<li>{_esc(e)}</li>"
                             for e in r.evidence_against_deletion)
        cluster = ""
        if r.cluster:
            cluster = ("<div class='cluster'>clump: "
                       + ", ".join(_esc(x) for x in r.cluster) + "</div>")
        rows.append(f"""
        <tr data-status="{r.status.value}">
          <td><span class="pill" style="background:{color}">{label}</span></td>
          <td class="rank">{r.rank_score:.2f}</td>
          <td><code>{_esc(r.symbol)}</code><div class="loc">{_esc(r.file)}:{r.line}</div>{cluster}</td>
          <td class="ev">
            <ul class="for">{ev_for}</ul>
            <ul class="against">{ev_against}</ul>
          </td>
        </tr>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeTruth report — {_esc(s['repo'])}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
        max-width: 1100px; margin-inline: auto; }}
 h1 {{ font-size: 1.25rem; margin: 0 0 .25rem; }}
 .sub {{ color: #6e7781; margin-bottom: 1rem; }}
 .counts {{ display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: 1rem; }}
 .count {{ padding: .35rem .7rem; border-radius: 6px; background: #f6f8fa;
          border: 1px solid #d0d7de; }}
 .count b {{ font-size: 1.1rem; }}
 @media (prefers-color-scheme: dark) {{
   .count {{ background: #161b22; border-color: #30363d; }} }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ text-align: left; padding: .5rem .6rem; vertical-align: top;
          border-top: 1px solid #d0d7de40; }}
 th {{ position: sticky; top: 0; background: Canvas; }}
 .pill {{ color: #fff; padding: .1rem .5rem; border-radius: 999px;
         font-size: .75rem; white-space: nowrap; }}
 .rank {{ font-variant-numeric: tabular-nums; text-align: right; }}
 .loc {{ color: #6e7781; font-size: .8rem; }}
 .cluster {{ color: #bc4c00; font-size: .78rem; margin-top: .2rem; }}
 code {{ font: 12px ui-monospace, monospace; }}
 .ev ul {{ margin: 0; padding-left: 1.1rem; }}
 .ev .for li {{ color: #1a7f37; }}
 .ev .against li {{ color: #a04100; }}
 .note {{ margin-top: 1.5rem; color: #6e7781; font-size: .85rem; }}
 input {{ margin-bottom: 1rem; padding: .4rem; width: 16rem; }}
</style></head><body>
<h1>CodeTruth report</h1>
<div class="sub">{_esc(s['repo'])} · {s['symbols']} symbols · {s['edges']} edges</div>
<div class="counts">
  <div class="count"><b>{c['safe_to_delete']}</b> safe to delete</div>
  <div class="count"><b>{c['likely_dead']}</b> likely dead</div>
  <div class="count"><b>{c['uncertain_dynamic_risk']}</b> uncertain</div>
  <div class="count"><b>{c['definitely_used']}</b> used</div>
</div>
<input id="filter" placeholder="filter symbols…" oninput="filt()">
<table id="t"><thead><tr><th>status</th><th>rank</th><th>symbol</th>
<th>evidence (for / against deletion)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="note">Advisory only — CodeTruth never deletes code. Act on
<b>safe to delete</b>; everything else needs human review.</p>
<script>
 function filt() {{
   const q = document.getElementById('filter').value.toLowerCase();
   for (const tr of document.querySelectorAll('#t tbody tr'))
     tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
 }}
</script>
</body></html>"""


def write_html_report(result: "ScanResult", path: str | Path) -> None:
    Path(path).write_text(render_html(result), encoding="utf-8")
