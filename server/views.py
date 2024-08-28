from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import json, shutil, pickle, io, os
from server.serializer import VectorUnpickler
from server.summary.deposition_chatbot import DB_PIECE_SIZE
from decouple import config

@csrf_exempt
def summarize(request):
	json_data = json.loads(request.body)
	print(json_data)
	dirname = f"vectordb_data_{DB_PIECE_SIZE}k_" + request.META['REMOTE_ADDR'] + "_OpenAI"
	#if os.path.isdir(dirname): shutil.rmtree(dirname)
	id = request.META['REMOTE_ADDR']
	l = create_summary(json_data, id)
	if l == 0:
		return HttpResponse("Error: no file_path")
	else:
		print("Adding session variables...")
		request.session['db_len'] = l
		request.session['prompt_append'] = []
		return HttpResponse("Here's the text of the web page.")

@csrf_exempt
def ask(request):
	print(request.session.items())
	json_data = json.loads(request.body)
	print(json_data)
	id = request.META['REMOTE_ADDR']
	if not request.session.get('db_len'): return HttpResponse("No file summarized")
	response = askQuestion(json_data.get('question', False), id, request.session['prompt_append'], request.session['db_len'])
	request.session['prompt_append'] = response[1]
	return HttpResponse(response[0])

@csrf_exempt
def clear(request):
	if request.session.get('db_len'):
		del request.session['db_len']
		del request.session['init']
		del request.session['prompt_append']
		shutil.rmtree(f"vectordb_data_{DB_PIECE_SIZE}k_" + request.META['REMOTE_ADDR'] + "_OpenAI")
	return HttpResponse("session cleared")

def output(request):
	try:
		with open(config('OUTPUT_FILE_PATH'), 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		return HttpResponse("Working on it")
