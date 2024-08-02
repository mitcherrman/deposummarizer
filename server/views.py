from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from server.summary.summarizer import create_summary
import json
from decouple import config

@csrf_exempt
def summarize(request):
	json_da
	ta = json.loads(request.body)
	print(json_data)
	if not create_summary(json_data):
		return HttpResponse("Error: no file_path")
	else:
		return HttpResponse("Here's the text of the web page.")

def output(request):
	try:
		with open(config('OUTPUT_FILE_PATH'), 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		return HttpResponse("Working on it")
