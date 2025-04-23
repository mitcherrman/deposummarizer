# Deposummarizer

A web app that summarizes legal depositions using AI.

## Environment Variables

The following environment variables must be set:
- `OPENAI_KEY`: Your OpenAI API key for accessing GPT models
- `GPT_MODEL`: The name of the GPT model to use (e.g. "gpt-4")
- `CODE_PATH`: Absolute path to the project directory
- `STATIC_ROOT`: Location of static files in production
- `DB_NAME`: Name of the PostgreSQL database
- `DB_HOST`: Hostname of the PostgreSQL database
- `DB_PORT`: Port number for the PostgreSQL database (default: 5432)
- `DB_CA_PATH`: Path to the SSL certificate bundle for database connection
- `SECRET_KEY`: Django secret key for cryptographic signing

## Installation

1. Install Python 3.10 or later
2. Install the required python packages: `pip install -r requirements.txt`
3. Install and set up additional dependencies based on individual instructions:
   1. Tesseract-ocr ([https://tesseract-ocr.github.io/tessdoc/Installation.html])
   2. Postgres server for local testing ([https://www.postgresql.org/download/])
4. Set up the environment variables listed above
5. Run the development server: `python manage.py runserver`

## Usage

1. Upload a PDF file containing a legal deposition
2. Wait for the summary to be generated
3. View the summary and ask questions about the deposition

## Architecture

The app uses:
- Django for the web framework
- OpenAI's GPT models for text generation
- PGVector for vector storage and similarity search
- Session database for storing uploaded and generated PDFs
- PyMuPDF for PDF processing
- Langchain for AI model interaction

## License

This project is licensed under the MIT License - see the [LICENSE.txt](LICENSE.txt) file for details.
