from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import shutil, os
from server.summary.deposition_chatbot import DB_PIECE_SIZE
from decouple import config
from threading import Thread

#ssummarizes input and sets up chatbot
@csrf_exempt
def summarize(request):
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	#check if summary already started
	try:
		if request.session['db_len'] == -1 and not os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{id}.pdf"):
			return HttpResponse("Summary in progress, please wait.")
	except: pass
	json_data = request.GET #json.loads(request.body)
	print(f"[{id}]: {json_data}")
	#clean up previous summaries
	dirname = f"databases/vectordb_data_{DB_PIECE_SIZE}k_" + id + "_OpenAI"
	if not settings.TEST_WITHOUT_AI:
		if os.path.isdir(dirname): shutil.rmtree(dirname)
	if os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{id}.pdf"): os.remove(f"{config('OUTPUT_FILE_PATH')}/output_{id}.pdf")
	request.session['db_len'] = -1
	request.session['prompt_append'] = []
	#start summarizing thread
	def r(id):
		l = create_summary(json_data, id)
		request.session['db_len'] = l
		request.session.save()
	t = Thread(target=r,args=[id])
	t.start()
	return HttpResponse("Summary started.")

#ask a question to the chatbot, requires summary to have been done first
@csrf_exempt
def ask(request):
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	json_data = request.GET #json.loads(request.body)
	print(f"[{id}]: {json_data}")
	#check for finished summary
	if (not request.session.get('db_len')) or request.session['db_len'] <= 0: return HttpResponse("No file summarized")
	response = askQuestion(json_data.get('question', False), id, request.session['prompt_append'], request.session['db_len'])
	request.session['prompt_append'] = response[1]
	return HttpResponse(response[0])

#debug view to print session in console, remove in production
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

#clears session/associated files
@csrf_exempt
def clear(request):
	try:
		dirname = f"databases/vectordb_data_{DB_PIECE_SIZE}k_" + request.session.session_key + "_OpenAI"
	except: pass
	request.session.clear()
	if os.path.isdir(dirname): shutil.rmtree(dirname)
	if os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf"): os.remove(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf")
	return HttpResponse("session cleared")

#provides output pdf
def output(request):
	if not request.session.session_key:
		request.session.save()
	id = request.session.session_key
	try:
		print(f"[{id}]: {config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf")
		with open(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf", 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		try:
			#check for summary in progress
			if request.session.get['db_len'] == -1:
				return HttpResponse("Working on it")
			#summarize returns 0 if the path isn't given in request body
			elif (request.session.get['db_len'] == 0):
				return HttpResponse("Error during summary: does the file path exist?")
			else:
				return HttpResponse("Unknown error")
		except KeyError:
			return HttpResponse("No input file found, summarize using the /summarize view")
