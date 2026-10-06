# server/views.py
from threading import Thread
from importlib import import_module
from decouple import config
from django.shortcuts import render, redirect
from pdf2docx import Converter
from server.util import session_lock
from . import util
import io, base64, re, time, uuid

from django.http import (
    HttpResponse, HttpResponseNotAllowed, HttpResponseServerError,
    HttpResponseBadRequest, HttpRequest, HttpResponseRedirect
)
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
import django.template.loader as ld
from importlib import import_module
from pdf2docx import Converter
from decouple import config

from server.summary.summarizer import create_summary, current_job_session
from server.summary.deposition_chatbot import askQuestion
from server.util import session_lock
from . import util

# --- session engine ------------------------------------------------------
session_engine = import_module(settings.SESSION_ENGINE)

# ---------------------------------------------------------------------------
#  Summary job state (shared by home, output and /out/verify)
# ---------------------------------------------------------------------------
# a running job that has reported no progress for this long is shown as
# stalled and the user is offered a way to cancel it
JOB_STALL_SECONDS = 15 * 60

# session keys describing the current job; removed when a job is cancelled.
# job_id is the upload's opaque token: a worker may only write to the session
# while session["job_id"] is still its own (see summarizer.current_job_session)
JOB_KEYS = ("db_len", "status_msg", "job_started", "status_at", "job_id", "chat_job_id")

def job_status(session):
    """
    Presentation state of the session's summary job as (state, reason):
    'running' / 'stalled' while db_len == -1, 'ready' once a summary is
    stored, 'failed' (reason 'no-text' or 'error') for db_len 0 / -2, and
    'none' otherwise. Reasons are fixed codes, never exception text.
    """
    db_len = session.get("db_len")
    if db_len == -1:
        last = max(session.get("job_started") or 0, session.get("status_at") or 0)
        # no timestamp at all: the job was started before stall tracking existed
        if not last or time.time() - last > JOB_STALL_SECONDS:
            return "stalled", None
        return "running", None
    if "summary_pdf" in session:
        return "ready", None
    if db_len == 0:
        # create_summary marks crashes with "❌ Error"; a clean run that
        # found no readable page also ends with db_len 0
        crashed = str(session.get("status_msg", "")).startswith("❌")
        return "failed", "error" if crashed else "no-text"
    if db_len == -2:
        return "failed", "error"
    return "none", None

def chat_available(session) -> bool:
    """
    Whether /ask would query the chatbot index for this session: a finished
    summary (db_len > 0) and, for token-aware jobs, chat_job_id proving the
    active job built its own index. Mirrors the checks in ask(); used only
    to present the chat panel, never to authorize a question.
    """
    db_len = session.get("db_len")
    if not isinstance(db_len, int) or db_len <= 0:
        return False
    if session.get("job_id") and session.get("chat_job_id") != session["job_id"]:
        return False
    return True

def _cancel_running_job(session) -> bool:
    """
    Drop an in-flight job (and its token) from the session; its worker aborts
    at the next race_check and its writes are refused. Saved under the lock
    so a worker can't check its token and write in between.
    """
    with session_lock:
        if session.get("db_len") != -1:
            return False
        for k in JOB_KEYS + ("depo_pdf",):
            session.pop(k, None)
        session.save()
    return True

def _looks_like_pdf(data: bytes) -> bool:
    # PDF readers accept the header anywhere in the first 1024 bytes
    return b"%PDF-" in data[:1024]

class _StartWorkerAfterResponse(HttpResponseRedirect):
    """
    Redirect that starts the summary worker only when the response is closed,
    i.e. after SessionMiddleware has saved this request's session. Starting
    it earlier let that save overwrite a job that failed quickly (such as an
    unreadable PDF) with db_len == -1, leaving the session stuck as running.
    """
    def __init__(self, url, start):
        super().__init__(url)
        self._start = start

    def close(self):
        try:
            super().close()
        finally:
            start, self._start = self._start, None
            if start:
                start()

# ---------------------------------------------------------------------------
#  Summarize view  –  handles file upload, language choice, and starts worker
# ---------------------------------------------------------------------------
def summarize(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # 1) ------- validate input before touching the session --------------------
    if not (request.FILES and request.FILES.get('file')):
        return HttpResponseBadRequest(
            "Request must include a file field named 'file'."
        )

    lang_choice = request.POST.get('lang', 'en').lower()        # en | es | both
    if lang_choice not in ('en', 'es', 'both'):
        return HttpResponseBadRequest("Invalid lang value; use en, es, or both.")

    filter_type = request.POST.get("filterType")
    if filter_type not in ["none", "include", "exclude"]:
        return HttpResponseBadRequest(f"Malformed request, invalid value \"{filter_type}\" for filterType")

    filter_keywords = []
    if filter_type != "none":
        for text in request.POST.getlist("filterText"):
            # Sanitize input by removing characters that aren't a-z, A-Z, 0-9, or hyphen
            filter_keywords.append(re.sub(r'[^a-zA-Z0-9- ]', '', text))

    pdf_bytes = request.FILES['file'].read()
    if not _looks_like_pdf(pdf_bytes):
        return redirect(f"{reverse(home)}?msg=That file isn't a PDF. Choose a PDF file and try again.")

    # 2) ------- guarantee a session id ----------------------------------------
    if not request.session.session_key:
        request.session.save()
    sid = request.session.session_key

    with session_lock:
        # 3) ------- prevent accidental double-click ----------------------------
        # must run before the stale-key wipe below, which removes db_len. The
        # stored session is re-read so a concurrent request that loaded the
        # session before the first upload saved it still sees the running job.
        stored = session_engine.SessionStore(sid)
        if request.session.get('db_len') == -1 or stored.get('db_len') == -1:
            state, _ = job_status(stored if stored.get('db_len') == -1 else request.session)
            if state == "stalled":
                msg = "Your previous summary stopped responding. Cancel it to upload a new document."
            else:
                msg = "Summary in progress, please wait."
            return redirect(f"{reverse(output)}?msg={msg}")

        # remove leftovers from an earlier run so counters start at 0
        for k in JOB_KEYS + ("summary_pdf", "num_docs", "chat_history", "prompt_append"):
            request.session.pop(k, None)
        request.session.modified = True            # flag change before save

        # 4) ------- stash file & flags in session -----------------------------
        job_id = uuid.uuid4().hex         # new token: older workers can no longer write
        request.session.update({
            "db_len": -1,                 # in-progress marker
            "job_id": job_id,
            "job_started": int(time.time()),
            "prompt_append": [],
            "summary_lang": lang_choice,
            "depo_pdf": base64.b64encode(pdf_bytes).decode(),
        })
        request.session.save()

    # 5) ------- background worker --------------------------------------------
    def worker(sess_id, lang, pdf_data, filter_keywords, filter_type, job_id):
        print(f"▶ worker start  sid={sess_id}  lang={lang}  bytes={len(pdf_data)}")
        try:
            page_cnt = create_summary(pdf_data, sess_id, target_lang=lang, filter_keywords=filter_keywords,
                                      filter_exclude=filter_type, job_id=job_id)
        except Exception as e:
            import traceback, sys
            traceback.print_exc(file=sys.stdout)
            with session_lock:
                s = current_job_session(sess_id, job_id)   # None once cancelled/replaced
                if s is not None:
                    s.update({
                        "db_len": -2,
                        "status_msg": f"Error: {e}",
                    })
                    s.save()
            return

        with session_lock:
            s = current_job_session(sess_id, job_id)       # None once cancelled/replaced
            if s is not None:
                #check if already complete by another thread
                if (s.get("db_len", 0) == -1 and page_cnt != -1):
                    s["db_len"] = page_cnt
                    s["num_docs"] = s.get("num_docs", 0) + 1
                    s.save()

    job = Thread(target=worker, args=[sid, lang_choice, pdf_bytes, filter_keywords, filter_type == "exclude", job_id])
    return _StartWorkerAfterResponse(reverse(output), job.start)

# ---------------------------------------------------------------------------
#  Chatbot – ask a question
# ---------------------------------------------------------------------------
def ask(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    s = request.session
    if not s.session_key:
        s.save()
    sid = s.session_key

    data = request.POST
    if not data.get('question'):
        return HttpResponseBadRequest("Please enter a question.")

    if (not s.get('db_len')) or s['db_len'] <= 0:
        return HttpResponse("No file summarized", status=409)

    # token-aware summaries may only chat against their own index; sessions
    # from before job tokens (no job_id) keep the old behavior
    if s.get('job_id') and s.get('chat_job_id') != s['job_id']:
        return HttpResponse("Chat isn't available for this summary. Upload the PDF again to use chat.", status=409)

    response = askQuestion(data['question'], sid, s['prompt_append'], s['db_len'])
    if response is None:
        return HttpResponseServerError("OpenAI call failed, please try again later.")

    s['prompt_append'] = response[1]
    s['num_questions'] = s.get('num_questions', 0) + 1
    return HttpResponse(response[0])

def chat_html(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    log = request.session.get('prompt_append', [])
    rendered = "".join(
        ld.render_to_string('chat_message.html',
                            {'outgoing': line['role'] == 'user',
                             'message':  line['content']})
        for line in log
    )
    return HttpResponse(rendered)

def transcript(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    raw = request.session.get('prompt_append', [])
    dialog = ""
    for line in raw:
        prefix = "Q" if line['role'] == 'user' else "A" if line['role'] == 'assistant' else "?"
        dialog += f"{prefix}: {line['content']}\n"

    response = HttpResponse(dialog, content_type='text/plain')
    response['Content-Disposition'] = 'filename=deposum_chat_transcript.txt'
    return response

# ---------------------------------------------------------------------------
#  Session helpers (debug)
# ---------------------------------------------------------------------------
@csrf_exempt
def session(request: HttpRequest):
    if not request.session.session_key:
        request.session.save()
    print(request.session.session_key)
    print(request.session.items())
    return HttpResponse("done")

@csrf_exempt
def cyclekey(request: HttpRequest):
    request.session.cycle_key()
    return HttpResponse("done")

# ---------------------------------------------------------------------------
#  Clear – button on output.html really wipes cached data
# ---------------------------------------------------------------------------
def clear(request: HttpRequest):
    if request.method == "POST":
        # under the lock, and saved there, so a worker can't check its job
        # token and write between this cancel and the response
        with session_lock:
            for key in [
                "summary_pdf", "status_msg", "db_len",
                "job_started", "status_at", "job_id", "chat_job_id",
                "num_docs", "chat_history", "prompt_append",
                "depo_pdf",
            ]:
                request.session.pop(key, None)
            request.session.modified = True
            if request.session.session_key:
                request.session.save()
    return redirect("/home")

# ---------------------------------------------------------------------------
#  Verify – polled by processing.js once per second
# ---------------------------------------------------------------------------
def verify(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    sid = request.session.session_key
    if not sid:
        response = HttpResponse("No active task", status=409)
        response["X-Job-State"] = "none"
        return response

    s = session_engine.SessionStore(sid)
    db_len     = s.get('db_len', 0)
    status_msg = s.get('status_msg', "Working...")

    if db_len == -1:
        response = HttpResponse(status_msg)          # 200 – still running
    else:
        response = HttpResponse("done", status=418)  # stop polling

    # additive: lets the page tell success, failure and "nothing to show"
    # apart without changing the status codes or body above
    state, reason = job_status(s)
    response["X-Job-State"] = state
    if reason:
        response["X-Job-Reason"] = reason
    return response

# ---------------------------------------------------------------------------
#  Helper – serve PDF or DOCX
# ---------------------------------------------------------------------------
def _serve_output(request: HttpRequest, type: str):
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])

    if not request.session.session_key:
        return HttpResponse("No input file found, summarize a file first", status=409)

    if request.method == 'HEAD':
        if 'summary_pdf' in request.session:
            return HttpResponse()
        elif request.session.get('db_len') in (-1, None):
            return HttpResponse(status=409)          # still running
        elif request.session['db_len'] == 0:
            return HttpResponseBadRequest()
        else:
            return HttpResponseServerError()

    # GET →
    try:
        if type == "pdf":
            pdf_data = base64.b64decode(request.session['summary_pdf'])
            response = HttpResponse(pdf_data, content_type='application/pdf')
            response['Content-Disposition'] = 'filename=deposition_summary.pdf'
            return response

        if type == "docx":
            pdf_data   = base64.b64decode(request.session['summary_pdf'])
            pdf_buffer = io.BytesIO(pdf_data)
            docx_buffer = io.BytesIO()
            # convert() returns None, so close the converter separately
            converter = Converter(stream=pdf_buffer)
            try:
                converter.convert(docx_buffer)
            finally:
                converter.close()
            docx_buffer.seek(0)
            response = HttpResponse(
                docx_buffer.read(),
                content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
            response['Content-Disposition'] = 'filename=deposition_summary.docx'
            return response

        raise ValueError("unsupported")
    except KeyError:
        return HttpResponse("No summary available", status=409)

def out(request: HttpRequest):
    return _serve_output(request, "pdf")

def out_docx(request: HttpRequest):
    return _serve_output(request, "docx")

# ---------------------------------------------------------------------------
#  User / account helpers
# ---------------------------------------------------------------------------
def create_account(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    user = request.POST.get('username')
    email = request.POST.get('email')
    password = request.POST.get('password')
    if not user or not password:
        return redirect(f"{reverse(new_account)}?msg=Please enter a username and password.")
    if User.objects.filter(username=user).exists():
        return redirect(f"{reverse(new_account)}?msg=Username is already taken.")
    auth_user = User.objects.create_user(user, email or None, password)
    return _login_and_redirect(request, auth_user)

def auth(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    user = request.POST.get('username')
    password = request.POST.get('password')
    auth_user = authenticate(username=user, password=password)
    if auth_user is not None:
        return _login_and_redirect(request, auth_user)
    return redirect(f"{settings.LOGIN_URL}?msg=Incorrect username/password.")

def _login_and_redirect(request: HttpRequest, auth_user):
    # login() cycles the session key: a running worker keeps the old key and
    # aborts, while the new session would inherit db_len == -1 and look busy
    # until it expires. Cancel the job explicitly and say so instead.
    cancelled = _cancel_running_job(request.session)
    login(request, auth_user)
    if cancelled:
        return redirect(f"{reverse(home)}?msg=Signing in cancelled the summary that was in progress. Please upload your PDF again.")
    return redirect("/home")

def logout_user(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    logout(request)
    return redirect(settings.LOGIN_URL)

def delete_account(request: HttpRequest):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    user = request.user
    # anonymous users have no account to delete; leave their session alone
    if user is None or not user.is_authenticated:
        return redirect(f"{settings.LOGIN_URL}?msg=You must be logged in to delete an account.")
    logout(request)
    user.delete()
    return redirect(settings.LOGIN_URL)

# ---------------------------------------------------------------------------
#  Template views
# ---------------------------------------------------------------------------
def _job_context(request: HttpRequest):
    state, reason = job_status(request.session)
    context = util.params_to_dict(request, 'msg')
    context.update(job_state=state, job_reason=reason,
                   stall_minutes=JOB_STALL_SECONDS // 60)
    return context

def home(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "home.html", _job_context(request))

def about(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "about.html")

def contact(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "contact.html")

SUMMARY_LANG_LABELS = {"en": "English", "es": "Spanish", "both": "English and Spanish"}

def output(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    context = _job_context(request)
    if context["job_state"] == "ready":
        # completed-summary workspace; job tokens never reach the template
        s = request.session
        db_len = s.get("db_len")
        context.update(
            chat_state="ready" if chat_available(s) else "unavailable",
            summary_pages=db_len if isinstance(db_len, int) and db_len > 0 else None,
            summary_lang_label=SUMMARY_LANG_LABELS.get(s.get("summary_lang")),
            has_chat_history=bool(s.get("prompt_append")),
        )
    return render(request, "output.html", context)

def login_page(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    if request.user is None or not request.user.is_authenticated:
        return render(request, "login.html", util.params_to_dict(request, 'msg'))
    return redirect(home)

def new_account(request: HttpRequest):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "new.html", util.params_to_dict(request, 'msg'))
