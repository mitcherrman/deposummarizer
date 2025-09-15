# server/views.py
from threading import Thread
from importlib import import_module
from decouple import config
from django.shortcuts import render, redirect
from pdf2docx import Converter
from server.util import session_lock
from . import util
import io, base64, re

from django.http import (
    HttpResponse, HttpResponseNotAllowed, HttpResponseServerError,
    HttpResponseBadRequest
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

from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
from server.util import session_lock
from . import util

# --- session engine ------------------------------------------------------
session_engine = import_module(settings.SESSION_ENGINE)

# ---------------------------------------------------------------------------
#  Summarize view  –  handles file upload, language choice, and starts worker
# ---------------------------------------------------------------------------
def summarize(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # 1) ------- validate input ------------------------------------------------
    if not (request.FILES and request.FILES.get('file')):
        return HttpResponseBadRequest(
            "Request must include a file field named 'file'."
        )

    lang_choice = request.POST.get('lang', 'en').lower()        # en | es | both
    if lang_choice not in ('en', 'es', 'both'):
        return HttpResponseBadRequest("Invalid lang value; use en, es, or both.")

    # 2) ------- guarantee a session id & wipe any stale keys ------------------
    if not request.session.session_key:
        request.session.save()
    sid = request.session.session_key

    # remove leftovers from an earlier run so counters start at 0
    for k in ("summary_pdf", "status_msg", "db_len",
              "num_docs", "chat_history", "prompt_append"):
        request.session.pop(k, None)
    request.session.modified = True            # flag change before save

	#get request data
    filter_type = request.POST.get("filterType")
    if filter_type not in ["none", "include", "exclude"]:
        return HttpResponseBadRequest(f"Malformed request, invalid value \"{filter_type}\" for filterType")
    
    filter_text = None
    if filter_type != "none":
        filter_text = request.POST.get("filterText")
        # Sanitize input by removing characters that aren't a-z, A-Z, 0-9, comma, or hyphen
        filter_text = re.sub(r'[^a-zA-Z0-9,-]', '', filter_text)

    # 3) ------- prevent accidental double-click --------------------------------
    if request.session.get('db_len') == -1:
        return redirect(f"{reverse(output)}?msg=Summary in progress, please wait.")

    # 4) ------- stash file & flags in session ---------------------------------
    pdf_bytes = request.FILES['file'].read()
    request.session.update({
        "db_len": -1,                 # in-progress marker
        "prompt_append": [],
        "summary_lang": lang_choice,
        "depo_pdf": base64.b64encode(pdf_bytes).decode(),
    })
    request.session.save()

    # 5) ------- background worker --------------------------------------------
    def worker(sess_id, lang, pdf_data, filter_text, filter_type):
        print(f"▶ worker start  sid={sess_id}  lang={lang}  bytes={len(pdf_data)}")
        try:
            page_cnt = create_summary(pdf_data, sess_id, target_lang=lang, filter_keywords=filter_text, filter_exclude=filter_type)
        except Exception as e:
            import traceback, sys
            traceback.print_exc(file=sys.stdout)
            with session_lock:
                s = session_engine.SessionStore(sess_id)
                if s.exists(sess_id):
                    s.update({
                        "db_len": -2,
                        "status_msg": f"Error: {e}",
                    })
                    s.save()
            return

        with session_lock:
            s = session_engine.SessionStore(sess_id)
            if s.exists(sess_id):
                s["db_len"] = page_cnt
                s["num_docs"] = s.get("num_docs", 0) + 1
                s.save()

    Thread(target=worker, args=[sid, lang_choice, pdf_bytes, filter_text, filter_type == "exclude"]).start()
    return redirect(output)

# ---------------------------------------------------------------------------
#  Chatbot – ask a question
# ---------------------------------------------------------------------------
def ask(request):
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

    response = askQuestion(data['question'], sid, s['prompt_append'], s['db_len'])
    if response is None:
        return HttpResponseServerError("OpenAI call failed, please try again later.")

    s['prompt_append'] = response[1]
    s['num_questions'] = s.get('num_questions', 0) + 1
    return HttpResponse(response[0])

def chat_html(request):
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

def transcript(request):
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
def session(request):
    if not request.session.session_key:
        request.session.save()
    print(request.session.session_key)
    print(request.session.items())
    return HttpResponse("done")

@csrf_exempt
def cyclekey(request):
    request.session.cycle_key()
    return HttpResponse("done")

# ---------------------------------------------------------------------------
#  Clear – button on output.html really wipes cached data
# ---------------------------------------------------------------------------
@csrf_exempt
def clear(request):
    if request.method == "POST":
        for key in [
            "summary_pdf", "status_msg", "db_len",
            "num_docs", "chat_history", "prompt_append"
        ]:
            request.session.pop(key, None)
        request.session.modified = True
    return redirect("/home")

# ---------------------------------------------------------------------------
#  Verify – polled by output.js once per second
# ---------------------------------------------------------------------------
def verify(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    sid = request.session.session_key
    if not sid:
        return HttpResponse("No active task", status=409)

    s = session_engine.SessionStore(sid)
    db_len     = s.get('db_len', -1)
    status_msg = s.get('status_msg', "Working...")

    if db_len == -1:
        return HttpResponse(status_msg)          # 200 – still running
    else:
        return HttpResponse("done", status=418)  # stop polling

# ---------------------------------------------------------------------------
#  Helper – serve PDF or DOCX
# ---------------------------------------------------------------------------
def _serve_output(request, type):
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
            Converter(stream=pdf_buffer).convert(docx_buffer).close()
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

def out(request):
    return _serve_output(request, "pdf")

def out_docx(request):
    return _serve_output(request, "docx")

# ---------------------------------------------------------------------------
#  User / account helpers (unchanged)
# ---------------------------------------------------------------------------
def create_account(request):
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
    login(request, auth_user)
    return redirect("/home")

def auth(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    user = request.POST.get('username')
    password = request.POST.get('password')
    auth_user = authenticate(username=user, password=password)
    if auth_user is not None:
        login(request, auth_user)
        return redirect("/home")
    return redirect(f"{settings.LOGIN_URL}?msg=Incorrect username/password.")

def logout_user(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    logout(request)
    return redirect(settings.LOGIN_URL)

def delete_account(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    user = request.user
    logout(request)
    if user is not None:
        user.delete()
    return redirect(settings.LOGIN_URL)

# ---------------------------------------------------------------------------
#  Template views (unchanged)
# ---------------------------------------------------------------------------
def home(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "home.html", util.params_to_dict(request, 'msg'))

def about(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "about.html")

def contact(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "contact.html")

def output(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "output.html", util.params_to_dict(request, 'msg'))

def login_page(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    if request.user is None or not request.user.is_authenticated:
        return render(request, "login.html", util.params_to_dict(request, 'msg'))
    return redirect(home)

def new_account(request):
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    return render(request, "new.html", util.params_to_dict(request, 'msg'))
