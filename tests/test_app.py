"""Synthetic records exist only in temporary test directories, never the real inbox."""
import csv
import io
import json
import os
import signal
import time
from dataclasses import replace
from datetime import date
from email.message import EmailMessage
from pathlib import Path
import pymupdf
import pytest
from openpyxl import load_workbook
from bs4 import BeautifulSoup
from collector import parse_text, parse_import, parse_email, clean_text, PublicClient, assert_public_url, collect_source
from config import Settings, load_settings, WEIGHTS
from cv_parser import parse_cv, extract_skills
from database import Database
from exporter import export_reports
from matcher import analyze, semantic_explanation, recruitment_status
from models import Opportunity, valid_url, canonical_url
from runner import run_session, bounded_messages

@pytest.fixture
def profile(tmp_path):
    path = tmp_path/"synthetic-cv.pdf"
    pdf = pymupdf.open(); p = pdf.new_page()
    p.insert_text((50,50), "Synthetic testing CV\nCybersecurity engineering student\nPython, Docker, Kubernetes, GitLab, Azure, Wazuh, Laravel\nPROFESSIONAL EXPERIENCE\nDevSecOps internship: Kubernetes and GitLab.\nSOC internship: Wazuh and incident response.\nSoftware developer: Laravel.\nEDUCATION\nFinal year engineering student", fontsize=10)
    pdf.save(path); pdf.close()
    return parse_cv(path)

@pytest.fixture
def post():
    return Opportunity(text="Nous recrutons un stagiaire PFE DevSecOps et cybersécurité. Python, Docker, Kubernetes, GitLab. Stage de 6 mois dès février 2027. Envoyez votre CV.",
        url="https://www.linkedin.com/posts/example-activity-1234567890123456789", company="Synthetic Test Only", title="Stage PFE DevSecOps", location="Casablanca", published_at="2026-11-01")

def test_cv_pdf_parsing(profile):
    assert "Python" in profile["skills"] and "Docker" in profile["skills"]
    assert "AWS" not in profile["skills"]
    assert "DevSecOps" in profile["experience_domains"]
    assert len(profile["sha256"]) == 64

def test_real_cv_is_authority():
    path = Path(__file__).resolve().parents[1]/"data/cv/CV_Houssam_Zouheir.pdf"
    if not path.exists(): pytest.skip("Personal CV not bundled in portable release")
    p = parse_cv(path)
    assert "Checkov" in p["skills"] and "BloodHound" in p["skills"]
    assert "Koog" in p["experience_text"]
    assert "Honeynet" not in p["experience_text"]
    assert "Docker" not in p["skills"]  # User prompt mentions it, but this actual PDF does not.

def test_cv_missing_invalid_scanned(tmp_path):
    with pytest.raises(ValueError): parse_cv(tmp_path/"missing.pdf")
    invalid = tmp_path/"bad.pdf"; invalid.write_bytes(b"not a pdf")
    with pytest.raises((ValueError, pymupdf.FileDataError)): parse_cv(invalid)
    p = pymupdf.open(); p.new_page(); p.save(tmp_path/"blank.pdf")
    with pytest.raises(ValueError, match="text layer"): parse_cv(tmp_path/"blank.pdf")

def test_skill_boundaries():
    assert "Java" not in extract_skills("JavaScript internship")
    assert "Git" not in extract_skills("GitLab internship")

def test_post_extraction_and_bulk(post):
    rows = parse_text(post.url+"\n"+post.text+"\n---\nhttps://www.linkedin.com/posts/second")
    assert len(rows) == 2 and "PFE" in rows[0].text
    assert rows[0].source == "LinkedIn (manual import)"
    assert rows[1].text == "" and recruitment_status(rows[1])[0] == "needs_text"
    assert len(parse_text("https://example.com/a\nhttps://example.com/b")) == 2

def test_csv_unicode_semicolon(post):
    data = 'text;url;company\n"Stage PFE cybersécurité Python";https://example.com/job;Test\n'
    rows = parse_import(data.encode(),"posts.csv")
    assert rows[0].company == "Test" and "cybersécurité" in rows[0].text
    with pytest.raises(ValueError): parse_import(b"a,b\n1,2\n", "bad.csv")

def test_email_digest_card_separation():
    msg = EmailMessage(); msg["Subject"] = "Two jobs"; msg["From"] = "alerts@example.com"
    msg["Date"] = "Wed, 04 Nov 2026 12:00:00 +0000"
    msg.set_content('<html><article>Python internship Casablanca <a href="https://example.com/a">Python role</a></article><article>Cloud internship Rabat <a href="https://example.com/b">Cloud role</a></article><a href="https://example.com/unsubscribe">Unsubscribe</a></html>',subtype="html")
    rows = parse_email(msg.as_bytes())
    assert len(rows) == 2
    assert "Cloud" not in rows[0].text and "Python" not in rows[1].text
    assert rows[0].published_at == "" and rows[0].metadata["email_received"]

def test_clean_text_strips_scripts():
    assert clean_text("<p>Hiring <b>Python</b></p><script>evil()</script>") == "Hiring\nPython"

@pytest.mark.parametrize("value", ["", "not-a-url", "javascript:alert(1)", "https://user:pass@example.com/a", "https://example.com/<x>", "http://localhost/", "https://example.com:bad/"])
def test_invalid_urls(value): assert not valid_url(value)

def test_linkedin_url_identity():
    a="https://www.linkedin.com/posts/company-title-activity-1234567890123456789?utm_source=share&trk=foo"
    b="https://www.linkedin.com/feed/update/urn:li:activity:1234567890123456789/"
    assert canonical_url(a) == canonical_url(b)
    assert canonical_url("https://example.com/job?id=1&utm_campaign=foo") == "https://example.com/job?id=1"

def test_duplicates_and_enrichment(tmp_path,post):
    db=Database(tmp_path/"test.db")
    identifier, added=db.add_import(Opportunity(url=post.url))
    assert added
    identifier2, added=db.add_import(post)
    assert identifier2 == identifier and not added
    assert len(db.imports())==1 and db.imports()[0][1].text == post.text
    duplicate=replace(post,url="https://example.com/other-source",source="Alert")
    assert db.add_import(duplicate)[1] is False
    assert len(db.imports()[0][1].provenance)==2
    assert db.add_import(Opportunity(url=duplicate.url))[1] is False
    assert db.imports()[0][1].url == post.url

def test_identical_description_different_employers(tmp_path,post):
    db=Database(tmp_path/"test.db")
    db.add_import(post)
    assert db.add_import(replace(post,url="https://other.example/job",company="Different employer"))[1] is True

def test_non_technical_title_is_excluded():
    item=Opportunity(title="Remote Office Assistant",text="We are hiring an office assistant for our software and cybersecurity company. Use cloud software every day.",trusted_listing=True)
    assert recruitment_status(item)[0] == "irrelevant"

def test_ranking_and_weights(profile,post):
    result=analyze(post,profile,date(2026,11,4))
    assert result["score"] == 100
    assert sum(WEIGHTS.values()) == 100
    assert sum(result["breakdown"].values()) == result["score"]
    assert result["pfe_status"]=="Explicit PFE"
    assert result["matching_skills"]==["Docker","GitLab","Kubernetes","Python"]

def test_missing_skills_reduce_score(profile,post):
    result=analyze(replace(post,text=post.text+" AWS Terraform required."),profile,date(2026,11,4))
    assert result["score"]<100 and result["missing_skills"] == ["AWS","Terraform"]

def test_senior_caps(profile,post):
    r=analyze(replace(post,title="Senior DevSecOps engineer",text=post.text+" 5 years of experience"),profile,date(2026,11,4))
    assert r["score"]<=59 and r["score_cap"]==59

def test_french_experience_requirement_caps(profile,post):
    r=analyze(replace(post,text=post.text+" 5 ans d'expérience requis."),profile,date(2026,11,4))
    assert r["score"]<=59

def test_unknowns_are_not_confirmations(profile):
    r=analyze(Opportunity(text="Nous recrutons un stagiaire cybersécurité. Envoyez votre CV pour rejoindre notre équipe.",title="Stage cybersécurité"),profile)
    assert r["breakdown"]["skills"]==0 and r["breakdown"]["recency"]==0
    assert r["breakdown"]["timing"]==6 and len(r["concerns"])>=4

def test_remote_restriction_and_wrong_duration(profile,post):
    r=analyze(replace(post,location="Remote US only",text=post.text.replace("6 mois","3 mois")+" Must reside in US only."),profile,date(2026,11,4))
    assert r["score"]<=74 and r["breakdown"]["location"]==0
    assert r["breakdown"]["timing"]==10

@pytest.mark.parametrize("text", ["Career advice: how to find an internship in cybersecurity. We are hiring is a common phrase.", "Je recherche un stage PFE en cybersécurité. Python et Kubernetes, disponible six mois.","Les tendances du recrutement DevOps pour 2027 : conseils aux étudiants."])
def test_reject_advice_and_jobseekers(text): assert recruitment_status(Opportunity(text=text))[0] == "irrelevant"

def test_english_inferred_internship(profile):
    r=analyze(Opportunity(text="We are hiring software engineering interns. Python and Laravel, six months from February 2027. Send your CV.",location="Rabat"),profile)
    assert r["status"] == "analyzed" and r["pfe_status"].startswith("Inferred")

def test_spontaneous_distinct(profile):
    r=analyze(Opportunity(kind="prospect",text="Company careers",url="https://example.com/careers"),profile)
    assert r["score"]==0 and r["status"]=="prospect" and "no confirmed vacancy" in r["pfe_status"]

def test_exports_typed_escaped_and_formula_safe(tmp_path,profile,post):
    r=analyze(post,profile,date(2026,11,4)); r["company"]='=HYPERLINK("https://evil.example")'; r["title"]='</script><script>alert(1)</script>'
    paths=export_reports([r],tmp_path)
    wb=load_workbook(paths["xlsx"])
    ws=wb["Opportunities"]
    assert ws["J2"].value==100 and ws["J2"].data_type=="n"
    assert ws["A2"].data_type != "f" and ws["A2"].value.startswith("'")
    assert ws["E2"].hyperlink.target == post.url
    assert ws["G2"].is_date
    assert ws.freeze_panes=="D2" and ws.auto_filter.ref
    page=Path(paths["html"]).read_text()
    assert r["title"] not in page and "base64," in page
    payload=BeautifulSoup(page,"html.parser").find("script",id="records").string
    assert json.loads(payload)[0]["title"]==r["title"]
    assert Path(paths["csv"]).read_text(encoding="utf-8-sig").splitlines()[1].startswith("\"'=HYPERLINK")

def test_empty_reports(tmp_path):
    paths=export_reports([],tmp_path)
    assert load_workbook(paths["xlsx"])["Opportunities"].max_row==1
    assert len(Path(paths["csv"]).read_text().splitlines())==1
    assert 'id="records">[]</script>' in Path(paths["html"]).read_text()

def test_missing_ai_credentials(profile,post,tmp_path):
    config=Settings(root=tmp_path,ai_enabled=True)
    assert semantic_explanation(analyze(post,profile),profile,config)["status"]=="missing_credentials"

def test_ai_invalid_evidence_falls_back(profile,post,tmp_path,monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices":[{"message":{"content":json.dumps({"explanation":"Fake skill", "evidence":[{"claim":"AWS expert","cv_quote":"AWS expert","post_quote":"AWS"}]})}}]}
    monkeypatch.setattr("requests.Session.post",lambda *a,**k: Response())
    config=Settings(root=tmp_path,ai_enabled=True,ai_api_key="test",ai_model="test")
    with pytest.raises(ValueError,match="verified"): semantic_explanation(analyze(post,profile),profile,config)

def test_network_unavailable_cache_cooldown(tmp_path,monkeypatch):
    config=Settings(root=tmp_path)
    monkeypatch.setattr("collector.assert_public_url",lambda u:None)
    def unavailable(*args,**kwargs): raise ConnectionError("Synthetic network outage")
    monkeypatch.setattr("requests.Session.get",unavailable)
    client=PublicClient(config)
    with pytest.raises(ConnectionError): client.get("https://example.com/jobs")
    with pytest.raises(RuntimeError,match="cooldown"): client.get("https://example.com/jobs")

def test_no_linkedin_fetch_or_unapproved_page(tmp_path):
    with pytest.raises(ValueError,match="LinkedIn"): assert_public_url("https://www.linkedin.com/feed/")
    with pytest.raises(ValueError,match="permission"): list(collect_source({"type":"jsonld","name":"Test","url":"https://example.com"},Settings(root=tmp_path)))

def slow_source(pipe):
    pipe.send(("item", "first result")); time.sleep(30)

def test_source_hard_timeout_preserves_partial():
    start=time.monotonic()
    messages=list(bounded_messages(slow_source,(),2.0))
    assert ("item","first result") in messages
    assert any("timeout" in str(v) for k,v in messages if k=="error")
    assert time.monotonic()-start<4

def stalled_worker(db_path,session_id,settings,profile,sources,deadline):
    os.setsid()
    db=Database(db_path)
    item=Opportunity(text="We are hiring Python interns. Six months from February 2027.",url="https://example.com/test-only",title="Software internship")
    db.save_result(session_id,item.key,analyze(item,profile)); time.sleep(30)

def test_session_timeout_exports_partial(tmp_path,profile):
    config=Settings(root=tmp_path,session_minutes=.05)
    start=time.monotonic()
    result=run_session(config,profile,[],worker_target=stalled_worker)
    assert result["status"]=="timed_out" and result["summary"]["analyzed"]==1
    assert (config.output_dir/"opportunities.xlsx").exists()
    assert time.monotonic()-start<6

def test_default_ten_minute_budget(monkeypatch):
    monkeypatch.delenv("SESSION_DURATION_MINUTES",raising=False)
    assert Settings().session_minutes*60 == 600

def test_offline_session_end_to_end(tmp_path,profile,post):
    config=Settings(root=tmp_path,session_minutes=.15); config.prepare()
    db=Database(config.db_path); db.add_import(post)
    result=run_session(config,profile,[])
    assert result["status"]=="completed" and result["summary"]["analyzed"]==1
    assert result["summary"]["collection_seconds"]<9

def test_cancelled_session_and_single_session_lock(tmp_path,profile):
    config=Settings(root=tmp_path); config.prepare(); db=Database(config.db_path)
    session=db.create_session(600)
    with pytest.raises(ValueError,match="already running"): db.create_session(600)
    db.cancel(session)
    r=run_session(config,profile,[],session,worker_target=stalled_worker)
    assert r["status"]=="cancelled" and (config.output_dir/"opportunities.html").exists()

def test_streamlit_startup():
    from streamlit.testing.v1 import AppTest
    app=Path(__file__).resolve().parents[1]/"app.py"
    at=AppTest.from_file(str(app),default_timeout=20).run()
    assert not at.exception
    assert len(at.tabs)==3
    assert at.tabs[0].label == "Live search & sheet"
    assert any(b.label == "▶ Start search" for b in at.button)
    assert any(b.label == "⏸ Pause" for b in at.button)

def test_recover_interrupted_session(tmp_path,profile,post):
    from recover import recover
    settings=Settings(root=tmp_path); settings.prepare(); db=Database(settings.db_path)
    session=db.create_session(600); db.save_result(session,post.key,analyze(post,profile))
    assert recover(settings)["saved_records"]==1
    assert db.get_session()["status"]=="recovered"
    assert load_workbook(settings.output_dir/"sessions"/session/"post_links.xlsx")["Post links"]["D2"].value==post.url
    assert (settings.output_dir/"sessions"/session/"opportunities.html").exists()

def test_live_sheet_incremental_and_duplicate_urls(tmp_path,profile,post):
    from exporter import export_link_sheet, link_rows
    r=analyze(post,profile); r["found_at"]="2026-11-04T10:00:00+00:00"
    r["provenance"]=[{"source":"Alert","url":"https://other.example/post"}, {"source":"LinkedIn","url":post.url+"?utm_source=copy"}]
    path=export_link_sheet([r],tmp_path,"first")
    wb=load_workbook(path); assert wb["Post links"].max_row == 3
    assert wb["Post links"]["D2"].hyperlink.target == post.url
    extra=analyze(replace(post,url="https://example.com/second",title="Different post"),profile)
    export_link_sheet([r,extra],tmp_path,"first")
    assert load_workbook(path)["Post links"].max_row == 4
    assert (tmp_path/"post_links.xlsx").read_bytes() == path.read_bytes()
    empty=export_link_sheet([],tmp_path,"second")
    assert load_workbook(empty)["Post links"].max_row == 1
    assert load_workbook(path)["Post links"].max_row == 4

def test_live_feed_escapes_untrusted_posts():
    from live_view import live_feed_html
    page=live_feed_html([{"Title":"<script>evil()</script>","Post link":"https://example.com/post","Company":"<b>Fake</b>"}])
    assert "<script>evil()" not in page and "&lt;script&gt;evil()" in page
    assert 'href="https://example.com/post"' in page
    assert "scrollTop" in page and "scrollTop" not in live_feed_html([],False)

def wait_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.03)
    raise AssertionError("Expected state was not reached")

def test_pause_resume_with_live_sheet(tmp_path,profile,post):
    from threading import Thread
    config=Settings(root=tmp_path,session_minutes=.2); config.prepare(); db=Database(config.db_path)
    db.add_import(post); session=db.create_session(12); db.request_pause(session,True)
    result={}
    thread=Thread(target=lambda:result.update(run_session(config,profile,[],session)))
    thread.start()
    try:
        wait_until(lambda:db.get_session(session)["status"]=="paused")
        assert db.results(session)==[]
        with pytest.raises(ValueError,match="already running"): db.create_session(60)
        path=config.output_dir/"sessions"/session/"post_links.xlsx"
        assert load_workbook(path)["Post links"].max_row==1
        db.request_pause(session,False); thread.join(8)
        assert not thread.is_alive() and result["status"]=="completed"
        assert load_workbook(path)["Post links"]["D2"].value==post.url
    finally:
        if thread.is_alive(): db.cancel(session); thread.join(5)

def heartbeat_source(pipe,path):
    for i in range(100):
        Path(path).write_text(str(i)); pipe.send(("item",i)); time.sleep(.04)

def test_pause_actually_suspends_source_child(tmp_path):
    from threading import Thread
    from runner import pause_checkpoint
    db=Database(tmp_path/"test.db"); session=db.create_session(10)
    path=tmp_path/"heartbeat.txt"; deadline=time.monotonic()+8; checks=[]
    generator=bounded_messages(heartbeat_source,(str(path),),6,
        lambda child:pause_checkpoint(db,session,deadline,child))
    assert next(generator)[0]=="item"
    db.request_pause(session,True)
    def resume_after_check():
        wait_until(lambda:db.get_session(session)["status"]=="paused")
        value=path.read_text(); time.sleep(.2); checks.append(value==path.read_text())
        db.request_pause(session,False)
    thread=Thread(target=resume_after_check); thread.start()
    try: assert next(generator)[0]=="item"
    finally: generator.close(); thread.join(3)
    assert checks==[True]

def test_deadline_still_applies_when_paused(tmp_path,profile):
    config=Settings(root=tmp_path,session_minutes=.05); config.prepare(); db=Database(config.db_path)
    # A pending import forces a checkpoint even with no online sources.
    db.add_import(Opportunity(url="https://example.com/pending"))
    session=db.create_session(3); db.request_pause(session,True)
    started=time.monotonic(); result=run_session(config,profile,[],session)
    assert result["status"]=="timed_out" and time.monotonic()-started<6
    assert (config.output_dir/"sessions"/session/"post_links.xlsx").exists()

def saved_then_paused_worker(db_path,session_id,settings,profile,sources,deadline):
    from exporter import export_link_sheet
    from runner import pause_checkpoint, SessionInterrupted
    os.setsid()
    db=Database(db_path)
    item=Opportunity(text="We are hiring Python interns for six months from February 2027.",
                     url="https://example.com/saved-before-pause",title="Software internship")
    db.save_result(session_id,item.key,analyze(item,profile))
    export_link_sheet(db.results(session_id),settings.output_dir,session_id)
    db.request_pause(session_id,True)
    try: pause_checkpoint(db,session_id,deadline)
    except SessionInterrupted: pass

def test_stop_while_paused_preserves_saved_links(tmp_path,profile):
    from threading import Thread
    config=Settings(root=tmp_path,session_minutes=.3); config.prepare()
    db=Database(config.db_path); session=db.create_session(18); result={}
    thread=Thread(target=lambda:result.update(run_session(config,profile,[],session,
                                                        worker_target=saved_then_paused_worker)))
    thread.start()
    try:
        wait_until(lambda:db.get_session(session)["status"]=="paused")
        path=config.output_dir/"sessions"/session/"post_links.xlsx"
        assert load_workbook(path)["Post links"]["D2"].value=="https://example.com/saved-before-pause"
        db.cancel(session); thread.join(6)
        assert not thread.is_alive() and result["status"]=="cancelled"
        assert len(db.results(session))==1
        assert load_workbook(path)["Post links"].max_row==2
        assert (config.output_dir/"opportunities.html").exists()
    finally:
        if thread.is_alive(): db.cancel(session); thread.join(5)

def test_reports_belong_to_their_session(tmp_path,profile,post):
    first=export_reports([analyze(post,profile)],tmp_path,{"session_id":"first"})
    before=Path(first["xlsx"]).read_bytes()
    second=export_reports([],tmp_path,{"session_id":"second"})
    assert Path(first["xlsx"]).read_bytes()==before
    assert load_workbook(first["xlsx"])["Opportunities"].max_row==2
    assert load_workbook(second["xlsx"])["Opportunities"].max_row==1
    assert (tmp_path/"opportunities.xlsx").read_bytes()==Path(second["xlsx"]).read_bytes()

def test_expired_session_does_not_restart_its_budget(tmp_path,profile):
    settings=Settings(root=tmp_path); settings.prepare(); db=Database(settings.db_path)
    session=db.create_session(1)
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET started_at=? WHERE id=?",("2020-01-01T00:00:00+00:00",session))
    started=time.monotonic()
    result=run_session(settings,profile,[],session,worker_target=stalled_worker)
    assert result["status"]=="timed_out" and db.results(session)==[]
    assert time.monotonic()-started<3

def test_simultaneous_imports_merge_without_errors(tmp_path,post):
    from concurrent.futures import ThreadPoolExecutor
    db=Database(tmp_path/"test.db")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:db.add_import(post),range(12)))
    assert sum(added for _,added in results)==1 and len(db.imports())==1

@pytest.mark.parametrize("source",[
    {"type":"unknown","name":"Bad"},
    {"type":"remotive","name":None},
    {"type":"remotive","name":"Bad","enabled":"false"},
    {"type":"smartrecruiters","name":"Bad","enabled":True},
    {"type":"rss","name":"Bad","enabled":True,"url":"https://example.com/jobs","permission_confirmed":True},
    {"type":"prospects","name":"Bad","enabled":True,"companies":[{"company":"Test","url":"invalid"}]},
])
def test_invalid_source_configuration_rejected(tmp_path,source):
    from collector import load_sources
    path=tmp_path/"sources.json"; path.write_text(json.dumps([source]))
    with pytest.raises(ValueError): load_sources(path)

def test_missing_sources_file_does_not_crash_dashboard(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    settings=Settings(root=tmp_path); settings.prepare()
    monkeypatch.setattr("config.load_settings",lambda:settings)
    at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"),default_timeout=20).run()
    assert not at.exception
    assert any(t.label=="sources.json" and t.value=="[]" for t in at.text_area)
