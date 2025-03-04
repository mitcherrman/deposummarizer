# Deposummarizer

A web app that summarizes legal depositions using AI.

## Environment Variables

The following environment variables must be set:
- `OPENAI_KEY`: your OpenAI API key
- `GPT_MODEL`: the name of the GPT model to use (e.g. "gpt-4")
- `CHROMA_URL`: the name of the directory where Chroma vector databases will be stored
- `TMP_URL`: the name of the directory where temporary files will be stored
- `SECRET_KEY`: Django secret key

## Installation

1. Install Python 3.10 or later
2. Install the required packages: `pip install -r requirements.txt`
3. Set up the environment variables listed above
4. Run the development server: `python manage.py runserver`

## Usage

1. Upload a PDF file containing a legal deposition
2. Wait for the summary to be generated
3. View the summary and ask questions about the deposition

## Architecture

The app uses:
- Django for the web framework
- OpenAI's GPT models for text generation
- Chroma for vector storage and similarity search
- Session database for storing uploaded and generated PDFs
- PyMuPDF for PDF processing
- Langchain for AI model interaction

In addition, this repository contains a script called `hourly.py`, which should be run hourly 
when the web server is active.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
