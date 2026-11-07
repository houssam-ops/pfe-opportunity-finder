"""Hard deadlines with process isolation; the supervisor always exports committed results."""
import argparse
import json
import multiprocessing as mp
import os
import signal
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from collector import collect_source, load_sources
from config import load_settings
from cv_parser import parse_cv
from database import Database
from exporter import export_reports, export_link_sheet
from matcher import analyze, semantic_explanation
from models import from_dict

def _source_child(pipe, source, settings):
    try:
        for item in collect_source(source, settings): pipe.send(("item", item.to_dict()))
    except Exception as e:
        pipe.send(("error", f"{type(e).__name__}: {str(e)[:250]}"))
    finally:
        try: pipe.send(("done", None))
        except (BrokenPipeError, EOFError): pass
        pipe.close()

def _ai_child(pipe, result, profile, settings):
    try: pipe.send(("item", semantic_explanation(result, profile, settings)))
    except Exception as e: pipe.send(("error", type(e).__name__ + ": provider response unavailable or invalid"))
    finally:
        try: pipe.send(("done", None))
        except (BrokenPipeError, EOFError): pass
        pipe.close()

def bounded_messages(target, args, timeout, checkpoint=None):
    """A stalled DNS lookup, TLS socket or parser cannot outlive the task's wall-clock budget."""
    ctx = mp.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(writer, *args))
    process.start(); writer.close()
    deadline = time.monotonic() + max(0, timeout)
    try:
        while True:
            if checkpoint: deadline += checkpoint(process)
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                yield "error", "Hard task timeout reached; partial results were retained."
                break
            if reader.poll(min(.1, remaining)):
                try: kind, value = reader.recv()
                except EOFError: break
                if kind == "done": break
                yield kind, value
            elif not process.is_alive(): break
    finally:
        if process.is_alive():
            try: os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError: pass
        if process.is_alive(): process.terminate()
        process.join(.3)
        if process.is_alive(): process.kill(); process.join(.3)
        reader.close()

class SessionInterrupted(Exception): pass

def pause_checkpoint(db, session_id, deadline, child=None):
    """Pause at transaction boundaries; suspend only the network child, never SQLite writers."""
    session = db.get_session(session_id)
    if session["cancel"] or time.monotonic() >= deadline: raise SessionInterrupted()
    if not session.get("pause"): return 0
    started = time.monotonic()
    stopped = False
    try:
        if child and child.is_alive():
            try: os.kill(child.pid, signal.SIGSTOP); stopped=True
            except ProcessLookupError: pass
        db.set_activity(session_id,"paused")
        db.event(session_id,"Search paused. Links already found are saved in the sheet.")
        while True:
            session = db.get_session(session_id)
            if session["cancel"] or time.monotonic() >= deadline: raise SessionInterrupted()
            if not session.get("pause"): break
            time.sleep(.05)
    finally:
        if stopped:
            try: os.kill(child.pid, signal.SIGCONT)
            except ProcessLookupError: pass
        db.set_activity(session_id,"running")
    db.event(session_id,"Search resumed.")
    return time.monotonic()-started

def pipeline(db_path, session_id, settings, profile, sources, deadline):
    db = Database(db_path)
    def checkpoint(child=None): return pause_checkpoint(db,session_id,deadline,child)
    def snapshot():
        try: export_link_sheet(db.results(session_id),settings.output_dir,session_id)
        except OSError as e: db.event(session_id,"Live sheet could not refresh: " + type(e).__name__ + ". Results remain saved in the database.")
    def process_item(item, identifier=None, check_pause=True):
        if check_pause: checkpoint()
        identifier = identifier or db.add_import(item)[0]
        result = analyze(item, profile)
        db.save_result(session_id, identifier, result)
        snapshot()
    imports = db.imports()
    db.event(session_id, f"Analyzing {len(imports)} saved imports against {profile['filename']}.")
    for identifier, item in imports: process_item(item, identifier)
    for source in sources:
        checkpoint()
        if not source.get("enabled", False) or time.monotonic() >= deadline: continue
        db.event(session_id, "Collecting: " + source["name"])
        count = 0
        for kind, value in bounded_messages(_source_child, (source, settings), min(settings.source_timeout, deadline-time.monotonic()), checkpoint):
            if kind == "error": db.event(session_id, source["name"] + " — " + value)
            else:
                item = from_dict(value)
                identifier, added = db.add_import(item)
                # Retrieve merged provenance; duplicates enrich one row, never add another result.
                with db.connect() as conn:
                    merged = conn.execute("SELECT payload FROM imports WHERE id=?", (identifier,)).fetchone()
                process_item(from_dict(json.loads(merged[0])), identifier, check_pause=False)
                count += 1
        db.event(session_id, f"{source['name']}: {count} records received.")
    if settings.ai_enabled and (not settings.ai_api_key or not settings.ai_model):
        db.event(session_id, "AI credentials/model missing; deterministic matching completed without external AI calls.")
    elif settings.ai_enabled:
        for result in db.results(session_id):
            if result["status"] != "analyzed" or time.monotonic() >= deadline: continue
            # Baseline is already durable; losing an AI request cannot lose a scored opportunity.
            for kind, value in bounded_messages(_ai_child, (result, profile, settings), min(settings.ai_timeout, deadline-time.monotonic()), checkpoint):
                if kind == "error": db.event(session_id, "AI fallback: " + value)
                elif value.get("status") == "ok":
                    result["ai_explanation"] = value["explanation"]
                    result["ai_evidence"] = value["evidence"]
                    result["analysis_method"] = "Deterministic score + AI interpretation with verified quote locations"
                    # result id may differ after a text-only import was enriched; resolve by canonical identity.
                    identifier, _ = db.add_import(from_dict(result))
                    db.save_result(session_id, identifier, result)
                    snapshot()
                else: db.event(session_id, "AI fallback: " + value.get("status", "unavailable"))

def _worker_entry(db_path, session_id, settings, profile, sources, deadline):
    # Every child belongs to this group; supervisor cancellation terminates all descendants.
    if hasattr(os, "setsid"): os.setsid()
    try: pipeline(db_path, session_id, settings, profile, sources, deadline)
    except SessionInterrupted: pass
    except Exception as e:
        Database(db_path).event(session_id, f"Worker error: {type(e).__name__}: {str(e)[:250]}")
        raise

def stop_process_tree(process):
    if process.pid:
        try: os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if process.is_alive(): process.terminate()
        process.join(.3)
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        if process.is_alive(): process.kill(); process.join(.3)

def run_session(settings, profile, sources, session_id=None, worker_target=_worker_entry):
    settings.prepare(); db = Database(settings.db_path)
    session_id = session_id or db.create_session(settings.session_minutes*60)
    session = db.get_session(session_id)
    if not session or session["status"] not in ("running", "paused"):
        raise ValueError("The session is missing or is no longer active.")
    elapsed = max(0, (datetime.now(timezone.utc)-datetime.fromisoformat(session["started_at"])).total_seconds())
    started = time.monotonic(); deadline = started + max(0, session["deadline_seconds"]-elapsed)
    ctx = mp.get_context("spawn")
    process = ctx.Process(target=worker_target, args=(str(settings.db_path), session_id, settings, profile, sources, deadline))
    status = "completed"
    try:
        export_link_sheet([],settings.output_dir,session_id)
        if not session["cancel"] and time.monotonic() < deadline:
            process.start()
        while process.is_alive():
            session = db.get_session(session_id)
            if session["cancel"]: status = "cancelled"; break
            if time.monotonic() >= deadline: status = "timed_out"; break
            process.join(min(.1, max(0, deadline-time.monotonic())))
        if not process.is_alive() and process.exitcode not in (None, 0): status = "failed"
        elif db.get_session(session_id)["cancel"]: status = "cancelled"
        elif time.monotonic() >= deadline: status = "timed_out"
    except BaseException:
        status = "failed"
        raise
    finally:
        stop_process_tree(process)
        collection_elapsed = time.monotonic()-started
        db.update_session(session_id, "exporting")
        rows = db.results(session_id)
        summary = {"session_id": session_id, "result": status, "collection_seconds": round(collection_elapsed, 2),
            "cv": profile["filename"], "cv_sha256": profile["sha256"],
            "analyzed":sum(r["status"] == "analyzed" for r in rows),
            "prospects":sum(r["status"] == "prospect" for r in rows),
            "needs_text":sum(r["status"] == "needs_text" for r in rows),
            "excluded":sum(r["status"] == "irrelevant" for r in rows),
            "events": [e["message"] for e in db.events(session_id)]}
        try:
            export_link_sheet(rows,settings.output_dir,session_id)
            export_reports(rows, settings.output_dir, summary)
            summary["total_seconds"] = round(time.monotonic()-started, 2)
            db.update_session(session_id, status, summary)
            db.event(session_id, "HTML, Excel and CSV reports exported.")
        except Exception as e:
            summary["export_error"] = f"{type(e).__name__}: {e}"
            db.update_session(session_id, "export_failed", summary)
    return db.get_session(session_id)

def main():
    parser = argparse.ArgumentParser(description="Run a bounded PFE opportunity session.")
    parser.add_argument("--session-id")
    parser.add_argument("--minutes", type=float)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--enable-ai", action="store_true")
    args = parser.parse_args()
    settings = load_settings(); settings.prepare()
    if args.minutes is not None:
        if not .001 <= args.minutes <= 120: parser.error("minutes must be between 0.001 and 120")
        settings.session_minutes = args.minutes
    settings.ai_enabled = settings.ai_enabled and args.enable_ai
    try:
        profile = parse_cv(settings.cv_file)
        sources = [] if args.offline else load_sources(settings.root/"sources.json")
        result = run_session(settings, profile, sources, args.session_id)
        print(json.dumps(result, indent=2))
    except Exception as e:
        if args.session_id:
            Database(settings.db_path).update_session(args.session_id, "failed", {"error": str(e)})
        raise

if __name__ == "__main__": main()
