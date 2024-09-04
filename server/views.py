from django.http import HttpResponse, HttpResponseNotFound, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import shutil, os
from decouple import config
from threading import Thread

#ssummarizes input and sets up chatbot
@csrf_exempt
def summarize(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	#check if summary already started
	try:
		if request.session['db_len'] == -1 and not os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/{id}.pdf"):
			return HttpResponse("Summary in progress, please wait.", status=409)
	except: pass
	data = request.GET
	print(f"[{id}]: {data}")
	#clean up previous summaries
	dirname = settings.CHROMA_URL + id
	if not settings.TEST_WITHOUT_AI:
		if os.path.isdir(dirname): shutil.rmtree(dirname)
	if os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/{id}.pdf"): os.remove(f"{config('OUTPUT_FILE_PATH')}/{id}.pdf")
	request.session['db_len'] = -1
	request.session['prompt_append'] = []
	#start summarizing thread
	def r(id):
		l = create_summary(data, id)
		request.session['db_len'] = l
		request.session.save()
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
	data = request.GET
	print(f"[{id}]: {data}")
	#check for finished summary
	if (not request.session.get('db_len')) or request.session['db_len'] <= 0: return HttpResponse("No file summarized", status=409)
	response = askQuestion(data.get('question', False), id, request.session['prompt_append'], request.session['db_len'])
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
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	try:
		#open summary file
		print(f"[{id}]: {config('OUTPUT_FILE_PATH')}/{id}.pdf")
		with open(f"{config('OUTPUT_FILE_PATH')}/{id}.pdf", 'rb') as pdf:
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
				return HttpResponse("Error during summary: does the file path exist?", status=409)
			else:
				return HttpResponse("Unknown error", status=500)
		except KeyError:
			return HttpResponse("No input file found, summarize using the /summarize view", status=409)
