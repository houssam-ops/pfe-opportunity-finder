# PFE Opportunity Finder

A local Ubuntu dashboard for collecting permitted job sources, importing LinkedIn posts and job alerts, and comparing them with your actual PDF CV. No Docker, cloud account or AI subscription is required.

## Launch

From this project directory:

```bash
./start.sh
```

Open **http://localhost:8501**. The launcher checks Python 3.10+, creates `.venv`, installs missing dependencies, creates local folders, copies `.env.example` to `.env` on first launch, and starts Streamlit on loopback.

If Ubuntu lacks virtual environment support, install `python3-venv`. The first installation needs internet access; subsequent offline matching does not. An occupied port can be changed with `PORT=8503` in `.env`. Stop the dashboard with Ctrl+C. An already-started collection process finishes under its own deadline; use **Stop & save** to cancel it and export saved work.

## First session

1. Click **Start search** at the top. First upload your own text-layer PDF under Settings → Replace CV → Use this CV. No personal CV is included in the repository. Public-source settings are provided in sources.json.
2. Watch **Live search & sheet**. New discoveries appear automatically and the results list scrolls to the latest item. Turn off **Follow new results automatically** to read earlier items.
3. Use **Pause**, **Resume**, or **Stop & save** to control the search. The session time limit continues while paused. Stopping preserves the links already saved.
4. Click **Download all post links · Excel** at any time. The sheet refreshes after each processed post and includes original post links, application links, scores and status. Duplicate URLs are merged and distinct source links are retained.
5. To include LinkedIn posts, open **Add LinkedIn posts**, copy each post's text and original URL from LinkedIn, paste both, and choose **Save posts**. Separate posts with a line containing `---`. Start a new search to analyze them. URL-only records are saved as **Needs text**; those links are never fetched or scored from their URLs.

The live list shows collected results, not a recording of LinkedIn or automated feed scrolling. Settings are in the collapsed sidebar; turn public sources off there for an offline session.

Expand **View the sheet** to inspect links inside the app, or **Match explanations & full reports** for detailed scoring and full reports. Clear the score filter to see low-priority records.

You can also import TXT, CSV, EML or saved HTML in **Add LinkedIn posts**. The blank CSV template uses `text,url,company,title,location,published_at,application_url,source`. UTF-8, common Windows encodings and comma/semicolon-separated CSV are supported. Import authorized job-alert emails as `.eml`; no mailbox access is required.

The live Excel sheet is written to `output/post_links.xlsx`, with a separate copy at `output/sessions/<session-id>/post_links.xlsx`. It includes links from all processed records, including company research, needs-text and excluded items, with their status clearly labeled.

Full reports are preserved for each session in `output/sessions/<session-id>/`. Dashboard downloads use that session’s files, so a failed search cannot offer a previous search’s report. The latest copies are also written to:

```text
output/opportunities.html
output/opportunities.xlsx
output/opportunities.csv
```

The HTML report works as a standalone file with search, score/location/category filters, sorting, original links and an embedded Excel download. Excel contains Opportunities, Company research, Needs text and excluded, and Session notes worksheets. Dates and scores are typed, links clickable, headers filtered and panes frozen. Untrusted content is escaped in HTML and protected against spreadsheet formula injection. No synthetic results are included in live exports.

## LinkedIn access

There is no automated feed scrolling, browser control, cookie/session extraction, LinkedIn fetching, or login integration. Your permission to use your account does not supply an authorized LinkedIn feed API to this app. Manual sharing/import is the implemented alternative. Public connectors also reject LinkedIn and `lnkd.in` fetches, including redirects. An authorized integration can later supply records through the same CSV/manual import schema without changing scoring.

## CV and scoring

The PDF is authoritative for skills and experience. Only skills recognized in the supplied PDF contribute to matching. Review extracted evidence in the sidebar. PDF content order is used to avoid interleaving the columns of a two-column CV. Unusual layouts may need a simpler PDF export; all detected evidence is visible for review.

The current search profile targets: six months from February 2027, Casablanca/Rabat/Marrakech, with remote/hybrid eligibility checked. These are separate from CV facts.

The six fixed components total 100:

- Technical requirements present in the CV: **35** points. Recognized job skills matched / recognized job skills requested. No recognizable requirements earns zero, not full points. Missing recognized requirements are listed.
- Duration and starting date: **20** points, 10 each. Six months and February 2027 earn full credit. Unknown duration/start earn three points each; stated conflicts earn zero for that part. Non-internship listings receive zero timing points.
- Location: **15** points for a preferred city or explicitly eligible remote region. Morocco outside preferred cities receives 10; unknown or unconfirmed remote eligibility receives five. Foreign restriction receives zero.
- Professional experience: **15** points, up to two overlapping professional domains extracted from the CV's experience section.
- Role/interests: **10** points, up to two overlapping role domains present in the CV.
- Publication recency: **5** points for up to 14 days, three for 15–30 days, one for 31–90 days. Unknown/future/older dates earn zero. Import time and email receipt time never substitute for publication time.

Excellent: 90–100; very relevant: 75–89; potential: 60–74; low priority: below 60. Senior and non-internship roles are capped at 59. Duration conflicts and explicit geographical restrictions cap scores at 74; expired structured listings cap at 39. Every cap and uncertainty is explained. Explicit PFE status describes wording in the source, not a verified acceptance decision. Missing requirements means recognized missing skills, not an exhaustive semantic guarantee.

The deterministic detector handles French/English recruitment phrases and filters candidate job-seeking posts, career advice and clearly unrelated advertised roles. It is heuristic: review the original before applying. Company identity, dates and location remain unknown when there is insufficient evidence. Junior jobs without explicit internships remain low priority and require confirmation of conversion to a PFE.

## Public sources and permission

Edit `sources.json` in the dashboard or a text editor. Enabled defaults:

- **Devoteam / SmartRecruiters, country `ma`:** documented public posting API; searches Morocco listings and gets relevant posting details. [API documentation](https://developers.smartrecruiters.com/docs/endpoints), [Morocco careers](https://www.devoteam.com/fr-ma/jobs/).
- **Remotive:** documented public remote-jobs API. Original Remotive links and attribution remain in every result. Results are delayed by the provider; requests are cached for six hours, including failures, to avoid frequent polling. Do not republish these records to third-party job boards. [Provider documentation and terms](https://github.com/remotive-com/remote-jobs-api).
- **Company research directory:** verified links for [Inetum Maroc spontaneous applications](https://www.inetum.com/global/en/careers/1f4138ad-91a0-4269-b10f-5a923287599f.html), [SQLI Morocco](https://www.sqli.com/fr-fr/locations/maroc), and Devoteam. These are research leads, have no vacancy score, and do not claim a current PFE opening. Directory notes are dated 2026-11-04 and require revisiting over time.

Additional built-in adapters: [Lever public postings](https://github.com/lever/postings-api), RSS/Atom job feeds, and career pages exposing `JobPosting` JSON-LD. Add a real employer identifier for Lever or SmartRecruiters. Custom RSS and career pages are disabled until you record `permission_confirmed: true` and a valid `permission_url` for documented authorization/terms. Verify those terms yourself; robots.txt alone is not authorization. Career-page collection additionally checks robots.txt and skips on denial/failure. It extracts structured jobs from the explicitly configured page only; it does not crawl a site. The application never bypasses authentication, CAPTCHA, throttling or blocked responses.

Coverage is bounded, not a general web search engine: it checks the configured boards/feeds/pages and a configurable number of records per source. Morocco internship availability may be sparse. Zero suitable results is an honest outcome. Add permitted employer boards and manually shared posts to broaden coverage.

## Optional AI

Deterministic matching is the default and does not call an AI service. To add semantic interpretation, edit `.env`:

```dotenv
AI_ENABLED=true
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=your-own-key
AI_MODEL=your-supported-model
```

Choose a model available to your account that supports Chat Completions JSON mode. The implementation follows the [official Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). Compatible HTTPS providers can be configured. No model name, credit balance, or account is assumed.

Before each UI session, enable **Send CV and job text to my AI provider**. This sends CV text and opportunity content to that configured service and may incur its charges. Keys remain in `.env`/process memory, not reports or the database. CLI sessions require `--enable-ai` as well as environment configuration. AI only adds an interpretation with CV/post quotes checked against the source text; it cannot change the score, extracted skills or vacancy facts. Valid quote locations do not prove an interpretation correct. Missing credentials, rejected responses, invalid JSON/evidence and timeouts fall back to the already-saved local score.

## Deadlines, cancellation and persistence

`SESSION_DURATION_MINUTES=10` is the default. The sidebar can change it for a run. Collection and analysis finish early when there is nothing left to do. The supervisor uses a monotonic deadline, terminates the worker process group at the deadline, and exports committed SQLite results. Exporting and process shutdown happen after the collection deadline and can add a short amount of time; the deadline does not promise that file generation itself finishes within exactly 600 seconds.

Each public source is independently isolated with `SOURCE_TIMEOUT_SECONDS=35`; HTTP requests use `REQUEST_TIMEOUT_SECONDS=12`; optional AI calls use `AI_TIMEOUT_SECONDS=20`. A stalled DNS lookup or nonresponsive source cannot hold the session indefinitely. Partial source results are saved as they arrive. Reports show skipped/failed/timed-out sources. `MAX_ITEMS_PER_SOURCE=150` bounds discovery.

SQLite stores imports, original source attribution, session records, events and per-session results in `data/opportunities.sqlite3`. Duplicates are merged by normalized URL aliases and exact normalized long content when known employer/title identities do not conflict. Tracking parameters are stripped for comparison, but original URLs remain in the record/provenance. No fuzzy merging of merely similar jobs occurs. Repeated sessions rescore saved records against the current CV and current date. Old local posts remain until removed from your local storage; original availability is not automatically verified.

Pause takes effect at a safe processing checkpoint and suspends the active network subprocess. The wall-clock deadline still applies while paused, and Stop & save also works while paused.

One session runs at a time per local database. The dashboard's stop button requests cancellation and exports saved work. If the dashboard was closed during a normal session, reopening it reconnects to status stored in SQLite. If the operating system forcibly kills the supervisor (for example, power loss), reopen the app. After the stored deadline has passed and no collection process remains, **Recover saved results** rebuilds the link sheet and full reports and unlocks new searches. Failed exports show their error and a **Retry saved-result export** button. The CLI recovery command below is also available.

## CLI and tests

```bash
# Process saved imports plus configured sources; finish early when done.
.venv/bin/python runner.py

# Offline imports only, optional shorter session.
.venv/bin/python runner.py --offline --minutes 2

# Optional external AI, only after configuring your provider.
.venv/bin/python runner.py --enable-ai

# Recover committed results after an interrupted process has exited.
.venv/bin/python recover.py

# Test dependencies and all tests.
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests use synthetic records only in temporary directories, never your real inbox or reports. Coverage includes CV PDF extraction (synthetic + this installation's real PDF), missing/scanned PDFs, bulk/text/CSV/email parsing, invalid/missing URLs, French/English detection, weighted scoring, seniority/location/date concerns, duplicate merging and attribution, HTML/Excel/CSV exports, empty results, formula/HTML escaping, unavailable sources/cache cooldowns, missing AI credentials, invalid AI evidence, cancellation, pause/resume, live-sheet updates, source suspension, paused deadlines, expired-session startup, concurrent duplicate imports, invalid source configuration, missing source-file UI recovery, session-specific reports, single-session locking, hard source timeouts, partial-result export at a shortened deadline, and Streamlit startup. The 600-second default is checked explicitly; the same deadline path is exercised with a three-second budget so the regression suite stays fast.

## Files

`app.py` is the UI; `live_view.py` renders the scrolling results; `collector.py` handles permitted sources/imports; `matcher.py` scores and optionally explains; `cv_parser.py` extracts evidence; `database.py` persists it; `exporter.py` writes reports; `runner.py` supervises deadlines; `config.py` validates settings; `models.py` defines normalized records. `.env`, the personal CV, local database/cache, virtual environment and generated reports are ignored by Git.
