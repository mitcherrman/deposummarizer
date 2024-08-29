from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from server.summary.summarizer import create_summary
from server.summary.deposition_chatbot import askQuestion
import json, shutil, os
from server.summary.deposition_chatbot import DB_PIECE_SIZE
from decouple import config
from threading import Thread

@csrf_exempt
def summarize(request):
	#check if summary already started
	try:
		if request.session['init'] and not os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf"):
			return HttpResponse("Summary in progress, please wait.")
	except: pass
	json_data = json.loads(request.body)
	print(json_data)
	#clean up previous summaries
	dirname = f"databases/vectordb_data_{DB_PIECE_SIZE}k_" + request.session.session_key + "_OpenAI"
	if os.path.isdir(dirname): shutil.rmtree(dirname)
	if os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf"): os.remove(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf")
	if os.path.isfile(f"len_data/{request.session.session_key}"): os.remove(f"len_data/{request.session.session_key}")
	request.session['init'] = True
	request.session['prompt_append'] = []
	#start summarizing thread
	def r(id):
		l = create_summary(json_data, id)
		with open(f"len_data/{id}", 'w') as f:
			f.write(str(l))
	t = Thread(target=r,args=[request.session.session_key])
	t.start()
	return HttpResponse("Summary started.")
	# l = create_summary(json_data, id)
	# if l == 0:
	# 	return HttpResponse("Error: no file_path")
	# else:
	# 	request.session['db_len'] = l
	# 	request.session['prompt_append'] = []
	# 	return HttpResponse("Here's the text of the web page.")

@csrf_exempt
def ask(request):
	json_data = json.loads(request.body)
	print(json_data)
	#check for finished summary
	id = request.session.session_key
	if (not request.session.get('init')) or not os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{id}.pdf"): return HttpResponse("No file summarized")
	with open(f"len_data/{id}", 'r') as f:
		l = int(f.read())
	response = askQuestion(json_data.get('question', False), id, request.session['prompt_append'], l)
	request.session['prompt_append'] = response[1]
	return HttpResponse(response[0])

#debug view to print session in console, remove in production
@csrf_exempt
def session(request):
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
	if os.path.isfile(f"{config('OUTPUT_FILE_PATH')}/output_{id}.pdf"): os.remove(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf")
	if os.path.isfile(f"len_data/{request.session.session_key}"): os.remove(f"len_data/{request.session.session_key}")
	return HttpResponse("session cleared")

def output(request):
	try:
		print(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf")
		with open(f"{config('OUTPUT_FILE_PATH')}/output_{request.session.session_key}.pdf", 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		try:
			def get_id():
				with open(f"len_data/{request.session.session_key}") as f: return int(f.read())
			#check for summary in progress
			if request.session.get('init') and not os.path.isfile(f"len_data/{request.session.session_key}"):
				return HttpResponse("Working on it")
			#summarize returns 0 if the path isn't given in request body
			elif (get_id() == 0):
				return HttpResponse("Error during summary: does the file path exist?")
			else:
				return HttpResponse("Unknown error")
		except KeyError:
			return HttpResponse("No input file found, summarize using the /summarize view")
