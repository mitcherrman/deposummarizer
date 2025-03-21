from django.http import HttpResponse, HttpResponseNotFound, HttpResponseNotAllowed, HttpResponseServerError, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import django.template.loader as ld
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import shutil, os
from threading import Thread
from importlib import import_module
import json
from decouple import config
from django.shortcuts import render, redirect
from pdf2docx import Converter
import os
from server.util import session_lock
import io
import base64

session_engine = import_module(settings.SESSION_ENGINE)

# --- backend views ---

#summarizes input and sets up chatbot
def summarize(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	#check if summary already started
	try:
		if request.session['db_len'] == -1:
			return HttpResponse("Summary in progress, please wait.", status=409)
	except: pass
	if not (request.FILES and request.FILES['file']):
		HttpResponseBadRequest("Malformed request, should contain a file called \"file\"")
	request.session['db_len'] = -1
	request.session['prompt_append'] = []
	
	#clean up previous summaries
	dirname = settings.CHROMA_URL + id
	if not settings.TEST_WITHOUT_AI:
		if os.path.isdir(dirname): shutil.rmtree(dirname)
	
	#store input file in session as base64
	file = request.FILES['file']
	pdf_data = file.read()
	request.session['depo_pdf'] = base64.b64encode(pdf_data).decode('utf-8')
	request.session.save()

	#start summarizing thread
	def r(id):
		l = create_summary(pdf_data, id)
		request.session['db_len'] = l
		with session_lock:
			s = session_engine.SessionStore(id)
			if s.exists(id):
				s['db_len'] = l
				s.save()
	t = Thread(target=r,args=[id])
	with session_lock:
		t.start()
		request.session.save()
	return redirect(output)

#ask a question to the chatbot, requires summary to have been done first
def ask(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	try:
		data = request.POST
	except:
		return HttpResponseBadRequest("Malformed body, should have a \"question\" element in the body")
	print(f"[{id}]: {data}")
	#check for finished summary
	if (not request.session.get('db_len')) or request.session['db_len'] <= 0: return HttpResponse("No file summarized", status=409)
	if not data.get('question'):
		return HttpResponseBadRequest("Please enter a question.")
	response = askQuestion(data['question'], id, request.session['prompt_append'], request.session['db_len'])
	if response == None:
		return HttpResponseServerError("Something went wrong with the OpenAI call, please try again later.")
	request.session['prompt_append'] = response[1]
	return HttpResponse(response[0])

def chat_html(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	try:
		log = request.session['prompt_append']
	except:
		return HttpResponse()
	ret = ""
	for line in log:
		ret += ld.render_to_string('chat_message.html', {'outgoing': line['role'] == 'user', 'message': line['content']})
	return HttpResponse(ret)

#returns a transcript of the chat dialog
def transcript(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	raw = request.session['prompt_append']
	dialog = ""
	for line in raw:
		dialog += ("Q" if line['role'] == 'user' else "A" if line['role'] == 'assistant' else "?") + ": " + line['content'] + "\n"
	response = HttpResponse(dialog, content_type='text/plain')
	response['Content-Disposition'] = 'filename=deposum_chat_transcript.txt'
	return response

#debug view to print session in console, returns 404 in production
@csrf_exempt
def session(request):
	if not settings.DEBUG:
		return HttpResponseNotFound()
	if not request.session.session_key:
		request.session.save()
	print(request.session.session_key)
	print(request.session)
	print(request.session.items())
	return HttpResponse("done")

#debug view to cycle session key, returns 404 in production
@csrf_exempt
def cyclekey(request):
	if not settings.DEBUG:
		return HttpResponseNotFound()
	request.session.cycle_key()
	return HttpResponse("done")

#clears session/associated files
@csrf_exempt
def clear(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	id = request.session.session_key
	if id:
		dirname = settings.CHROMA_URL + id
		if os.path.isdir(dirname):
			shutil.rmtree(dirname)
	request.session.clear()
	return HttpResponse("session cleared")

#helper function, gets output doc in different formats
def get_out(request, type):
	if request.method != 'GET' and request.method != 'HEAD':
		return HttpResponseNotAllowed(['GET', 'HEAD'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	if request.method == 'HEAD':
		if 'summary_pdf' in request.session:
			return HttpResponse()
		elif not request.session.get('db_len') or request.session['db_len'] == -1:
			return HttpResponse(status=409)
		elif request.session['db_len'] == 0:
			return HttpResponseBadRequest()
		else:
			return HttpResponseServerError()
	try:
		if type == "pdf":
			pdf_data = base64.b64decode(request.session['summary_pdf'])
			response = HttpResponse(pdf_data, content_type='application/pdf')
			response['Content-Disposition'] = 'filename=deposition_summary.pdf'
			return response
		elif type == "docx":
			# Convert PDF data to DOCX
			pdf_data = base64.b64decode(request.session['summary_pdf'])
			pdf_buffer = io.BytesIO(pdf_data)
			docx_buffer = io.BytesIO()
			conv = Converter(stream=pdf_buffer)
			conv.convert(docx_buffer)
			conv.close()
			docx_buffer.seek(0)
			response = HttpResponse(docx_buffer.read(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
			response['Content-Disposition'] = 'filename=deposition_summary.docx'
			return response
		else:
			raise ValueError()
	except KeyError:
		try:
			#check for summary in progress
			if request.session['db_len'] == -1:
				return HttpResponse("Working on it", status=409)
			#summarize returns 0 if the path isn't given in request body
			elif request.session['db_len'] == 0:
				return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"file_path\" key")
			#returns -2 if there was some error in the initial pdf processing (probably file not found)
			elif request.session['db_len'] == -2:
				return HttpResponseServerError("Something went wrong in the summary, is the file a valid pdf?")
			else:
				return HttpResponseServerError("Unknown error")
		except KeyError:
			return HttpResponse("No input file found, summarize a file first", status=409)

#provides output pdf
def out(request):
	return get_out(request, "pdf")

#provide output as docx		
def out_docx(request):
	return get_out(request, "docx")

#efficiently checks if summary is in progress
def verify(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	if request.session and request.session['db_len'] == -1:
		return HttpResponse(request.session.get('status_msg', "Working...")) #body used by frontend for status message
	return HttpResponse("Summary not in progress.", status=418) #no error code for "task failed successfully"

#creates an account
def create_account(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	user = request.POST.get('username')
	email = request.POST.get('email')
	password = request.POST.get('password')
	#check if user exists
	#check for valid inputs
	if not user or not password:
		return redirect("/new?msg=1")
	if User.objects.filter(username=user).exists():
		return redirect("/new?msg=2")
	auth = User.objects.create_user(user, email or None, password)
	login(request, auth)
	return redirect("/home")

#authenticates a user and logs in if valid
def auth(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	user = request.POST.get('username')
	password = request.POST.get('password')
	auth = authenticate(username=user, password=password)
	if auth is not None:
		login(request, auth)
		return redirect("/home")
	else:
		return redirect(f"{settings.LOGIN_URL}?msg=1")

#logs user out
def logout_user(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	logout(request)
	return redirect(settings.LOGIN_URL)

#deletes account
def delete_account(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	user = request.user
	logout(request)
	if user is not None:
		user.delete()
	return redirect(settings.LOGIN_URL)

# --- template views ---
		
def home(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	return render(request, "home.html")
		
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
	return render(request, "output.html")

def login_page(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	if request.user is None or not request.user.is_authenticated:
		return render(request, "login.html", request.GET)
	return redirect(home)

def new_account(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	return render(request, "new.html", request.GET)