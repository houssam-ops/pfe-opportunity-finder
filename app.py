"""Simple controls, real live results and an automatically saved link sheet."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from collector import parse_text, parse_import, load_sources
from config import ROOT, load_settings
from cv_parser import parse_cv
from database import Database
from exporter import export_reports, export_link_sheet, link_rows
from live_view import live_feed_html
from models import valid_url
from recover import recover, active_workers

st.set_page_config(page_title="PFE Finder",page_icon="🔎",layout="wide",initial_sidebar_state="collapsed")
st.markdown('''<style>
.block-container{max-width:1180px;padding-top:1.7rem}h1{letter-spacing:-1px;margin-bottom:0}
div[data-testid="stMetric"]{background:white;border:1px solid #dce7ec;border-radius:12px;padding:12px 18px}
button[kind="primary"]{min-height:52px;font-size:18px}div[data-testid="stTabs"] button{font-weight:600}
@media(max-width:850px){div[data-testid="stHorizontalBlock"]{flex-wrap:wrap}div[data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{min-width:min(170px,100%);flex:1 1 170px}}
</style>''',unsafe_allow_html=True)
try:
    settings=load_settings(); settings.prepare(); db=Database(settings.db_path)
except (ValueError,OSError) as e: st.error(str(e)); st.stop()
try: profile=parse_cv(settings.cv_file); cv_error=None
except Exception as e: profile=None; cv_error=str(e)

with st.sidebar:
    st.header("Settings")
    st.caption("The search works with these defaults. No API key is required.")
    minutes=st.number_input("Maximum session minutes",min_value=.01,max_value=120.0,value=float(settings.session_minutes),step=1.0)
    online=st.toggle("Search public job sources",value=True)
    ai_ready=bool(settings.ai_enabled and settings.ai_api_key and settings.ai_model)
    ai_consent=st.checkbox("Send CV and job text to my AI provider",value=False,disabled=not ai_ready)
    st.divider(); st.subheader("Your CV")
    if profile:
        st.success(profile["filename"])
        with st.expander("Check extracted skills"):
            st.write(", ".join(profile["skills"]))
            st.text_area("CV text",profile["text"],height=200,disabled=True)
    else: st.warning(cv_error)
    uploaded=st.file_uploader("Replace CV",type=["pdf"])
    if uploaded and st.button("Use this CV"):
        temp=settings.root/"data/cv/upload-check.pdf"
        try:
            temp.write_bytes(uploaded.getvalue()); parse_cv(temp)
            settings.cv_file.parent.mkdir(parents=True,exist_ok=True); os.replace(temp,settings.cv_file); st.rerun()
        except Exception as e: temp.unlink(missing_ok=True); st.error(str(e))

st.title("🔎 PFE Finder")
st.write("**Start the search → watch the results → download all post links.**")
if profile: st.caption(f"CV ready · {len(profile['skills'])} recognized skills · PFE February 2027 · {minutes:g}-minute limit")
else: st.warning("Upload your CV under Settings to enable search.")

def launch_search():
    session_id=None
    try:
        if online: load_sources(settings.root/"sources.json")
        session_id=db.create_session(minutes*60)
        command=[sys.executable,str(ROOT/"runner.py"),"--session-id",session_id,"--minutes",str(minutes)]
        if not online: command.append("--offline")
        if ai_consent: command.append("--enable-ai")
        with (settings.root/"data/session.log").open("a") as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        st.session_state["worker"]=process; st.session_state["worker_id"]=session_id
    except Exception as e:
        if session_id: db.update_session(session_id,"failed",{"error":str(e)})
        st.error(str(e))

@st.fragment(run_every="1s")
def search_controls():
    session=db.get_session()
    active=bool(session and session["status"] in ("running","paused","exporting"))
    paused=bool(active and session.get("pause"))
    start,pause,stop=st.columns([2,1,1])
    if start.button("▶ Start search",type="primary",use_container_width=True,disabled=not profile or active):
        launch_search(); st.rerun()
    if pause.button("▶ Resume" if paused else "⏸ Pause",use_container_width=True,disabled=not active or session["status"]=="exporting"):
        db.request_pause(session["id"],not paused); st.rerun()
    if stop.button("■ Stop & save",use_container_width=True,disabled=not active or session["status"]=="exporting"):
        db.cancel(session["id"]); st.rerun()
    if not session:
        st.caption("Ready. Click Start search to scan configured sources and your imported posts."); return
    if active:
        elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(session["started_at"])).total_seconds()
        remaining=max(0,session["deadline_seconds"]-elapsed)
        label="Saving reports…" if session["status"]=="exporting" else "Paused" if session["status"]=="paused" else "Pausing…" if paused else "Searching…"
        st.progress(max(0,min(elapsed/session["deadline_seconds"],1.0)),text=f"{label} · {int(remaining)//60}:{int(remaining)%60:02d} remaining")
        if paused: st.caption("Collection pauses at a safe checkpoint. The session deadline keeps running; saved links stay available below.")
        events=db.events(session["id"])
        if events: st.caption(events[-1]["message"])
        process=st.session_state.get("worker")
        if process and st.session_state.get("worker_id")==session["id"] and process.poll() is not None and db.get_session(session["id"])["status"] in ("running","paused","exporting"):
            try:
                rows=db.results(session["id"]); export_link_sheet(rows,settings.output_dir,session["id"])
                export_reports(rows,settings.output_dir,{"session_id":session["id"],"status":"Recovered after process exit"})
                db.update_session(session["id"],"failed",{"error":"Process exited; saved results recovered."})
            except Exception as e:
                db.update_session(session["id"],"export_failed",{"export_error":str(e)})
            st.rerun()
        if remaining == 0 and not active_workers(settings.root):
            st.warning("The search process has exited. Recover saved results to unlock a new search.")
            if st.button("Recover saved results"):
                try: recover(settings); st.rerun()
                except (RuntimeError,ValueError,OSError) as e: st.error(str(e))
    else:
        message={"completed":"Search finished — all found links are saved.","cancelled":"Search stopped — found links are saved.","timed_out":"Time limit reached — found links are saved."}.get(session["status"],"Session: "+session["status"])
        st.caption(message)
        if session["status"] in ("failed","export_failed"):
            st.error(session["summary"].get("export_error") or session["summary"].get("error") or "Search failed. Check Source activity for details.")
            if st.button("Retry saved-result export"):
                try: recover(settings); st.rerun()
                except Exception as e: st.error(str(e))

search_controls()
live_tab,import_tab,source_tab=st.tabs(["Live search & sheet","Add LinkedIn posts","Sources"])

with live_tab:
    st.caption("This view follows real discoveries and saved posts being reviewed. It is not a browser recording or automated LinkedIn scrolling.")
    follow=st.toggle("Follow new results automatically",value=True)

    @st.fragment(run_every="1s")
    def live_results():
        session=db.get_session(); rows=db.results(session["id"]) if session else []; links=link_rows(rows)
        one,two,three=st.columns(3)
        one.metric("Post links saved",len(links))
        two.metric("Potential PFE matches",sum(r.get("score",0)>=60 and r["status"]=="analyzed" for r in rows))
        three.metric("Posts reviewed",len(rows))
        if session:
            path=settings.output_dir/"sessions"/session["id"]/"post_links.xlsx"
            if path.exists():
                st.download_button("↓ Download all post links · Excel",path.read_bytes(),"post_links.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",type="primary",use_container_width=True)
            elif session["status"] not in ("running","paused","exporting"):
                if st.button("Create the link sheet for this session"):
                    export_link_sheet(rows,settings.output_dir,session["id"]); st.rerun()
        st.caption("Each link is saved to SQLite immediately. Excel refreshes after each processed post; duplicate URLs are merged. Low-priority and excluded posts remain labeled in the sheet.")
        components.html(live_feed_html(links,follow),height=350,scrolling=False)
        with st.expander(f"View the sheet ({len(links)} links)"):
            if links:
                st.dataframe(pd.DataFrame(links),hide_index=True,use_container_width=True,
                    column_config={"Post link":st.column_config.LinkColumn("Original post",display_text="Open post ↗"),
                                   "Application link":st.column_config.LinkColumn("Apply",display_text="Open application ↗")})
            else: st.write("No links saved in this session yet.")
        with st.expander("Match explanations & full reports"):
            query=st.text_input("Filter by company, role or skill",key="match_query")
            relevant_only=st.checkbox("Show only scores of 60 or more",value=True)
            visible=[r for r in rows if (not relevant_only or r.get("score",0)>=60) and query.casefold() in json.dumps(r,ensure_ascii=False).casefold()]
            if not visible: st.info("No matching posts. Clear the score filter to inspect all reviewed records.")
            for r in visible[:50]:
                with st.expander(f"{r['score']}/100 · {r['title'] or 'Post needs text'}"):
                    st.write(r["pfe_status"]+" · "+r["recommended_action"]); st.write(r["explanation"])
                    st.caption("Missing recognized requirements: "+(", ".join(r["missing_skills"]) or "None identified"))
                    for concern in r["concerns"]: st.write("• "+concern)
                    if r.get("ai_explanation"): st.write(r["ai_explanation"])
                    if valid_url(r["url"]): st.link_button("Open original post",r["url"])
            if session and session["status"] not in ("running","paused","exporting","export_failed"):
                report_dir=settings.output_dir/"sessions"/session["id"]
                if not (report_dir/"opportunities.xlsx").exists():
                    if st.button("Create full reports for this session"):
                        try:
                            export_reports(rows,settings.output_dir,{**session["summary"],"session_id":session["id"]}); st.rerun()
                        except OSError as e: st.error(str(e))
                for col,suffix,mime in zip(st.columns(3),["html","xlsx","csv"],["text/html","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","text/csv"]):
                    path=report_dir/f"opportunities.{suffix}"
                    if path.exists(): col.download_button("Full "+suffix.upper()+" report",path.read_bytes(),path.name,mime)
        if session:
            with st.expander("Source activity"):
                for event in db.events(session["id"]): st.text(event["message"])
    live_results()

with import_tab:
    st.subheader("Add posts from your LinkedIn feed")
    st.write("Open LinkedIn, copy a post’s text and link, and paste them below. Separate multiple posts with `---`.")
    st.link_button("Open LinkedIn in a new tab ↗","https://www.linkedin.com/feed/")
    with st.form("paste_form",clear_on_submit=True):
        text=st.text_area("Post text and links",height=160,placeholder="Paste the post text and its original link…")
        submitted=st.form_submit_button("Save posts",type="primary")
    if submitted:
        items=parse_text(text); added=sum(db.add_import(item)[1] for item in items)
        if items: st.success(f"{added} new posts saved; {len(items)-added} duplicates merged. Start a new search to analyze them.")
        else: st.warning("Paste at least one post or URL.")
    with st.expander("Import files or job-alert emails"):
        files=st.file_uploader("TXT, CSV, EML or saved HTML",type=["txt","csv","eml","html","htm"],accept_multiple_files=True)
        if st.button("Import files",disabled=not files):
            for file in files:
                try:
                    items=parse_import(file.getvalue(),file.name); added=sum(db.add_import(item)[1] for item in items)
                    st.success(f"{file.name}: {added} new records; {len(items)-added} merged.")
                except Exception as e: st.error(str(e))
        st.download_button("Blank CSV template","text,url,company,title,location,published_at,application_url,source\n","import-template.csv","text/csv")
    pending=[item for _,item in db.imports() if len(item.text.strip())<30]
    if pending:
        with st.expander(f"Add missing text ({len(pending)} links)"):
            choice=st.selectbox("Saved link",range(len(pending)),format_func=lambda i:pending[i].url or "No URL"); item=pending[choice]
            if valid_url(item.url): st.link_button("Open saved post",item.url)
            content=st.text_area("Full post text")
            if st.button("Update post",disabled=len(content.strip())<30): item.text=content.strip(); db.add_import(item); st.rerun()
    st.caption("The app does not fetch LinkedIn pages, sign in, scroll your feed or extract cookies. URL-only records need pasted text before they can be scored.")

with source_tab:
    st.subheader("Where the search looks")
    st.write("Public job sources and your saved imports. Company research leads are labeled separately from confirmed vacancies.")
    try:
        sources=load_sources(settings.root/"sources.json")
        for source in sources:
            if source.get("enabled"): st.write("✓ "+source["name"])
    except Exception as e: st.error(str(e))
    st.caption("Public API requests are cached for six hours. Repeated searches may reuse cached listings. Collection stops at the session deadline even while paused.")
    with st.expander("Advanced source configuration"):
        st.write("Configure permitted SmartRecruiters, Lever, RSS or JobPosting pages. Custom RSS/pages require documented permission in permission_url and permission_confirmed=true.")
        try: source_text=(settings.root/"sources.json").read_text()
        except (OSError,UnicodeError): source_text="[]"
        config_text=st.text_area("sources.json",source_text,height=350)
        current=db.get_session(); active=current and current["status"] in ("running","paused","exporting")
        if st.button("Save sources",disabled=bool(active)):
            temp=settings.root/"data/sources-check.json"
            try: temp.write_text(config_text); load_sources(temp); os.replace(temp,settings.root/"sources.json"); st.rerun()
            except Exception as e: temp.unlink(missing_ok=True); st.error(str(e))
    st.caption("Scoring: skills 35%, timing 20%, location 15%, experience 15%, interests 10%, recency 5%. Senior and non-internship jobs cannot score above 59. AI is optional; local matching works without a key.")
