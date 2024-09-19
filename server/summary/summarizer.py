import fitz  # PyMuPDF
import time
import io
import os
import logging
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from decouple import config
import server.summary.deposition_chatbot as cb  # Reintroducing chatbot
from django.conf import settings

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize Langchain OpenAI model
llm = ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'))  # Secure API key handling

# Define a prompt template for better input to the LLM
prompt = ChatPromptTemplate.from_messages([
    ("system", "The input is a legal deposition. Summarize into brief key points without adhering to grammatical rules. Only include the bare minimum amount of information."),
    ("user", "{input}")
])

# Initialize output parser to convert chat message to string
output_parser = StrOutputParser()

# Combine prompt, LLM, and output parser into a chain
chain = prompt | llm | output_parser

# Extracts text from the PDF, ignoring top and bottom margins.
# Returns the extracted text as a string.
def extract_text_with_numbers(pdf_path, exclude_top=40, exclude_bottom=70):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logging.error(f"Error opening PDF file: {e}")
        raise FileNotFoundError(f"Could not open PDF file: {pdf_path}")
    
    all_text = []
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        text_blocks = page.get_text("blocks")
        filtered_text = extract_filtered_text(text_blocks, page_height, exclude_top, exclude_bottom)

        if len(filtered_text) > 150:
            all_text.append(f"Page {page_num + 1}\n{filtered_text}")
            logging.info(f"Processed page {page_num + 1}, content size {len(filtered_text)}")
        else:
            logging.info(f"Skipped page {page_num + 1}, content size {len(filtered_text)}")

    return "".join(all_text)

# Filter out text blocks within excluded areas.
def extract_filtered_text(blocks, page_height, exclude_top, exclude_bottom):
    return "\n".join(
        block[4] for block in blocks 
        if not (block[1] < exclude_top or block[3] > page_height - exclude_bottom) and block[4].strip()
    )

# Writes summaries to a PDF file at the given output path.
def write_summaries_to_pdf(summaries, output_path):
    with io.BytesIO() as pdf_file:
        doc = SimpleDocTemplate(pdf_file, pagesize=letter)
        story = build_pdf_story(summaries)
        doc.build(story)
        pdf_file.seek(0)
        with open(output_path, 'wb') as f:
            f.write(pdf_file.read())

# Build the paragraphs and structure for the PDF document.
def build_pdf_story(summaries):
    styles = getSampleStyleSheet()
    page_style = ParagraphStyle(
        name='Page', parent=styles['Normal'], fontSize=14, leading=12, spaceAfter=11, bold=True
    )
    summary_style = ParagraphStyle(
        name='Summary', parent=styles['Normal'], fontSize=12, leading=12, spaceAfter=11
    )
    
    story = []
    for page_num, summary in enumerate(summaries, start=1):
        story.append(Paragraph(f"Page {page_num}", page_style))
        story.append(Paragraph(summary, summary_style))
        story.append(Spacer(1, 10))
    return story

# Sends text to the language model and returns the summary.
def summarize_deposition_text(text):
    try:
        response = chain.invoke({"input": text, "max_tokens": 52000})
        return response.strip()
    except Exception as e:
        logging.error(f"Error summarizing text: {e}")
        return ""

# Summarizes each page of the deposition and returns the summaries.
def summarize_deposition(text_pages, id):
    summaries = []
    for page in text_pages:
        if len(page) > 150:
            summary = summarize_deposition_text(page)
            summaries.append(summary)
        else:
            logging.info(f"[{id}]: Skipped page with size {len(page)}")
    return summaries

# Split extracted text into pages, assuming each page starts with "Page" followed by a number.
def split_text_by_page(text):
    pages = text.split('\nPage ')
    pages = [page if i == 0 else 'Page ' + page for i, page in enumerate(pages)]
    return pages

# Orchestrates the entire summary process: from extracting text, splitting it into pages,
# summarizing it, and writing the final summaries to a PDF.
def create_summary(request, id):
    file_path = request
    if not file_path:
        logging.error(f"[{id}]: No file path provided")
        return 0

    logging.info(f"Processing file: {file_path}")
    
    try:
        raw_text = extract_text_with_numbers(file_path)
    except FileNotFoundError:
        return -2

    # Initialize chatbot for processing
    l = cb.initBot(raw_text, id)

    text_pages = split_text_by_page(raw_text)
    
    if not settings.TEST_WITHOUT_AI:
        summarized_pages = summarize_deposition(text_pages, id)
        write_summaries_to_pdf(summarized_pages, f"{settings.SUMMARY_URL}{id}.pdf")
    else:
        write_summaries_to_pdf(text_pages[0], f"{settings.SUMMARY_URL}{id}.pdf")

    logging.info(f"[{id}]: Summary saved to: {settings.SUMMARY_URL}{id}.pdf")
    return l
