"""Export durable results after an interrupted supervisor has fully exited."""
import os
from pathlib import Path
from config import load_settings
from database import Database
from exporter import export_reports, export_link_sheet

def active_workers(root):
    active = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid(): continue
        try:
            if (entry/"cwd").resolve() != root.resolve(): continue
            command = (entry/"cmdline").read_bytes().replace(b"\x00",b" ").decode(errors="replace")
            if "runner.py" in command or "multiprocessing.spawn" in command: active.append(int(entry.name))
        except (OSError, PermissionError): continue
    return active

def recover(settings):
    if active_workers(settings.root): raise RuntimeError("A collection process is still running. Cancel it in the dashboard or wait for its deadline before recovery.")
    db = Database(settings.db_path); session = db.get_session()
    if not session: raise ValueError("There is no saved session to recover.")
    rows = db.results(session["id"])
    summary = {**session["summary"], "session_id":session["id"], "recovered_session":session["id"], "saved_records":len(rows)}
    export_link_sheet(rows,settings.output_dir,session["id"])
    export_reports(rows,settings.output_dir,summary)
    db.update_session(session["id"],"recovered",summary)
    return summary

if __name__ == "__main__":
    try: print(recover(load_settings()))
    except (RuntimeError, ValueError) as e: raise SystemExit(str(e))
