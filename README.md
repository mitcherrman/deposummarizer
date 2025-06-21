# Deposummarizer

A web app that summarizes legal depositions using AI.

## Environment Variables

The following environment variables must be set in your `.env` file:

### OpenAI Configuration
- `OPENAI_KEY`: Your OpenAI API key for accessing GPT models and embeddings
- `GPT_MODEL`: The name of the GPT model to use (e.g., "gpt-4", "gpt-3.5-turbo")

### Database Configuration
- `DB_NAME`: Name of the PostgreSQL database
- `DB_HOST`: Hostname of the PostgreSQL database server
- `DB_PORT`: Port number for the PostgreSQL database (default: 5432)
- `DB_SECRET_ARN`: AWS Secrets Manager ARN containing database credentials (username/password)
- `DB_CA_PATH`: Path to the SSL certificate bundle for secure database connections

### Django Configuration
- `SECRET_KEY`: Django secret key for cryptographic signing (required in production)
- `DEBUG_MODE`: Set to `True` for development, `False` for production
- `USE_LOCAL_DB`: Set to `True` to use local PostgreSQL for testing, `False` for production database
- `STATIC_ROOT`: Absolute path where static files should be collected in production

### Application Configuration
- `CODE_PATH`: Absolute path to the project directory (used for session cleanup)

## Installation

1. Install Python 3.10 or later
2. Install the required python packages: `pip install -r requirements.txt`
3. Install and set up additional dependencies based on individual instructions:
   1. Tesseract-ocr ([https://tesseract-ocr.github.io/tessdoc/Installation.html])
   2. Postgres server for local testing ([https://www.postgresql.org/download/])
4. Create a `.env` file in the project root and set up the environment variables listed above
5. To run the development server: `python manage.py runserver`

## Usage

1. Upload a PDF file containing a legal deposition
2. Wait for the summary to be generated
3. View the summary and ask questions about the deposition

## Architecture

The app uses:
- Django for the web framework
- OpenAI's GPT models for text generation
- PGVector for vector storage and similarity search
- PyMuPDF for PDF processing
- Langchain for AI model interaction
- AWS to host infrastructure

## License

This project is licensed under the MIT License - see the [LICENSE.txt](LICENSE.txt) file for details.
