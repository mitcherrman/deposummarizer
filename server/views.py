from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from server.summary.summarizer import create_summary
import json
from decouple import config
from django.shortcuts import render
import os

@csrf_exempt
def summarize(request):
    if request.method == 'POST':
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return HttpResponse("No file uploaded.")
        
        # Save the uploaded file temporarily
        file_path = os.path.join(config('UPLOAD_DIR'), uploaded_file.name)
        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        # Process the uploaded file
        json_data = {
            "file_path": file_path
        }
        
        if not create_summary(json_data):
            return HttpResponse("Error processing file.")
        else:
            return HttpResponse("File summarized successfully.")
    
    return HttpResponse("Invalid request method.")

def output(request, filename=''):
	if filename == '':
		return HttpResponse("Give me a real file name bruv")
	try:
		with open(config('OUTPUT_FILE_PATH') + filename, 'rb') as pdf:
			response = HttpResponse(pdf.read(), content_type='application/pdf')
			response['Content-Disposition'] = 'filename=some_file.txt'
			return response
	except FileNotFoundError:
		return HttpResponse("File ain't ready yet boyo")

def home (request):
	return render(request, "home.html")
