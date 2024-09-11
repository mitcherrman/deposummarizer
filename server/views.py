from django.http import HttpResponse, HttpResponseNotFound, HttpResponseNotAllowed, HttpResponseServerError, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import shutil, os
from threading import Thread, Lock
from importlib import import_module
import json
from decouple import config
from django.shortcuts import render
import os


session_engine = import_module(settings.SESSION_ENGINE)

#session lock used by thread, should be used by anything else that may modify session during summary process
session_lock = Lock()

#summarizes input and sets up chatbot
@csrf_exempt
def summarize(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	#check if summary already started
	try:
		if request.session['db_len'] == -1 and not os.path.isfile(f"{settings.SUMMARY_URL}{id}.pdf"):
			return HttpResponse("Summary in progress, please wait.", status=409)
	except: pass
	try:
		data = json.loads(request.body)
	except:
		return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"file_path\" key")
	if not data.get('file_path'):
		return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"file_path\" key")
	print(f"[{id}]: {data}")
	#clean up previous summaries
	dirname = settings.CHROMA_URL + id
	if not settings.TEST_WITHOUT_AI:
		if os.path.isdir(dirname): shutil.rmtree(dirname)
	if os.path.isfile(f"{settings.SUMMARY_URL}{id}.pdf"): os.remove(f"{settings.SUMMARY_URL}{id}.pdf")
	request.session['db_len'] = -1
	request.session['prompt_append'] = []
	#start summarizing thread
	def r(id): #note: session key cannot change during this thread's execution (relevant for authentication if it's implemented)
		l = create_summary(data, id)
		request.session['db_len'] = l #in case this thread somehow ends before view
		with session_lock:
			s = session_engine.SessionStore(id) #is there a cleaner way to do this?
			if s.exists(id):
				s['db_len'] = l
				s.save()
	t = Thread(target=r,args=[id])
	t.start()
	return HttpResponse("Summary started.")

#ask a question to the chatbot, requires summary to have been done first
@csrf_exempt
def ask(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	try:
		data = json.loads(request.body)
	except:
		return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"question\" key")
	print(f"[{id}]: {data}")
	#check for finished summary
	if (not request.session.get('db_len')) or request.session['db_len'] <= 0: return HttpResponse("No file summarized", status=409)
	if not data.get('question'):
		return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"question\" key")
	response = askQuestion(data.get('question', False), id, request.session['prompt_append'], request.session['db_len'])
	if response == None:
		return HttpResponseServerError("Something went wrong with the OpenAI call, please try again later.")
	request.session['prompt_append'] = response[1]
	return HttpResponse(response[0])

#debug view to print session in console, returns 404 in production
@csrf_exempt
def session(request):
	if not settings.DEBUG:
		return HttpResponseNotFound()
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
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
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	request.session.cycle_key()
	return HttpResponse("done")

#clears session/associated files
@csrf_exempt
def clear(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	request.session.clear()
	return HttpResponse("session cleared")

#provides output pdf
@csrf_exempt
def output(request):
	if request.method != 'GET':
		return HttpResponseNotAllowed(['GET'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	try:
		#open summary file
		print(f"[{id}]: {settings.SUMMARY_URL}{id}.pdf")
		with open(f"{settings.SUMMARY_URL}{id}.pdf", 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		try:
			#check for summary in progress
			if request.session['db_len'] == -1:
				return HttpResponse("Working on it", status=409)
			#summarize returns 0 if the path isn't given in request body
			elif (request.session['db_len'] == 0):
				return HttpResponseBadRequest("Malformed body, should be formatted in JSON with a value for the \"file_path\" key")
			#returns -2 if there was some error in the initial pdf processing (probably file not found)
			if request.session['db_len'] == -2:
				return HttpResponseServerError("Summary file did not save properly")
			else:
				return HttpResponseServerError("Unknown error")
		except KeyError:
			return HttpResponse("No input file found, summarize using the /summarize view", status=409)
		
def home (request):
	return render(request, "home.html")