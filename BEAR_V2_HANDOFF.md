# BEAR V2 — B0 Baseline Audit & Modernization Handoff

Workstream: `BEAR-V2-B0 — baseline audit and modernization handoff`
Repository: `mitcherrman/deposummarizer` (public on GitHub)
Branch: `bearv2/b0-baseline`
Audit date: 2026-10-06

This document is investigative. **No product code was changed in B0.** Bear V2 work starts at B1.

### Evidence labels

| Label | Meaning |
|---|---|
| **VERIFIED-code** | Established by reading the code at the audited SHA. The behavior follows directly from the code or from documented library/HTML/Postgres semantics. |
| **VERIFIED-exec** | Reproduced by running the real repository code locally in a throwaway venv. Fakes were used only for OpenAI, the session store and Postgres. See Appendix A. |
| **VERIFIED-live** | Observed read-only (GET/HEAD and page views only) against `bearsummarizer.com` on 2026-10-06. No uploads, logins or form submissions. |
| **INFERRED** | Strongly suggested by evidence but not proven. |
| **UNKNOWN** | Neither the repository nor read-only observation can establish it. |

---

## 1. Executive summary

BearSummarizer (still branded **"Deposum"** in the UI) is a small Django 5-era application of about 2,000 lines. It has no models and no tests, and one Django "app" (`server`) holds everything. Live production serves exactly the static assets in the audited commit (VERIFIED-live: 7 of 7 JS/CSS files are byte-identical), so this baseline does represent what users see.

The core product works as described: PDF upload, EN/ES/both output, include/exclude keyword filter, PyMuPDF extraction with a Tesseract OCR fallback, per-page GPT summaries, a ReportLab summary PDF, DOCX via pdf2docx, and a PGVector-backed chatbot with transcript download. However, the audit found several **pre-existing defects that matter directly to the modernization goals**:

1. **Source-page identity is lost** (VERIFIED-exec). The generated PDF's "Page N" headings are an enumeration counter, not PDF page numbers. In addition, the first two *usable* pages are always discarded, and short pages are silently dropped. In the reproduction, the summary headed "Page 1 / 2 / 3" actually summarized source pages **4 / 6 / 8**, and source pages 2–3 (real testimony) were never summarized. This is the single most important correctness issue for a legal tool. It was introduced on 2025-08-07 (`5c60370`).
2. **DOCX download is broken on current `main`** (VERIFIED-exec against pdf2docx 0.5.6, 0.5.8, 0.5.11 and 0.5.13). `Converter(...).convert(buf).close()` calls `.close()` on `None`, which raises `AttributeError` and returns HTTP 500. This was also introduced in `5c60370`. It was not exercised live, because that would have required uploading a document.
3. **The prompt claims whole-document context that is not provided** (VERIFIED-code and VERIFIED-exec). Each LLM call receives exactly one page, truncated to 4,000 characters. The sentence is left over from a 2024 design that did pass the whole document.
4. **Summary formatting is model-authored.** The prompt asks for up to 3 `•` bullets separated by `<br/>`, and that raw string is fed straight into a ReportLab `Paragraph` (VERIFIED-code). Bullets therefore have no hanging indent (VERIFIED-exec, rendered). If the model uses newlines instead of `<br/>`, bullets run together. Angle-bracket text is silently eaten as markup.
5. **Data lifecycle and privacy gaps undercut the "encrypted vector DB" story.** The full uploaded deposition is stored base64 in the Django session row. That data is signed but not encrypted, it is never read again, and **"Clear data" does not remove it** (VERIFIED-exec). The hourly cleanup SQL misuses Postgres `trim()` and will delete about 19% of *active* sessions' chatbot collections (VERIFIED-code, not executed: no local Postgres). Logging in after summarizing double-encrypts the chatbot's vector text (VERIFIED-exec at unit level).
6. **`www.bearsummarizer.com` renders a blank page** (VERIFIED-live). The `base.js` hostname allowlist omits `www.`.
7. **Mobile is effectively unsupported.** There are zero `@media` queries. On a 375px phone the output page lays out at 423px and the summary viewer is 108px wide. The navbar hamburger and logo are dark on navy and nearly invisible (VERIFIED-live).

None of this requires a new architecture. Everything above fits the approved B1–B6 plan with Django templates/JS/CSS plus a contained summarizer change. Section 17 proposes the stages, and Section 15 lists the invariants that must not break.

Decisions the owner should make before or early in the work are collected in **Section 19.3**.

---

## 2. Exact audited base

| Item | Value |
|---|---|
| Worktree | `C:\Users\mlmit\Desktop\bear-v2-b0` (linked worktree of `C:\Users\mlmit\Desktop\deposummarizer`) |
| Branch | `bearv2/b0-baseline` (tracking `origin/main`) |
| Starting `HEAD` | `fc61efc4c6795aaa049b67093504908d5be28189` |
| `origin/main` (after `git fetch`, and via `git ls-remote`) | `fc61efc4c6795aaa049b67093504908d5be28189` |
| Expected SHA in handoff | `fc61efc4c6795aaa049b67093504908d5be28189` — **matches, no discrepancy** |
| Worktree state at start | clean (`nothing to commit, working tree clean`) |
| Other worktrees | `C:/Users/mlmit/Desktop/deposummarizer` on `main` @ `fc61efc` (untouched) |
| Commits on `main` | 192; latest `fc61efc 2025-10-06 "bug fix"` |
| Other remote branches | `deposum-with_unit_tests` (2 ahead/127 behind, 2024), `dj-stripe` (1 ahead/17 behind, payments — out of scope), `spanish-translation` (merged), `summarizer-prompt-tester` (merged) |

Local audit environment: Windows 11, CPython 3.14.7, throwaway venv **outside** the repository. `requirements.txt` was installed minus `frontend` and `tools` (see §14). Because requirements are unpinned, this resolved to Django 6.1.1, PyMuPDF 1.28.2, reportlab 5.0.1, pdf2docx 0.5.13, langchain 1.4.3, langchain-core 1.6.6, langchain-openai 1.6.7, langchain-postgres 0.0.18, psycopg 3.3.6, boto3 1.43.108 and pycryptodome 3.24.0. **Production package versions are UNKNOWN.**

---

## 3. Verified existing product contract

Every capability below must survive Bear V2 unless the owner explicitly approves a change (see §15).

| Capability | How it works today | Status |
|---|---|---|
| PDF upload | `POST /summarize`, multipart field `file`, `accept="application/pdf"`; no size/type check server-side | VERIFIED-code |
| Language | radio `lang` ∈ `en` \| `es` \| `both` (400 on anything else). Spanish is produced by translating the English summary. | VERIFIED-code |
| Keyword filter | radio `filterType` ∈ `none` \| `include` \| `exclude` (400 otherwise); repeated text field `filterText`; sanitized to `[a-zA-Z0-9- ]` | VERIFIED-code / VERIFIED-exec |
| Text extraction | PyMuPDF `get_text("blocks")`, discarding blocks that touch an 8% top/bottom/side margin | VERIFIED-code |
| OCR fallback | `page.get_textpage_ocr()` only if Tesseract is discoverable **and** native text fails `is_page_valid`; final fallback is unfiltered `get_text("text")` | VERIFIED-code (Tesseract presence in prod UNKNOWN) |
| Summarization | one LLM call per page (plus one translation call for es/both); background thread; status polled | VERIFIED-code / VERIFIED-exec |
| Chatbot init | full extracted text → 1,000/200 recursive chunks → encrypted PGVector collection `collection_<session_key>` | VERIFIED-code |
| Retrieval Q&A | `POST /ask` (`question`); top-k similarity (`k = max(6, db_len/32)`); full chat history replayed; answer returned as plain text | VERIFIED-code |
| Summary PDF | ReportLab, served inline at `/out` → `/out/pdf`, shown in an `<iframe>` on `/output` | VERIFIED-code / VERIFIED-exec |
| DOCX download | `/out/docx` converts the summary PDF with pdf2docx on every request | VERIFIED-code; **currently raises → 500** (VERIFIED-exec) |
| Chat transcript | `GET /transcript`, `text/plain`, `Q:`/`A:` lines | VERIFIED-code |
| Accounts | optional username/password sign-up, login, logout; **no feature requires login** (no `login_required` anywhere) | VERIFIED-code |
| Session cleanup | 12h session age; `Clear data` pops selected keys; logout flushes the session and deletes the vector collection; `hourly.py` → `manage.py clearsessions` → custom `clear_expired` deletes orphan collections | VERIFIED-code (cron schedule UNKNOWN) |

---

## 4. Current architecture

### 4.1 Repository structure (VERIFIED-code)

```
manage.py
requirements.txt            # 16 unpinned packages (incl. stray `frontend`, `tools`)
README.md                   # partly stale (see §14)
.vscode/launch.json         # tracked despite .gitignore
server/
  settings.py  urls.py  views.py  util.py  wsgi.py  asgi.py
  hourly.py                 # cron-style cleanup script (shells out to clearsessions)
  vector_db_session.py      # custom SESSION_ENGINE that also manages PGVector collections
  rotating_key_db_engine/   # custom Postgres backend: re-reads creds from AWS Secrets Manager on auth failure
  PGVector_encrypt/         # PGVector subclass: AES-CTR encrypts chunk text
  summary/summarizer.py     # extraction, OCR, prompts, LLM calls, translation, ReportLab PDF, orchestrator
  summary/deposition_chatbot.py  # chunking, PGVector init, RAG Q&A
  templates/                # base, home, output, login, new, about, contact, 404, chat_message
  static/{styles,javascript,images}/
```

There are no `models.py`, migrations, `tests/`, Dockerfile, CI config, nginx config or IaC in the tree (VERIFIED-code).

### 4.2 Runtime (VERIFIED-code unless noted)

- **Web:** Django (settings generated by 5.0.7), function views, server-rendered templates, Bootstrap 5.3.3 from the jsDelivr CDN, plain JS. jQuery 3.7.1 slim and Bootstrap Icons 1.11.3 are loaded but **unused**.
- **Database:** PostgreSQL through the custom engine `server.rotating_key_db_engine`. The same database holds Django sessions/auth and the `langchain_pg_collection`/`langchain_pg_embedding` tables (pgvector).
- **Sessions:** `SESSION_ENGINE = 'server.vector_db_session'` (DB-backed). The session row carries the job state, the uploaded PDF, the summary PDF and the chat history. See §7.1.
- **Background work:** a `threading.Thread` per upload, inside the web process. The lock is a process-local `threading.Lock`. There is no queue, retry or persistence of jobs.
- **LLM:** LangChain `ChatOpenAI`. The summary model is `GPT_MODEL` (default `gpt-4o-mini` in the summarizer, **required** with no default in the chatbot), `temperature=1`. The translator is hard-coded to `gpt-3.5-turbo-0125` with no explicit temperature. Embeddings use `text-embedding-3-small`.
- **Live front end** (VERIFIED-live): `Server: nginx`. `http://` → `307` → `https://` (redirect happens before Django; `SECURE_SSL_REDIRECT` is unset). Django's HSTS header (`max-age=1209600; includeSubDomains; preload`) is present, `DEBUG` is off (the custom `404.html` is served), and `www.` plus `bear-ai-summarizer.com` both reach the app.

### 4.3 Import-time side effects (VERIFIED-exec)

Importing `server.urls` imports `views` → `summarizer` and `deposition_chatbot`. Those **construct OpenAI clients at import time** and call `config("OPENAI_KEY")` with no default. As a result, *every* management command, including `check`, fails without `OPENAI_KEY`. Any future test suite will need lazy client construction or a test env (see §21).

### 4.4 CSS / JS architecture (VERIFIED-code)

- **CSS:** Bootstrap 5.3.3 (CDN, no SRI on the CSS link) plus one global `base.css` (navy/gold tokens on `:root`, navbar, message banner, `.container` card, go-to-top button). There is one page stylesheet per screen (`home.css`, `output.css`, `login.css`, `new.css`). `login.css` duplicates `home.css` rules and lacks the login layout classes. There are no media queries and no build step.
- **JS:** plain global-scope scripts, one per page, with no modules and no bundler.
  - `base.js`: hostname gate, scroll-to-top, `logoutConfirm`, `clearConfirm`, `removeMessage`.
  - `home.js`: form validation, filter add/remove, `HEAD /out` probe. It is **included twice** in `home.html` (lines 11 and 81). The first copy runs before the DOM exists, so its listeners attach to nothing. `addEventListener("load", checkForSummary())` *calls* the function immediately, so the probe fires twice.
  - `output.js`: 1s polling, iframe swap, chat, download-format switch.
  - `new.js`: client-side password match.
- Bootstrap JS (not the bundle) drives the navbar collapse. jQuery slim is loaded but unused, and so is the Bootstrap Icons CSS.

---

## 5. Route / screen inventory (VERIFIED-code; screens VERIFIED-live)

| Path | View | Methods | Purpose / notes |
|---|---|---|---|
| `/` | `RedirectView` → `/home` | any | |
| `/home` | `home` | GET only (HEAD → 405, VERIFIED-live) | Upload form, language, filter, Summarize, Clear data, "already summarized" hint |
| `/output` | `output` | GET | Processing spinner → summary iframe + download + chat |
| `/about` | `about` | GET | Two sentences + GitHub link |
| `/contact` | `contact` | GET | bearinc.com link, two developer names with **personal email addresses**; one link is `about:blank` |
| `/login` | `login_page` | GET | redirects to home if authenticated |
| `/new` | `new_account` | GET | create-account form |
| `/summarize` | `summarize` | POST | starts the worker, redirects to `/output` |
| `/out/verify` | `verify` | GET | polled every 1s. `200` + status text while running; **`418`** when not running; `409` if no session |
| `/chat` | `chat_html` | GET | server-rendered chat bubbles (restores history) |
| `/ask` | `ask` | POST | chatbot question; `409` if no summary; `500` on OpenAI failure |
| `/clear` | `clear` | POST (GET just redirects) | pops session keys (incomplete, see §7.1) |
| `/out` | `RedirectView` → `/out/pdf` | any | |
| `/out/pdf` | `out` | GET/HEAD | inline PDF; HEAD used as "summary exists?" probe |
| `/out/docx` | `out_docx` | GET/HEAD | **GET currently 500s** (see §13) |
| `/transcript` | `transcript` | GET | text/plain chat log |
| `/create` | `create_account` | POST | |
| `/auth` | `auth` | POST | |
| `/logout` | `logout_user` | POST | |
| `/delete` | `delete_account` | POST | **no UI**; anonymous call raises `NotImplementedError` → 500 (VERIFIED-exec) |
| `/session`, `/cyclekey` | debug | any, `csrf_exempt` | only registered when `DEBUG=True` |
| `admin` | — | — | commented out |

Templates: `base.html` (shell), `home.html`, `output.html`, `login.html`, `new.html`, `about.html`, `contact.html`, `404.html`, and `chat_message.html` (fragment used by `/chat`).

Duplicate route names (`home` ×2, `out_pdf` ×2) are harmless today, but `{% url %}`/`reverse` resolve to the last one registered.

---

## 6. Current summarization pipeline (VERIFIED-code + VERIFIED-exec)

```
POST /summarize (views.summarize)
 ├─ validate file / lang / filterType; sanitize filterText → [a-zA-Z0-9- ]
 ├─ pop stale keys: summary_pdf, status_msg, db_len, num_docs, chat_history, prompt_append
 ├─ "double-submit" guard: if session.db_len == -1 → redirect   ← DEAD: db_len was just popped (VERIFIED-exec)
 ├─ session.update(db_len=-1, prompt_append=[], summary_lang, depo_pdf=<base64 of whole PDF>)
 └─ Thread(worker).start() → redirect /output

worker → summarizer.create_summary(pdf_bytes, sid, lang, keywords, exclude_bool)
 1. extract_text_pages(): for each PDF page
      text = native blocks minus margins
           → else OCR blocks minus margins (if Tesseract)
           → else unfiltered native text
      keep text only if is_page_valid(text)   # len ≥150 OR contains "exhibit|affidavit|page|witness"
      → returns list[str]  (NO page numbers retained)
 2. pages = raw_pages[2:]      # drops the first two *kept* pages, whatever they are
    raw_text = "\n\n".join(raw_pages)   # chatbot gets ALL kept pages, incl. the two dropped
    db_len_value = len(pages)
 3. cb.initBot(raw_text, sid)  # failure only logged; summary continues
 4. summarize_deposition(pages):
      for i, pg in enumerate(pages, 1):
        if len(pg) < 150: continue          # second, silent drop (keyword-valid short pages)
        en = LLM(system prompt, user=pg[:4000])   # one page only, truncated
        es = translator(en) if lang in (es, both)
        summaries.append({"en": en, "es": es} filtered by lang)
 5. write_summaries_to_pdf(): heading = f"Page {enumerate_index}"
 6. finally: unless race_check → session.summary_pdf = base64(pdf); session.db_len = db_len_value
```

Key behaviors:

- **Prompt** (`getPrompt`): three variants (none/include/exclude). All of them say *"Provide a brief summary of each page, considering the context of the entire document"* and *"Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022). Separate bullet points with `<br/>`."* (VERIFIED: the `•`/`<br/>` contract stated in the B0 brief is accurate.) Filtered variants add *"…say that no important information is on the page."*
- **Model/temperature:** `GPT_MODEL`, `temperature=1` for summaries and chat. The translator uses `gpt-3.5-turbo-0125` with the provider default temperature. The value of `GPT_MODEL` in production and the availability of `gpt-3.5-turbo-0125` today are both **UNKNOWN**.
- **Retries:** `_chat_with_retries` makes 5 attempts with linear backoff (8, 16, 24, 32, 40 s, i.e. up to 120 s per call). On final failure it **returns the string** `"⚠️ EN page N failed after 5 retries."`. That string is printed into the PDF as if it were a summary, and the job reports success. The `⚠️` glyph renders as `II` in Helvetica (VERIFIED-exec). If the hard-coded translator model were unavailable, every ES/both page would take about 2 minutes to fail (INFERRED from code).
- **Cancellation:** `race_check(sid)` returns True when `session.db_len != -1`. "Clear data", logout and an expired session therefore all abort the worker between steps (VERIFIED-code).
- **Progress:** `status_msg` values are "Extracting text… N% (i/total)", "Configuring chatbot…", "i/total pages processed…", "Building PDF summary…" and "Finished ✓ Ready to download." There is no percentage during summarization.
- **Error surfacing:** `create_summary` catches everything, sets `status_msg="❌ Error: …"` and `db_len=0`. `verify` then returns 418, so the UI stops polling and loads the iframe, which shows *"No summary available"*. **The error message is never shown to the user** (VERIFIED-code). The worker's own `db_len=-2` branch is reachable only if an exception escapes `create_summary` itself.
- **Short documents:** if ≤2 pages pass `is_page_valid`, then `pages == []`, `db_len = 0`, and the job is treated as a **failure**, so chat is disabled even though `initBot` ran (VERIFIED-code).
- **Extraction loss:** a text block is discarded **whole** if it merely touches the 8% side margin. For narrow-margin transcripts this silently drops testimony while the page still passes validity, so no fallback runs (VERIFIED-exec: a block starting at x=45pt on a 612pt page was dropped). How common such PDFs are is **UNKNOWN**. Use real transcript fixtures in B4.
- **Validity heuristic:** the keyword `"page"` matches almost any transcript text ("Page 12", "page"), so nearly every non-empty page is "valid" (VERIFIED-exec; it accidentally matched the audit's own marker).

---

## 7. Current chatbot / RAG pipeline (VERIFIED-code)

- `initBot(raw_text, sid)`: `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)` → `split_text` (**no metadata, no page numbers**) → `PGVectorEncrypt(collection_name=f"collection_{sid}", pre_delete_collection=True)` → `add_texts`. Held under a process-local `db_lock`.
- `askQuestion(question, sid, prompt_append, db_len)`: builds a retriever with `k = max(6, int(db_len/32))`, where `db_len` is **pages**, not chunks. Retrieval embeds the **current question only** (not history-aware). The system prompt reads: *"answer… using the excerpts… If you don't know… three sentences maximum… Include the exact quote(s)"*, with the excerpts inlined. The **entire** prior `prompt_append` history is replayed (unbounded growth). `temperature=1`. A bare `except` returns `None`, which becomes a 500 "OpenAI call failed".
- History lives in `session['prompt_append']` and is restored by `GET /chat` (server-rendered and autoescaped, so XSS-safe). New bubbles are added with `innerText` (safe).
- Because chunks carry no page metadata, **answers cannot cite page numbers**. Adding page metadata to chunks is a small, contained change that B4's page-identity model would enable. It is *not* a new RAG architecture, but it is optional and needs owner approval (§19.3).
- Encryption and lifecycle: see §7.1 and §14 (S1–S3, S13).

### 7.1 Session & data lifecycle (VERIFIED-code unless noted)

| Data | Where | Created | Removed by |
|---|---|---|---|
| Uploaded PDF (`depo_pdf`, base64) | `django_session.session_data` (signed, **not encrypted**) | `/summarize` | logout (flush), session expiry + `clearsessions`. **Not** removed by Clear data (VERIFIED-exec), and never read by any code |
| Summary PDF (`summary_pdf`, base64) | session row | worker `finally` | Clear data, new upload, logout, expiry |
| Chat history (`prompt_append`) | session row | `/ask` | Clear data, new upload, logout, expiry |
| Job state (`db_len`, `status_msg`, `num_docs`, `num_questions`, `summary_lang`) | session row | `/summarize`, worker, `/ask` | Clear data pops `db_len`, `status_msg`, `num_docs`; `num_questions` and `summary_lang` persist |
| Chunk text (AES-CTR encrypted) + embeddings (plaintext vectors) | `langchain_pg_embedding` (cascade from `langchain_pg_collection` `collection_<sid>`) | `initBot` | logout/flush → `SessionStore.delete` → `pre_delete_collection`; hourly `clear_expired` orphan sweep (buggy, S2). **Not** removed by Clear data; replaced on next upload |
| Session key rotation (login) | — | `login()` → `cycle_key` | collection is copied to the new key with double encryption (S3); an in-flight job is orphaned (S4) |

`SESSION_COOKIE_AGE` is 12h. `hourly.py` shells out to `manage.py clearsessions`. Whether cron actually runs it is UNKNOWN.

---

## 8. Deployment evidence and unknowns

| Topic | Evidence | Label |
|---|---|---|
| Public site | `bearsummarizer.com`, nginx in front, TLS, DEBUG off, static assets identical to `fc61efc` | VERIFIED-live |
| Extra hostnames | `www.bearsummarizer.com` and `bear-ai-summarizer.com` serve the app; `www.` is blanked by `base.js` | VERIFIED-live |
| WSGI server | `gunicorn` in requirements. A dev gunicorn config (2 workers, `0.0.0.0:8000`, `reload=True`, `daemon=True`) existed until `b74b065` and `gunicorn` is now gitignored | VERIFIED-code (history); current prod config **UNKNOWN** |
| Database | Postgres + pgvector; `sslmode=verify-full` with `DB_CA_PATH`; creds from **AWS Secrets Manager** (`DB_SECRET_ARN`, region `us-east-2`, boto3 profile `db_access`); re-fetched on auth failure ("rotating key") | VERIFIED-code |
| DB service | Amazon RDS with Secrets Manager rotation | INFERRED |
| Host | a VM with an AWS shared-credentials profile `db_access` (e.g. EC2) and a POSIX shell (`hourly.py` uses `cd …; python3`) | INFERRED |
| Hourly cleanup | `server/hourly.py` exists; whether/how it is scheduled | **UNKNOWN** |
| Deploy process | `upload.sh` and `scripts/` are gitignored; no CI/IaC | **UNKNOWN** |
| Static serving | `STATIC_ROOT` env + `collectstatic`; nginx likely serves `/static/` | INFERRED |
| Python version in prod | history contains `cpython-310` and one `cpython-312` `.pyc`; README says 3.10+ | INFERRED 3.10–3.12; **UNKNOWN** |
| Tesseract in prod | needed for OCR fallback; silently skipped if absent | **UNKNOWN** |
| Package versions in prod | requirements unpinned | **UNKNOWN** |
| `GPT_MODEL` value in prod | env var | **UNKNOWN** |
| Upload size limit | none in Django; nginx `client_max_body_size` | **UNKNOWN** |
| Logging/monitoring/backups | nothing in repo | **UNKNOWN** |

**Implication:** every Bear V2 phase that changes Python dependencies, fonts (B5) or OCR needs a prod environment check that only the owner can do. Record the prod `pip freeze`, Python version, `tesseract --version` and `GPT_MODEL` before B4/B5 merge.

---

## 9. Current visual / UI deficiencies (VERIFIED-code + VERIFIED-live screenshots)

- **Brand incoherence:** UI says "Deposum", the domain is BearSummarizer, the logo is "bearinc", the README says "Deposummarizer", and `base.css` is titled "Cal Berkeley look & feel" (navy `#003262` / gold `#FDB515`). The default `<title>` block is "Django App". The summary PDF uses Bootstrap blue `#007bff`.
- The **logo** (dark artwork) sits on a navy navbar and is barely visible. `favicon.ico` is actually a WebP file.
- **Missing asset:** `base.css` references `/static/images/bear-claw.svg`, which returns 404 (VERIFIED-live). `loading.gif` is unused. The "Roboto" font is named but never loaded.
- **Home/upload:** a bare file input; filter radios are raw `<input>` elements with invalid `</input>` closers and `<br/>` spacing; "Add filter / Remove filter" buttons; a red "Clear data" button sits beside the primary action; and the processing indicator is a gavel GIF.
- **Processing:** text status next to the gavel GIF. There is no progress bar, no stages and no time expectation, and failures look like success followed by "No summary available".
- **Output:** the PDF sits in an iframe at `aspect-ratio: 3/2`; there is a `<select>` plus a 1em download icon; the chat is a fixed-width sidebar titled "Chatbot:" with no send button (Enter only) and no empty state. The transcript download stays hidden after reload even when history exists.
- **Messages:** a `?msg=` banner using a non-standard `<c>` tag.
- **Auth pages:** `login.html` uses `.login-container/.login-box`, but only `new.css` defines them, so login is unstyled. `new.html` has two `<label for="password">`.
- **About/Contact:** minimal; personal emails are exposed; one developer link is `about:blank`.
- **Go-to-top button:** appears only when scrolled to the very bottom, centered over content.

## 10. Current responsive / mobile deficiencies

- **Zero `@media` queries** in any stylesheet (VERIFIED-code).
- `/output` at a 375px device width: the layout viewport expands to **423px** (horizontal overflow), the chat panel is cut off, and the summary iframe is **108px wide** (VERIFIED-live).
- `.body-container{display:flex}` has no wrap, and `.chat-container{width:300px; flex:1; max-height:80vh}` with chat bubbles fixed at `width:60%`.
- Inline PDF-in-iframe is poorly supported on mobile browsers (iOS Safari renders only a static first page; Android Chrome typically won't render it inline). A text/HTML rendering of the summary would be needed for a good mobile experience (INFERRED, platform behavior).
- Navbar uses `navbar-light` on a navy background, so the hamburger icon is dark-on-dark (VERIFIED-live).
- Status and spinner sizing are fixed in pixels; touch targets (download icon, close "×") are about 16px.

---

## 11. Current LLM / prompt deficiencies

1. **False context claim:** "considering the context of the entire document" when only `pg[:4000]` is sent (VERIFIED-exec: every call contained exactly one source page).
2. **Formatting delegated to the model:** `•` and `<br/>` are in the prompt; the output is unvalidated and goes straight into ReportLab markup. Newline-separated bullets collapse into one line, `-`/`*`/Markdown bullets pass through as-is, and `<Exhibit 4>` is silently deleted as an unknown tag (all VERIFIED-exec).
3. **`temperature=1`** for factual legal summarization and Q&A. This is unjustified, and lower values should be evaluated (some newer models restrict temperature, so verify per candidate).
4. **No fidelity instructions:** nothing about preserving names, dates, amounts, exhibit numbers, quotes or speaker attribution (Q vs. A, which attorney), stating uncertainty, or refusing to infer legal conclusions.
5. **No injection boundary:** deposition text is the whole user message with no delimiting. Text inside a transcript could steer output (INFERRED, low likelihood).
6. **Filter semantics:** the include/exclude list is embedded in the prompt. Pages with no relevant content produce a filler line ("no important information") rather than a structured status. The sanitizer **strips non-ASCII letters**, so Spanish keywords are corrupted (`niño` → `nio`, `café` → `caf`; VERIFIED-exec).
7. **Translation:** a separate legacy model translates the *English string including `<br/>`*, with the ambiguous instruction "preserve line breaks". It is also unvalidated, and `translate_to_spanish()` is dead code.
8. **Failures are content:** the retry-exhaustion string is rendered as a summary.
9. **No evaluation harness.** The `summarizer-prompt-tester` branch (2024, merged) is the only prior art.
10. **Chatbot:** `temperature=1`, unbounded history, retrieval ignores history, `k` is keyed off page count, and answers have no page citations.

---

## 12. Source-page-number correctness analysis

### 12.1 Exact current behavior (VERIFIED-code)

| Step | Code | Effect on page identity |
|---|---|---|
| Extraction | `extract_text_pages` appends `text` only when `is_page_valid(text)`; returns `list[str]` | PDF page index is **discarded**; blank, short and non-keyword pages vanish and shift everything after them |
| Cover skip | `pages = raw_pages[2:]` in `create_summary` | drops the first two **kept** pages, which are *not necessarily* cover pages; if pages 1–2 were blank, real testimony is dropped |
| Short-page skip | `if len(pg) < 150: continue` in `summarize_deposition` | pages kept by keyword (e.g. "Exhibit 12 was marked.") are silently skipped, with no record |
| Headings | `for idx, item in enumerate(summaries, start=1): Paragraph(f"Page {idx}")` | heading = ordinal of the summary record |
| `db_len` | `len(pages)` (after slice, before short-skip) | not equal to summarized-page count; used as chatbot `k` input and success flag |
| Chatbot | `raw_text` = all kept pages, **including** the two sliced off | chatbot can answer about pages the summary omits |

### 12.2 Empirical reproduction (VERIFIED-exec, Appendix A)

The test used a synthetic 8-page PDF: p1 caption only (short), p2–p4 testimony, p5 "Exhibit 12 was marked." (short and keyword-valid), p6 testimony, p7 blank, p8 testimony.

```
returned db_len           : 4
chatbot text contains     : pages 2,3,4,5,6,8
LLM calls                 : 3   (inputs: page 4 | page 6 | page 8 — one page each)
generated-PDF heading 'Page 1'  ->  source page 4
generated-PDF heading 'Page 2'  ->  source page 6
generated-PDF heading 'Page 3'  ->  source page 8
```

**Every heading is wrong**, and source pages 2 and 3 (testimony) are never summarized. With a typical real transcript (cover + appearances + testimony, no blanks), the minimum error is an offset of +2, and every blank or short page adds more drift.

### 12.3 Origin

`git log -S` shows that `raw_pages[2:]` and `Page {idx}` both arrived in `5c60370` (2025-08-07, "spanish translation added with new UI colors"). The earlier `deposum-with_unit_tests` branch carried `f"Page {page_num + 1}"` through extraction (`extract_text_with_numbers`). This is a regression, not an original design limitation.

### 12.4 Note on "page number" semantics (decision required)

Two numbering schemes exist:

- **PDF page index:** what the pipeline can carry deterministically.
- **Printed transcript page number:** what lawyers cite as page:line. It often differs, for example in condensed 4-up transcripts or with front matter.

The printed number usually lives in the header and footer, which the 8% margin filter currently removes. B4 should at minimum carry the **PDF page index** end to end and label it unambiguously ("PDF p. 14"). Extracting printed transcript page/line numbers is a possible later enhancement. It needs an owner decision and is not assumed in scope.

---

## 13. Generated-PDF analysis (VERIFIED-exec, rendered)

- `SimpleDocTemplate(letter, 1-inch margins)`. The story is just `Paragraph("Page N")` (Helvetica-Bold 16, `#007bff`, leading 16) → EN `Paragraph` (Helvetica 12/14) → ES `Paragraph` (Times-Italic 11/13, grey) → `Spacer`.
- There is **no** title block, source filename (the upload filename is never stored), generation date, language or filter disclosure, page count, running header/footer, page numbers, "AI-generated — verify against transcript" disclaimer, or PDF metadata (title/author).
- **Bullets** are characters typed by the model. Wrapped lines return to the left margin, so there is **no hanging indent** (rendered and confirmed). Spacing between bullets depends on the model emitting `<br/>`.
- **Fonts:** only the base-14 fonts (Helvetica, Helvetica-Bold, Times-Italic). Latin-1 accents and `•` render and survive the text layer, including into DOCX (VERIFIED-exec). Characters outside WinAnsi do not render correctly (e.g. `⚠️` → `II`). There is no embedded Unicode TTF.
- **Bilingual:** EN block followed by the ES block under the same heading, distinguished only by grey italics, with no labels.
- **Markup injection:** LLM text is parsed as ReportLab mini-HTML. `&` and `<` happened to survive in the tested reportlab 5.0.1. Unknown tags are silently dropped (content loss, VERIFIED-exec). Malformed markup can raise a parse error that fails the whole job (INFERRED, from ReportLab's parser behavior). B5 must escape all model text and own every tag.
- **DOCX** (pdf2docx of this PDF, using a corrected call): each "Page N" heading is merged into the same paragraph as the first bullet, there are no Word heading styles, and no list numbering is used (bullets are literal `•` text). **The production call raises `AttributeError` before any of this** (`views.py:252`). In every checked pdf2docx release (0.5.6, 0.5.8, 0.5.11, 0.5.13), `Converter.convert()` has no `return`. The earlier working form was `conv = Converter(...); conv.convert(...); conv.close()` (commit `fa4f4e7`).

---

## 14. Security / config / documentation issues relevant to this project

Ordered by relevance to Bear V2. "Fix in" proposes an owning phase, but any behavior or security change still needs owner approval.

| # | Issue | Label | Fix in |
|---|---|---|---|
| S1 | **Raw deposition PDF stored in session** (`depo_pdf`, base64 in `django_session`, signed not encrypted), never read anywhere, **not removed by Clear data**. Summary PDF and chat history are also stored unencrypted. This undermines the PGVector encryption claim. | VERIFIED-code/exec | B6 (decision) |
| S2 | `clear_expired` SQL uses `trim(LEADING 'collection_' FROM name)`. Postgres `trim` strips a **character set**, not a prefix, so session keys starting with any of `c e i l n o t` (≈7/36 ≈ 19%) never match, and those **active** sessions' collections are deleted hourly. `PGVector.__post_init__` then silently recreates an empty collection and the chatbot answers "I don't know". | VERIFIED-code (documented Postgres semantics; not executed) | B6 / hotfix (decision) |
| S3 | `_rename_vector_collection` (runs on login via `cycle_key`) reads stored ciphertext and passes it to the encrypting `add_embeddings`, which **double-encrypts** it. The upsert by id moves rows into the new collection, so retrieval then returns ciphertext. Latent since encryption landed (`4a1719b`, 2025-09-19). | VERIFIED-exec (unit level); end-to-end INFERRED | B6 / hotfix (decision) |
| S4 | Logging in mid-job: `cycle_key` copies `db_len=-1` into the new session, the worker's `race_check` on the old sid aborts, and the new session polls "Working…" until expiry. | INFERRED (code path) | B2 |
| S5 | Double-submit guard is dead (checks `db_len` after popping it). A second upload in the same session starts a second worker. Both workers see `db_len==-1`, so the **first to finish wins**, possibly the *older* document, and both rebuild the same vector collection. | VERIFIED-exec (guard), INFERRED (race outcome) | B2 |
| S6 | `www.bearsummarizer.com` renders blank: `base.js` allowlist `['bearsummarizer.com','bear-ai-summarizer.com','127.0.0.1','localhost']`. The same mechanism blanks any staging or preview hostname. | VERIFIED-live | B1 |
| S7 | Logout button has no `type`, so it defaults to `submit`. `onclick="logoutConfirm()"` does not prevent the default, so **"Cancel" still logs out** (and clears data). | VERIFIED-code (HTML default button type) | B1 |
| S8 | `/delete` (no UI) raises `NotImplementedError` for anonymous users → 500. | VERIFIED-exec | B1 |
| S9 | Account creation bypasses `AUTH_PASSWORD_VALIDATORS` (`create_user` does not run them); password confirmation is client-side only; no rate limiting on `/auth`. | VERIFIED-code | B1 (validators) / out of scope (rate limit) |
| S10 | `manage.py check --deploy` warnings: W008 `SECURE_SSL_REDIRECT` (mitigated: nginx redirects), **W012 `SESSION_COOKIE_SECURE`**, **W016 `CSRF_COOKIE_SECURE`** (live `csrftoken` cookie has no `Secure` flag, VERIFIED-live), W019 `X_FRAME_OPTIONS != DENY` (required `SAMEORIGIN` for the summary iframe). | VERIFIED-exec / live | B6 (prod config, owner) |
| S11 | `ALLOWED_HOSTS = ['*']`; `SECURE_PROXY_SSL_HEADER` trusts `X-Forwarded-Proto` (whether nginx overwrites it is UNKNOWN). | VERIFIED-code | B6 (owner) |
| S12 | No upload size/type limits. The whole file is read into memory, then base64'd into the session row (+33%). | VERIFIED-code | B2 (UI limit) + B6 (server limit, decision) |
| S13 | PGVector encryption: AES-CTR **without integrity** (no MAC/GCM); key is zero-padded or truncated to 32 bytes, so weak keys are silently accepted; embeddings and metadata are stored unencrypted. | VERIFIED-code | out of scope (document only) unless owner approves |
| S14 | DB URL fetched from Secrets Manager **twice per call** (every question, init and delete); password not URL-escaped. `DEBUG=True` with `USE_LOCAL_DB=False` splits Django (remote) from vectors (local). | VERIFIED-code | out of scope (note) |
| S15 | Background jobs are in-process threads with a process-local lock: lost on worker restart, and not coordinated across gunicorn workers. | VERIFIED-code (impact INFERRED) | out of scope (no new architecture); document in B6 |
| S16 | **Public git history:** OpenAI-key-shaped strings (`sk-…`) in two early files (`langchain summary to pdf`, `oneforall`, July 2024; values not printed here), plus two non-demo legal PDFs (`COMPLAINT FILED (…).PDF`, `DEPOSITION - Cribbs.Samuel 090823.full.pdf`; committed 2024-08/09, deleted 2024-10) that remain retrievable. The historical `.env` was empty. Whether the keys are revoked and whether the PDFs are public record are **UNKNOWN**. | VERIFIED-code (presence) | owner action now; history rewrite is a separate decision |
| S17 | Personal email addresses on `/contact`. | VERIFIED-code | B1 (content decision) |
| S18 | `?msg=` reflects arbitrary text into a banner (autoescaped, so content spoofing only). | VERIFIED-code | B1 (use Django messages or a fixed code→text map) |
| S19 | `requirements.txt`: fully unpinned; contains `frontend` and `tools`, which nothing imports. Likely artifacts of the PyPI `fitz` name collision (INFERRED). Not installed during the audit. | VERIFIED-code | B6 |
| S20 | Python 3.14 warnings: `SyntaxWarning: 'return' in a 'finally' block` (`summarizer.py:352`) and PyMuPDF "`fitz` API is deprecated". | VERIFIED-exec | B4 |

**Stale documentation and dead code (VERIFIED-code):**
- README says `PGVECTOR_ENCRYPTION_KEY` is "optional, defaults to development key"; it is required, with no default.
- README omits `DB_USER`/`DB_PASSWORD` (used for local DB), lists `DEBUG_MODE` twice, and doesn't mention Spanish, filters, DOCX or the transcript.
- Code comments are stale: "Chroma" in `deposition_chatbot.py`, gpt-3.5 pricing notes, and "(unchanged)" section headers in `views.py`.
- Dead code: `TEST_WITHOUT_AI`, `LOAD_DB_FROM_FOLDER`, `translate_to_spanish()`, `getChain()`, duplicate imports in `views.py`, `loading.gif`, jQuery and Bootstrap Icons.
- `.vscode/` and `.DS_Store` are tracked despite being in `.gitignore`.

---

## 15. Invariants future workstreams must preserve

### 15.1 Product invariants
1. All capabilities in §3 keep working: upload, en/es/both, none/include/exclude, extraction, OCR fallback, summary, chatbot, PDF, DOCX (restored), transcript, accounts, cleanup.
2. Using the app never requires an account (no new `login_required`) unless the owner decides otherwise.
3. "Clear data", logout and session expiry keep cancelling in-flight jobs (`race_check` contract: worker aborts when `db_len != -1`).
4. One active document per session. A new upload replaces the previous summary and chat (no document management).
5. Bear V2 must **never regress page identity once B4 lands**. Every summary heading maps to a real PDF page index.

### 15.2 Server/front-end contract (breaks silently if changed)
- **Form field names:** `file`, `lang` (`en|es|both`), `filterType` (`none|include|exclude`), `filterText` (repeatable), `question`, `username`, `password`, `csrfmiddlewaretoken`. The server ignores `password-confirm`, and the form doesn't send `email`.
- **Status protocol:** `GET /out/verify` → `200` + text = running; any non-200 (`418` done, `409` no session) = stop polling. `HEAD /out` → `200` = summary exists (home.js hint).
- **Session keys:** `db_len` (−1 running, 0 failure, >0 success, also feeds chatbot `k`), `status_msg`, `summary_pdf` (base64), `prompt_append` (list of `{role, content}`), `summary_lang`, `num_docs`, `num_questions`, `depo_pdf`.
- **Relative URLs in JS:** `fetch("chat")`, `fetch("ask")`, `fetch("out/verify")`, `href="out"`, `"out/" + fmt`, `href="transcript"`, iframe `src="/out"`. `home.js` builds the `/out` URL with `href.substring(0, len-4) + "out"`, which only works when the URL ends in `home` (it already breaks on `/home?msg=…`). Moving pages under a prefix or adding trailing slashes breaks these.
- **DOM hooks used by JS:** `#fileInput`, `#btnClicked`, `#loading`, `.output-url`, `.filter-type`, `.filter-disable`, `.filter-text`, `#addFilter`, `#removeFilter`, `#form`, `#clearForm`, `#logoutForm`, `.msg-container`, `#goToTopBtn`, `#status_msg`, `.body-container`, `.summary-container`, `.summary-placeholder`, `.chat-messages`, `.chat-question`/`#chat-question`, `#question`, `.chat-download-button`, `#summary-download-option`, `.summary-download-button`, `#create-btn`, `#warning-box`. `new.js` reads `.form-control` **by position** (`inputs[0..2]`).
- **Global inline-handler functions:** `validateForm`, `addFilterKeyword`, `removeFilterKeyword`, `clearConfirm`, `logoutConfirm`, `removeMessage`, `topFunction`, `changeDownloadFormat` (9 inline handlers). Adopting a CSP later requires removing these.
- **Body visibility gate:** `<body hidden>` plus `base.js` loaded first in `<body>`. If the new shell drops `base.js`, the page stays hidden. If it keeps it, local previews must use `localhost`/`127.0.0.1`.
- **Framing:** `X_FRAME_OPTIONS = 'SAMEORIGIN'` is required while the summary is shown in an iframe.
- **CSRF:** the chat form's `{% csrf_token %}` is load-bearing (FormData posts it). Every POST form needs it.
- **Chat keydown quirk:** the Enter handler doesn't `preventDefault`. Implicit form submission is currently blocked only because `form.reset()` empties a `required` field. Replace with an explicit `submit` handler plus `preventDefault` when touching chat (B3).

### 15.3 Summary-data invariants (for B4/B5)
- Nothing outside `summarizer.py` consumes the per-page summary strings (VERIFIED by grep). They exist only between `summarize_deposition` → `build_pdf_story`. The structured change is therefore contained to the summarizer, PDF builder and DOCX path.
- `summary_pdf` remains the persisted artifact that `/out/pdf`, `/out/docx`, `HEAD /out` and the iframe rely on.
- `db_len > 0` must still mean "success, chat enabled". If its meaning changes, decouple chatbot `k` explicitly.

---

## 16. Explicit out-of-scope list

Stripe/payments (incl. the `dj-stripe` branch), flashcards, quizzes, generalized learning engine, new SaaS architecture, document/matter management, multi-document sessions, new RAG architecture (re-ranking, hybrid search, agents), job queue/Celery/worker migration, major AWS migration or infra changes, React or any SPA rewrite, ORM models or migrations for summaries, unrelated refactors, git-history rewriting, speculative product features. In-process threading stays (documented as a known limitation).

---

## 17. Proposed staged implementation plan

Each phase is one PR off `main`, independently revertible, with no deploy implied. The B0 recommendation is that the owner first decides on the **hotfix candidates** in §19.3. They are small, user-facing bugs (DOCX 500, `www.` blank page, page identity) that could ship before or inside the phases below.

### B1 — Visual foundation
- Design tokens (CSS custom properties) for a restrained Bear/BEAR identity: color, type scale, spacing, radius, elevation, focus ring, motion (with `prefers-reduced-motion`).
- New global shell in `base.html`: header/nav with a visible brand mark and accessible mobile menu (fixes the dark-on-navy toggler), footer, a message component that replaces `<c>`, and consistent page titles ("BearSummarizer"/"BEAR"; naming is an owner decision).
- Keep the `base.js` gate but **add `www.bearsummarizer.com`** (or replace the allowlist with a documented approach approved by the owner). Fix the logout confirm (`type="button"`).
- Restyle login and create-account (shared auth CSS; fix duplicate label; show server `msg` errors properly). Clean up About/Contact content (owner to supply copy and decide on personal emails). 404 page.
- Remove unused jQuery and Bootstrap Icons only if B1 confirms nothing needs them. Decide whether to keep Bootstrap (recommended for B1: keep it to limit risk).
- Self-hosted or Google-Fonts typography. Add the missing image asset or remove the reference.

### B2 — Upload + processing experience
- Upload: drag-and-drop zone wrapping the existing `<input name="file">`; filename and size display; client-side PDF type and size check (limit value is an owner decision).
- Language as a segmented control (same `lang` values). Filter as a mode selector plus a chip-style keyword list, emitting the same repeated `filterText` fields. Accent-safe sanitization (allow Unicode letters) is a small server change that needs owner approval.
- Processing view: staged progress (Extracting → Preparing chat → Summarizing i/N → Building PDF) driven by the existing `status_msg`. Optionally add a structured status JSON endpoint *alongside* `/out/verify`, with no removal. Restrained state animation.
- **Error state:** show the failure message instead of the iframe when `db_len == 0`. Keep polling semantics.
- Fix the dead double-submit guard (check before popping) and the login-mid-job stall (S4), both contained in `views.summarize`.

### B3 — Output / document workspace
- Responsive two-pane workspace (summary | chat) that stacks on mobile.
- Summary pane: keep the iframe on desktop. On mobile, provide a download-first card (or HTML rendering once B4's structured data exists). Clear PDF / DOCX / transcript download buttons that replace the `<select>`.
- **Restore DOCX download** (`views._serve_output`: split `convert()`/`close()`) if it was not already hotfixed.
- Chat: message list with empty state, explicit send button, `submit` handler with `preventDefault`, pending/typing state, error bubble, transcript button visible when history exists. Keep `/chat`, `/ask`, `/transcript` and FormData+CSRF.
- Sequenced **after B2**, because both touch `output.html`/`output.js`.

### B4 — Contained LLM modernization
1. **Page model:** extraction returns `PageText(pdf_page: int, text: str, method: native|ocr|fallback, usable: bool)` for every page. Remove the silent `[2:]` and the `<150` second skip. Replace them with explicit, recorded statuses (`skipped_no_text`, `skipped_front_matter` if the owner keeps a cover-skip rule).
2. **Structured output:** per-page JSON validated against a schema, e.g. `{"bullets": [str, 1..3], "status": "summarized" | "no_relevant_content", "uncertain": bool}`. The **pipeline**, not the model, attaches `pdf_page`. Use the provider's structured-output/JSON-schema mode. On validation failure, retry once, then record `status: failed`. Never emit failure text as content.
3. **Prompt rewrite:** legal-summary system prompt with explicit rules. Preserve names, dates, times, amounts, exhibit numbers and quoted phrases verbatim. Attribute statements (witness vs. examining attorney). Mark uncertainty. No legal conclusions, no inference beyond the page. Treat document text as data (delimited). Remove the "entire document" claim. Filter rules map to `no_relevant_content`.
4. **Temperature:** evaluate 0–0.3 against the current 1. Pick per model (some models don't accept temperature).
5. **Neighbor context (evaluate, default off):** optionally pass the tail of page *n−1* and head of page *n+1*, clearly marked "context only — do not summarize". Adopt only if the eval shows better continuity without misattribution.
6. **Translation:** translate the bullet **array** (JSON in/out, same length enforced) with the same summary model or an evaluated replacement for the hard-coded legacy model. Keep "both".
7. **Model evaluation** (current `GPT_MODEL` as baseline vs. 1–2 candidates chosen at B4 time): a small fixture set of synthetic or public-domain depositions (**not** the PDFs in git history, **no** client data). Score:
   - page-identity correctness (deterministic, must be 100%);
   - schema validity (100% after retry);
   - entity/date/number preservation (automatic string match against source);
   - unsupported-claim rate (human review of a sample);
   - Spanish fidelity (spot-check);
   - latency and cost per 100 pages.
8. Lazy LLM client construction so the summarizer imports (and tests run) without `OPENAI_KEY`. Fix the `return`-in-`finally` warning; use `import pymupdf`.
9. Keep `create_summary`'s external contract (signature, return value, session keys, `race_check` points). Keep a v1 code path behind a setting for one release so rollback is a config change.
10. *(Optional, owner decision)* add `{"pdf_page": n}` metadata to chatbot chunks for page citations. This is the same RAG design with only metadata added.

### B5 — Generated PDF modernization
- New renderer consuming B4's structured pages:
  - title block (BearSummarizer, generated date, language, filter mode and keywords, page count; source filename only if the owner approves storing it);
  - per-page heading with the real PDF page ("PDF page 14");
  - deterministic bullets via `ListFlowable` or `bulletText` + `leftIndent`/`bulletIndent` (renderer-owned glyph, hanging indent, wrapping, spacing);
  - bilingual blocks with explicit EN/ES labels;
  - distinct styling for `no_relevant_content` / `skipped` / `failed`;
  - running footer with "Page x of y" and an AI-generated/verify-against-transcript notice;
  - PDF metadata.
- Embed a Unicode TTF (license-compatible, e.g. a Noto/Source family) so any character the model emits renders. Requires adding the font file to the repo; confirm prod packaging.
- Escape all model text before it reaches ReportLab markup.
- Validate the **DOCX** path still produces acceptable output from the new PDF (headings as separate paragraphs, bullets intact). If pdf2docx output is poor, raise native DOCX (python-docx is already installed transitively) as an owner decision, not a default.

### B6 — Cleanup + integrated/live verification
- README and env documentation; pin `requirements.txt` to the versions verified in prod (needs a prod `pip freeze`); remove `frontend`/`tools` after confirming prod doesn't need them; remove dead code and assets; untrack `.vscode`/`.DS_Store` (owner OK).
- Data-lifecycle fixes, **each individually owner-approved**: stop storing `depo_pdf` (it is never read) or clear it on Clear; Clear also deletes the vector collection; fix the `trim()` cleanup SQL (`name LIKE 'collection_%'` + `substr`); fix double encryption on key cycle (copy ciphertext with the base-class insert, or decrypt before re-adding).
- Prod config recommendations for the owner (do not change prod directly): `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, explicit `ALLOWED_HOSTS`, nginx overwriting `X-Forwarded-Proto`, upload size limit.
- Full integrated regression on a staging-equivalent environment, then owner-run live verification.

---

## 18. Recommended test / acceptance criteria per phase

General rule: no phase merges with `manage.py check` failing. From B4 on, `manage.py test` (Django `SimpleTestCase`, no DB/OpenAI) must pass. Each phase records manual QA on desktop (≥1280px) and mobile (375px) using `localhost`/`127.0.0.1` (because of the `base.js` gate).

| Phase | Acceptance criteria |
|---|---|
| **B1** | All 8 pages render with the new shell at 375/768/1280px with **no horizontal scroll** (`documentElement.scrollWidth == innerWidth`). Mobile menu opens/closes and is visible. Logo visible. `www.bearsummarizer.com` allowed by the gate. Logout "Cancel" does not log out. Login, create-account and logout still work end to end. `?msg=` errors are still displayed. 404 page uses the shell. No 404s for static assets. Lighthouse accessibility ≥ 90 on home/login. Keyboard focus visible. |
| **B2** | Upload with each `lang` × `filterType` combination posts identical field names/values (compare request payloads before/after). Keywords add/remove; disabled when "No filter". Processing view shows every `status_msg` stage. A forced failure (e.g. non-PDF upload) shows an error, not an empty iframe. Double-submit in the same session runs only one job. "Clear data" mid-job still cancels. `HEAD /out` hint still appears. No horizontal scroll at 375px. |
| **B3** | Summary visible on desktop. Mobile has a usable summary path. **PDF, DOCX and transcript downloads each return 200 with the correct content-type.** Chat: send by button and Enter, no page reload, history restored on reload, transcript button visible when history exists, error bubble on 409/500. CSRF enforced. Clear data from the workspace still works. |
| **B4** | Unit tests on synthetic PDFs (see Appendix A pattern): every page gets a record with the correct `pdf_page`, including blanks, short pages and leading pages; **no silent drops**. Schema-invalid model output → retry → `failed` status, never raw text. Prompt contains no "entire document" claim. Filter modes map to `no_relevant_content`. ES output is the same length as EN bullets. Imports work without `OPENAI_KEY`. Eval report committed (metrics in §17 B4.7) comparing baseline vs. candidates, with the chosen model, temperature and neighbor-context decision justified. Runtime per 100 pages not worse than baseline by more than an agreed margin. |
| **B5** | Golden-file style checks: headings equal real PDF pages; bullets have a hanging indent (wrapped line x-offset == text x-offset); title block, footer and page numbers present; EN/ES labelled; non-Latin-1 test string renders with the embedded font (no `II`/tofu); model text with `<`, `&`, `<tag>` appears literally. DOCX from the new PDF: headings in their own paragraphs, all bullets present, accents preserved. Visual review of 1-, 10- and 100-page outputs in en/es/both. |
| **B6** | README matches the actual env vars. `pip install -r requirements.txt` on the prod Python version succeeds with pins. Cleanup SQL tested against a real Postgres with session keys starting `c/e/i/l/n/o/t` (collections survive). Login after summarizing → chatbot still answers from plaintext context. Clear removes `depo_pdf` and the collection (if approved). `check --deploy` warnings either resolved in prod config or explicitly accepted by the owner. Full feature matrix (§3) re-run on staging, then live smoke test by the owner. |

---

## 19. Risks and rollback boundaries

### 19.1 Risks
- **Silent contract breaks in templates** (relative URLs, DOM hooks, `body hidden` gate, CSRF in FormData). Mitigation: the §15.2 checklist in every UI PR, plus request-payload comparison in B2.
- **B2/B3 overlap** on `output.html`/`output.js`. Mitigation: run them sequentially, or have B2 extract the processing UI into its own partial and JS file first.
- **B4 output drift:** structured output changes summary content and length. Mitigation: eval set, a v1 fallback setting, and an owner sign-off on eval results.
- **Model availability/cost:** the production `GPT_MODEL` value is unknown, and the hard-coded translator is a legacy model. Mitigation: confirm both before B4 begins.
- **Fonts/packaging (B5):** an embedded TTF must ship with static/app files. pdf2docx behavior may change with layout changes.
- **Prod environment unknowns** (Python version, unpinned deps, Tesseract, nginx limits). Mitigation: owner captures them before B4/B5/B6.
- **Data-lifecycle fixes** touch the session engine and live data deletion. They need tests against real Postgres and an explicit owner go-ahead.
- **Mobile PDF iframe** limits remain until an HTML summary view exists (B3/B4 interplay).

### 19.2 Rollback boundaries
- B1/B2/B3: template/CSS/JS-only plus small view fixes. Revert the PR; no data migration.
- B4: keep the v1 pipeline selectable by setting for one release. Revert by setting, then by PR. Session keys stay compatible (`summary_pdf`, `db_len`).
- B5: the renderer is a separate module behind the same `write_summaries_to_pdf` call site. Revert the PR.
- B6: each data-lifecycle fix is its own commit so it can be reverted individually. Prod config changes are owner-applied and owner-reverted.
- No phase introduces DB schema changes. That keeps every rollback a code-only operation.

### 19.3 Owner decisions needed
1. **Hotfix first?** DOCX `.close()` 500, the `www.` blank page, and logout-cancel are one-line fixes affecting live users today. Page identity is larger, but it is a correctness issue in legal output.
2. Product naming: "BearSummarizer", "BEAR" or "Deposum"?
3. Cover-page handling: summarize all pages, or a detected/labelled front-matter skip? (The current blind `[2:]` should go.)
4. PDF page index vs. printed transcript page:line (§12.4).
5. Store the original filename for the PDF title block?
6. Accent-safe keyword sanitization.
7. Chatbot page citations (chunk metadata).
8. Stop storing `depo_pdf`; make Clear remove it and the vector collection.
9. Fix the S2/S3 data bugs as a hotfix or in B6.
10. Upload size limit value.
11. Personal emails on Contact.
12. Rotate or confirm revocation of the historical OpenAI key strings; decide on the legal PDFs in public history.

---

## 20. Files likely owned by each phase

| Phase | Primary files | Shared/touch-only |
|---|---|---|
| **B1** | `server/templates/base.html`, `login.html`, `new.html`, `about.html`, `contact.html`, `404.html`; `server/static/styles/base.css` (+ new `tokens.css`/`auth.css`), `login.css`, `new.css`; `server/static/javascript/base.js`, `new.js`; `server/static/images/*` (brand assets) | `server/views.py` (`delete_account` guard, `create_account` validators only) |
| **B2** | `server/templates/home.html`, `server/static/styles/home.css`, `server/static/javascript/home.js`; processing portion of `output.html`/`output.js` (prefer extracting to `_processing.html` + `processing.js`) | `server/views.py` (`summarize`, `verify`, optional status endpoint); `server/urls.py` (additive only) |
| **B3** | `server/templates/output.html`, `chat_message.html`, `server/static/styles/output.css`, `server/static/javascript/output.js` | `server/views.py` (`_serve_output`, `chat_html`, `transcript`) |
| **B4** | `server/summary/summarizer.py` (extraction, prompts, LLM, translation, orchestrator); new `server/summary/schema.py`, `server/summary/prompts.py`; new `server/tests/` + fixtures; eval script/report (e.g. `tools/eval/`, outside the runtime path) | `server/summary/deposition_chatbot.py` (only lazy client / optional chunk metadata); `server/settings.py` (new non-secret settings only) |
| **B5** | new `server/summary/pdf_report.py` (renderer); `server/static/fonts/*` or `server/summary/fonts/*`; tests | `summarizer.py` call site of `write_summaries_to_pdf` (one-line switch); `views.py` DOCX path if needed |
| **B6** | `README.md`, `requirements.txt`, `.gitignore`, `server/vector_db_session.py`, `server/PGVector_encrypt/vectorstores.py` (only if S3 fix approved), `server/hourly.py`, `server/settings.py` (only non-prod-breaking, owner-approved) | everything (cleanup) — run last, alone |

**Concurrency guidance:** B1 can start immediately. B4 can run in parallel with B1–B3 (disjoint files except `views.py`, which B4 should not need). B5 depends on B4's `schema.py`: land a schema-only commit early so B5 can start. B2 → B3 run sequentially. B6 runs last.

---

## 21. Baseline checks run in B0

| Check | Result |
|---|---|
| `git status` / branch / SHAs / `worktree list` | clean; `bearv2/b0-baseline` @ `fc61efc…`; `origin/main` @ `fc61efc…` (matches expected) |
| `pip install -r requirements.txt` (minus `frontend`, `tools`) on CPython 3.14.7 | succeeds (unpinned → latest versions listed in §2) |
| `manage.py check` with no env | **fails**: `DEBUG_MODE not found` |
| …with `DEBUG_MODE`, `USE_LOCAL_DB` | fails: `STATIC_ROOT not found` |
| …+ `STATIC_ROOT` | fails: `OPENAI_KEY not found` (surfaced as a misleading chained "Error loading psycopg2 or psycopg module") |
| …+ placeholder `OPENAI_KEY`, `GPT_MODEL` | **passes: "System check identified no issues"** (warnings: `fitz` deprecation; `'return' in a 'finally' block`) |
| `manage.py check --deploy` (DEBUG off, placeholder SECRET_KEY) | 4 warnings: W008, W012, W016, W019 (see S10) |
| `manage.py test` | **"Ran 0 tests — NO TESTS RAN"**; no test suite exists on `main` (an outdated `server/tests/testing_summarizer` exists only on the stale `deposum-with_unit_tests` branch and targets removed functions) |
| Runtime with real DB/OpenAI/AWS | **not run, blocked by design**: no local Postgres/pgvector, no Tesseract, no OpenAI key, no AWS profile `db_access`. No workaround was attempted against real services. |
| Pipeline harness (Appendix A) | page-identity, prompt-context, ReportLab, pdf2docx and view-level behaviors reproduced as documented |
| Live read-only probes | headers, 404, redirects, static-asset parity, `www.` blank page, mobile layout measurements |

---

## Appendix A — How the audit reproductions were run (for B4/B5 test design)

The scripts were kept outside the repository, in a temp venv, and are not committed. The pattern can be reused for real tests:

1. Set placeholder env vars (`DEBUG_MODE=True`, `USE_LOCAL_DB=True`, `STATIC_ROOT=/tmp/static`, `OPENAI_KEY=dummy-not-a-key`, `GPT_MODEL=gpt-4o-mini`), then `django.setup()`. No network calls happen at import.
2. Monkeypatch `summarizer.session_engine` with an in-memory dict-backed `SessionStore`, `update_status_msg` with a no-op, `race_check` with `lambda sid: False`, `cb.initBot` with a recorder, and `llm`/`translator_llm` with fakes. The fakes echo back which page markers appeared in their input.
3. Build a synthetic PDF with PyMuPDF (`doc.new_page()` + `insert_textbox` inside the margins), with a unique marker per page. **Avoid the words "page", "exhibit", "affidavit" and "witness" in markers**, since they trip `is_page_valid`.
4. Call `create_summary(pdf_bytes, sid, target_lang=...)`, decode `summary_pdf`, and map each heading to the marker it actually summarized.
5. For DOCX, use `cv = Converter(stream=...); cv.convert(buf); cv.close()`. The production chained form raises.
6. View-level checks used `RequestFactory` with a dict-based fake session and a patched `views.Thread`.

Encoding note: Windows console output mangles non-ASCII. Print with `ascii()`, or set `PYTHONIOENCODING=utf-8`, before concluding that characters were lost. An apparent "accent loss" during this audit turned out to be exactly this console artifact.
