"""Portable, escaped HTML and typed Excel/CSV reports, including empty sessions."""
import base64
import csv
import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter
from matcher import parse_date
from models import valid_url, canonical_url

def link_rows(records):
    """One row per distinct discovered post URL, including cross-source provenance."""
    found, rows = set(), []
    for record in sorted(records, key=lambda r:r.get("found_at", "")):
        origins = [{"url":record.get("url", ""), "source":record.get("source", "")}]
        origins += record.get("provenance", [])
        for origin in origins:
            url = origin.get("url", "")
            identity = canonical_url(url)
            if not identity or identity in found: continue
            found.add(identity)
            rows.append({"Company":record.get("company") or "Not stated", "Title":record.get("title") or "Post needs text",
                "Source":origin.get("source",record.get("source", "")), "Post link":url,
                "Application link":record.get("application_url", ""),
                "Score":record.get("score") if record.get("status") == "analyzed" else None,
                "Status":record.get("status", ""), "Saved at (UTC)":record.get("found_at", "")})
    return rows

def export_link_sheet(records, output_dir, session_id):
    """Atomically replace a small, live workbook after every committed result."""
    output_dir = Path(output_dir)
    folder = output_dir / "sessions" / session_id
    folder.mkdir(parents=True, exist_ok=True)
    rows = link_rows(records)
    wb = Workbook(); ws = wb.active; ws.title = "Post links"
    headers = ["Company", "Title", "Source", "Post link", "Application link", "Score", "Status", "Saved at (UTC)"]
    ws.append(headers)
    for row in rows: ws.append([cell_value(row.get(k)) for k in headers])
    for c in ws[1]:
        c.fill = PatternFill("solid",fgColor="163C50"); c.font = Font(color="FFFFFF",bold=True)
    ws.freeze_panes = "D2"; ws.auto_filter.ref = ws.dimensions
    for i,width in enumerate([26,55,32,65,65,12,18,30],1): ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2):
        ws.row_dimensions[row[0].row].height = 42
        for c in row: c.alignment = Alignment(vertical="top",wrap_text=True)
        for c in (row[3],row[4]):
            if valid_url(c.value): c.hyperlink=c.value; c.font=Font(color="007F8B",underline="single")
    with tempfile.TemporaryDirectory(dir=folder, prefix=".links-") as tmp:
        temp = Path(tmp)/"post_links.xlsx"; wb.save(temp)
        os.replace(temp, folder/"post_links.xlsx")
        # A stable shortcut always points to a complete snapshot, never a partial ZIP.
        shutil.copyfile(folder/"post_links.xlsx", temp)
        os.replace(temp, output_dir/"post_links.xlsx")
    return folder/"post_links.xlsx"

COLUMNS = {
    "company":"Company", "title":"Job title", "location":"Location", "source":"Source platform",
    "url":"Original publication URL", "application_url":"Application URL", "published_at":"Publication date",
    "internship_type":"Internship type", "pfe_status":"PFE eligibility", "score":"Match score",
    "matching_skills":"Matching technical skills", "missing_skills":"Missing requirements",
    "explanation":"Score explanation", "ai_explanation":"AI explanation", "concerns":"Eligibility concerns",
    "recommended_action":"Recommended action", "band":"Priority", "status":"Status", "kind":"Opportunity kind",
    "analysis_method":"Analysis method", "breakdown":"Weighted score components", "provenance":"Source attribution",
}

def cell_value(value):
    if isinstance(value, list): value = "; ".join(json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v) for v in value)
    if isinstance(value, dict): value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)[:32000]
        # Prevent spreadsheet formula injection in both Excel and CSV.
        if value.lstrip().startswith(("=", "+", "-", "@")): value = "'" + value
    return value

def write_excel(records, path, metadata):
    wb = Workbook(); wb.remove(wb.active)
    groups = [("Opportunities", [r for r in records if r["status"] == "analyzed"]),
              ("Company research", [r for r in records if r["status"] == "prospect"]),
              ("Needs text and excluded", [r for r in records if r["status"] not in ("analyzed", "prospect")])]
    for name, rows in groups:
        ws = wb.create_sheet(name)
        ws.append(list(COLUMNS.values()))
        for record in rows:
            values = []
            for key in COLUMNS:
                val = record.get(key, "")
                if key == "published_at": val = parse_date(val) or val
                values.append(cell_value(val))
            ws.append(values)
        ws.freeze_panes = "D2"
        ws.auto_filter.ref = ws.dimensions
        ws.row_dimensions[1].height = 34
        for c in ws[1]:
            c.fill = PatternFill("solid", fgColor="163C50"); c.font = Font(color="FFFFFF", bold=True)
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for col, key in enumerate(COLUMNS, 1):
            ws.column_dimensions[get_column_letter(col)].width = 15 if key == "score" else 22 if key in ("company","location","published_at","status","kind") else 44
        for row in ws.iter_rows(min_row=2):
            ws.row_dimensions[row[0].row].height = 72
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
                if c.row % 2 == 0: c.fill = PatternFill("solid", fgColor="EEF5F6")
            row[6].number_format = "yyyy-mm-dd"
            for idx in (4,5):
                if valid_url(row[idx].value):
                    row[idx].hyperlink = row[idx].value; row[idx].font = Font(color="007F8B", underline="single")
        if rows:
            ws.conditional_formatting.add(f"J2:J{len(rows)+1}", ColorScaleRule(start_type="num", start_value=0, start_color="F9E0D9", end_type="num", end_value=100, end_color="A7DFCC"))
    ws = wb.create_sheet("Session notes")
    ws.append(["Session detail", "Value"])
    notes = {**metadata, "Scoring weights":"Skills 35, timing 20, location 15, experience 15, interests 10, recency 5",
             "CV source":"PDF is authoritative; search preferences are supplied by the user.",
             "Unknown information":"Unknown dates, skills and eligibility never receive full points.",
             "AI":"Optional explanatory layer; fixed score and source facts stay deterministic.",
             "LinkedIn":"Manual imports only. URL-only records require pasted post content.",
             "Sources":"Remotive attribution and original links retained. Public API listings may be delayed.",
             "Empty report":"No synthetic opportunities are added when a session has no results."}
    for k,v in notes.items(): ws.append([k, cell_value(v)])
    ws.column_dimensions["A"].width = 30; ws.column_dimensions["B"].width = 105
    ws.freeze_panes = "A2"
    for row in ws:
        for c in row: c.alignment = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]: c.fill=PatternFill("solid", fgColor="163C50"); c.font=Font(bold=True,color="FFFFFF")
    wb.save(path)

def write_html(records, path, excel_path, metadata):
    safe_records = [{k:r.get(k, "") for k in COLUMNS} for r in records if r["status"] in ("analyzed", "prospect")]
    data = json.dumps(safe_records, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    encoded_excel = base64.b64encode(Path(excel_path).read_bytes()).decode()
    template = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PFE Opportunity Finder · Session report</title>
<style>
:root{color-scheme:light;--ink:#183247;--muted:#536778;--accent:#007f87;--line:#dce5e9}
*{box-sizing:border-box}body{margin:0;background:#f4f7fa;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
header{background:#123247;color:white;padding:44px max(24px,calc((100vw - 1180px)/2)) 36px}
.eyebrow{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#8ee0d1}h1{font-size:clamp(30px,5vw,46px);line-height:1.15;margin:12px 0}header p{color:#c8dbe6;max-width:750px}
main{max-width:1228px;padding:28px 24px;margin:auto}.toolbar{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0;align-items:center}
input,select{padding:12px;border:1px solid var(--line);border-radius:9px;background:white;color:var(--ink);font:inherit}input{flex:1;min-width:210px}
.download{background:#a4edda;color:#123247;text-decoration:none;border-radius:9px;padding:11px 17px;font-weight:700;display:inline-block}
.stats{display:flex;gap:16px;flex-wrap:wrap}.stat{flex:1;min-width:160px;padding:20px 24px;background:white;border:1px solid var(--line);border-radius:12px}.stat strong{display:block;font-size:32px;line-height:1.2}.muted{color:var(--muted);font-size:14px}
article{padding:26px;margin:16px 0;background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 4px 20px #163c5005}.top{display:flex;gap:22px;justify-content:space-between}h2{font-size:22px;line-height:1.3;margin:0 0 8px}.score{font-size:30px;font-weight:750;color:var(--accent);white-space:nowrap}.tag{display:inline-block;border-radius:20px;background:#e7f5f1;color:#176354;padding:3px 10px;margin:4px 4px 0 0;font-size:13px}.concern{background:#fff6e7;padding:12px 16px;border-radius:8px;color:#77521b;font-size:14px}.links{display:flex;gap:18px;flex-wrap:wrap;margin-top:20px}a{color:#006c7b}details{margin-top:16px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;font:inherit;font-size:14px}.empty{text-align:center;padding:50px 20px;border:1px dashed #a5bac5;border-radius:14px}footer{margin:35px 0;color:var(--muted);font-size:13px}.small{font-size:13px}label{font-size:13px;display:flex;flex-direction:column;gap:3px}
@media(max-width:650px){.top{display:block}.score{font-size:24px}article{padding:20px}.toolbar>*{width:100%}}
</style></head><body><header><div class="eyebrow">Career research / February 2027</div><h1>Your next opportunity,<br>with the evidence to assess it.</h1><p>Six-month PFE · Cybersecurity, DevSecOps, cloud and software development · Casablanca, Rabat, Marrakech and eligible remote roles.</p><a class="download" download="opportunities.xlsx" href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,__XLSX__">Download Excel ↗</a></header>
<main><div class="stats"><div class="stat"><strong id="total">0</strong>Analyzed vacancies</div><div class="stat"><strong id="strong">0</strong>Score 75 and above</div><div class="stat"><strong id="prospects">0</strong>Company research leads</div></div>
<div class="toolbar"><label style="flex:1">Search<input id="search" type="search" placeholder="Company, role, skill…"></label><label>Minimum score<select id="score"><option value="60">60+ · Potential</option><option value="75">75+ · Very relevant</option><option value="90">90+ · Excellent</option><option value="0">All scores</option></select></label><label>Location<select id="location"><option value="">All locations</option></select></label><label>Category<select id="kind"><option value="vacancy">Vacancies</option><option value="prospect">Company research</option><option value="">Everything</option></select></label><label>Sort<select id="sort"><option value="score">Highest score</option><option value="date">Newest dated first</option><option value="company">Company A–Z</option></select></label></div>
<p id="count" class="muted" role="status"></p><div id="cards"></div>
<footer>__META__<br>Scores are decision support. Unknown eligibility needs confirmation. Company research leads are not confirmed internships. LinkedIn content is manually imported; Remotive listings retain source attribution. The Excel download includes low-priority, pending and excluded records.</footer></main>
<script type="application/json" id="records">__DATA__</script><script>
const records=JSON.parse(document.getElementById('records').textContent), $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const list=v=>Array.isArray(v)?v:[];
const link=(url,label)=>{try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:''}catch{return ''}};
$('total').textContent=records.filter(r=>r.status==='analyzed').length;$('strong').textContent=records.filter(r=>r.status==='analyzed'&&r.score>=75).length;$('prospects').textContent=records.filter(r=>r.kind==='prospect').length;
[...new Set(records.map(r=>r.location||'Unspecified'))].sort().forEach(l=>{const o=document.createElement('option');o.value=l;o.textContent=l;$('location').appendChild(o)});
function render(){let rows=records.filter(r=>(!$('kind').value||r.kind===$('kind').value)&&(r.kind==='prospect'||r.score>=Number($('score').value))&&(!$('location').value||(r.location||'Unspecified')===$('location').value)&&JSON.stringify(r).toLowerCase().includes($('search').value.toLowerCase()));rows.sort((a,b)=>$('sort').value==='company'?String(a.company).localeCompare(String(b.company)):$('sort').value==='date'?(Date.parse(b.published_at)||0)-(Date.parse(a.published_at)||0):b.score-a.score);
$('count').textContent=`${rows.length} results shown`;
$('cards').innerHTML=rows.length?rows.map(r=>`<article><div class="top"><div><div class="muted">${esc(r.company||'Company not stated')} · ${esc(r.location||'Location not stated')}</div><h2>${esc(r.title)}</h2><div class="muted">${esc(r.source)} · ${esc(r.published_at||'Publication date unknown')}</div></div><div class="score">${r.kind==='prospect'?'Research':esc(r.score)+'/100'}</div></div><p><span class="tag">${esc(r.pfe_status)}</span> <span class="tag">${esc(r.band)}</span></p><div>${list(r.matching_skills).map(s=>`<span class="tag">${esc(s)}</span>`).join('')}</div><p>${esc(r.recommended_action)}</p>${list(r.concerns).length?`<div class="concern">${list(r.concerns).map(esc).join('<br>')}</div>`:''}<details><summary>Why this score?</summary><p>${esc(r.explanation)}</p><p><b>Missing recognized requirements:</b> ${esc(list(r.missing_skills).join(', ')||'None identified; review the full description.')}</p>${r.ai_explanation?`<p><b>Optional AI interpretation:</b> ${esc(r.ai_explanation)}</p>`:''}<p class="small">${esc(r.analysis_method)}</p></details><div class="links">${link(r.url,'Original source')}${r.application_url&&r.application_url!==r.url?link(r.application_url,'Apply'):''}</div></article>`).join(''):'<div class="empty"><h2>No opportunities match these filters.</h2><p>Try all scores, or import more posts and run another session. No results have been fabricated.</p></div>'}
['search','score','location','sort','kind'].forEach(id=>$(id).addEventListener('input',render));render();
</script></body></html>'''
    meta = " · ".join(str(v) for v in (
        "Session " + str(metadata.get("result", "report")),
        f"Collection: {metadata['collection_seconds']} seconds" if "collection_seconds" in metadata else "",
        "CV: " + metadata["cv"] if metadata.get("cv") else "",
        f"{metadata['excluded']} excluded records in Excel/CSV" if "excluded" in metadata else "") if v)
    rendered = template.replace("__XLSX__", encoded_excel).replace("__META__", html.escape(meta)).replace("__DATA__", data)
    Path(path).write_text(rendered, encoding="utf-8")

def export_reports(records, output_dir, metadata=None):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    metadata = metadata or {"Generated":datetime.now(timezone.utc).isoformat()}
    session_id = metadata.get("session_id")
    folder = output_dir / "sessions" / session_id if session_id else output_dir
    folder.mkdir(parents=True, exist_ok=True)
    # Build a full set first so a failed export leaves the previous files intact.
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".export-") as tmp:
        tmp = Path(tmp)
        write_excel(records, tmp/"opportunities.xlsx", metadata)
        with (tmp/"opportunities.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f); writer.writerow(COLUMNS.values())
            for r in records: writer.writerow([cell_value(r.get(k,"")) for k in COLUMNS])
        write_html(records, tmp/"opportunities.html", tmp/"opportunities.xlsx", metadata)
        for suffix in ("xlsx", "csv", "html"):
            filename = f"opportunities.{suffix}"
            os.replace(tmp/filename, folder/filename)
            if folder != output_dir:
                shutil.copyfile(folder/filename, tmp/filename)
                os.replace(tmp/filename, output_dir/filename)
    return {suffix:str(folder/f"opportunities.{suffix}") for suffix in ("html","xlsx","csv")}
