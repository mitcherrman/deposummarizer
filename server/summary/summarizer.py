import fitz  # PyMuPDF
import time
import io
import os
import logging
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib import colors
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from decouple import config
import server.summary.deposition_chatbot as cb  # Reintroducing chatbot
from django.conf import settings
from importlib import import_module
from server.util import session_lock
import base64

session_engine = import_module(settings.SESSION_ENGINE)

# Set up logging
logging.basicConfig(level=logging.INFO)

# Initialize Langchain OpenAI model
llm = ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'), temperature=1)  # Secure API key handling

# Define a prompt template for better input to the LLM
def getPrompt(filter_keywords=None, filter_exclude=False):
    systemText = ""
    if filter_keywords == None:
        systemText = """You will be given a section from a legal deposition.
            Provide a brief summary of each page, considering the context of the entire document.
            Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
            Separate bullet points with <br/>.
            """
    else:
        filters = [s.strip() for s in filter_keywords.split(',')]
        if filter_exclude:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Some details are unimportant. Do not include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filters)}]
                Include no information that pertains to these keywords. If a page only contains information related to these keywords, then say that no important information is on the page.
                """
        else:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Only certain details are important. Only include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filters)}]
                Include no information that does not pertain to these keywords. If a page contains no information related to these keywords, then say that no important information is on the page.
                """
    prompt = ChatPromptTemplate.from_messages([
        ("system", systemText),
        ("user", "{input}")
    ])
    return prompt

# Initialize output parser to convert chat message to string
output_parser = StrOutputParser()

# Combine prompt, LLM, and output parser into a chain
def getChain(filter_keywords=None, filter_exclude=False):
    chain = getPrompt(filter_keywords, filter_exclude) | llm | output_parser
    return chain

# Helper function to determine if a page is valid for processing based on content length or keywords
KEYWORDS = ["Exhibit", "Affidavit", "Page", "Witness"]
def is_page_valid(text, min_length=150, keywords=KEYWORDS):
    if len(text) >= min_length:
        return True
    for keyword in keywords:
        if keyword.lower() in text.lower():
            return True
    return False

# Remove marginal text (headers, footers, side margins)
def remove_marginal_text(input_buffer, output_buffer):
    pdf_document = fitz.open(stream=input_buffer, filetype="pdf")
    new_pdf = fitz.open()  # Create a new PDF

    for page_num in range(pdf_document.page_count):
        page = pdf_document.load_page(page_num)
        page_rect = page.rect  # Get the page dimensions

        # Define threshold areas for headers, footers, and side margins
        header_threshold = 0.1 * page_rect.height  # Top 10% of the page
        footer_threshold = 0.9 * page_rect.height  # Bottom 10% of the page
        side_margin_threshold = 0.1 * page_rect.width  # Left and right 10% of the page

        # Extract all text blocks
        text_blocks = page.get_text("dict")['blocks']

        # Create a new page to copy only the main text
        new_page = new_pdf.new_page(width=page_rect.width, height=page_rect.height)

        # Iterate over text blocks and filter out marginal content
        for block in text_blocks:
            if 'lines' in block:
                for line in block['lines']:
                    for span in line['spans']:
                        block_rect = fitz.Rect(span['bbox'])
                        text = span['text']

                        # Skip blocks in the header, footer, or side margins
                        if (block_rect.y1 <= header_threshold or
                            block_rect.y0 >= footer_threshold or
                            block_rect.x0 <= side_margin_threshold or
                            block_rect.x1 >= page_rect.width - side_margin_threshold):
                            continue  # This is marginal content, skip it

                        # Otherwise, re-insert the content in the new PDF using default font
                        new_page.insert_text((block_rect.x0, block_rect.y0), text, fontsize=span['size'])

    pdf_document.close()

    # Save the new PDF to the output buffer
    new_pdf.save(output_buffer)
    new_pdf.close()

# Extracts text from the PDF, ignoring top and bottom margins.
# Returns the extracted text as a string.
def extract_text_with_numbers(pdf_buffer, exclude_top=40, exclude_bottom=70, number_margin=True):
    try:
        doc = fitz.open(stream=pdf_buffer, filetype="pdf")
    except Exception as e:
        logging.error(f"Error opening PDF buffer: {e}")
        raise ValueError("Could not open PDF from buffer")
    
    all_text = []
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        if not fitz.get_tessdata():
            logging.error("Tesseract is not installed")
            raise RuntimeError()
        ocr_page = page.get_textpage_ocr()
        text_blocks = page.get_text("blocks", textpage=ocr_page)
        filtered_text = "\n".join(block[4] for block in text_blocks) #extract_filtered_text(text_blocks, page_height, exclude_top, exclude_bottom)

        if is_page_valid(filtered_text):
            all_text.append(f"\n{filtered_text}")
            logging.info(f"Processed page {page_num + 1}, content size {len(filtered_text)}")
        else:
            logging.info(f"Skipped page {page_num + 1}, content size {len(filtered_text)}")

    doc.close()
    return "".join(all_text)

# Filter out text blocks within excluded areas.
def extract_filtered_text(blocks, page_height, exclude_top, exclude_bottom):
    return "\n".join(
        block[4] for block in blocks 
        if not (block[1] < exclude_top or block[3] > page_height - exclude_bottom) and block[4].strip()
    )

# Writes summaries to a PDF file at the given output path.
def write_summaries_to_pdf(summaries, output):
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72  # Adjust margins for readability
    )
    story = build_pdf_story(summaries)
    doc.build(story)

# Build the paragraphs and structure for the PDF document with improved styling
def build_pdf_story(summaries):
    styles = getSampleStyleSheet()

    # Define styles for the page headers and summaries
    page_style = ParagraphStyle(
        name='Page',
        parent=styles['Normal'],
        fontSize=16,  # Larger font size for page headers
        leading=16,
        spaceAfter=12,
        textColor=colors.HexColor('#007bff'),  # Blue color for page titles
        fontName="Helvetica-Bold"
    )

    summary_style = ParagraphStyle(
        name='Summary',
        parent=styles['Normal'],
        fontSize=12,
        leading=14,
        spaceAfter=10,
        textColor=colors.black,
        fontName="Times-Roman",
        borderPadding=(5, 5, 5, 5),  # Add some padding around the summaries
        backColor=colors.whitesmoke,  # Light background for summaries
        borderColor=colors.black,
        borderWidth=1,
        borderRadius=5
    )
    
    story = []
    
    # Iterate over the list of summaries
    for i, summary in enumerate(summaries, start=1):  # Use enumerate to get the page number
        # Page header with larger font and color
        story.append(Paragraph(f"Page {i}", page_style))
        
        # Summary paragraph with enhanced formatting
        story.append(Paragraph(f"{summary}", summary_style))
        
        story.append(Spacer(1, 12))  # Add some space between summaries
    
    return story

# Sends text to the language model and returns the summary.
def summarize_deposition_text(text, filter_keywords=None, filter_exclude=False):
    try:
        response = getChain(filter_keywords, filter_exclude).invoke({"input": text, "max_tokens": 52000})
        return response.strip()
    except Exception as e:
        logging.error(f"Error summarizing text: {e}")
        return ""

# Summarizes each page of the deposition and returns the summaries.
def summarize_deposition(text_pages, id, filter_keywords=None, filter_exclude=False):
    summaries = []  # Use a list to store summaries in order
    for page_num, page in enumerate(text_pages, start=1):
        update_status_msg(id, f"Summarizing page {page_num} of {len(text_pages)}...")
        if len(page) > 150:
            summary = summarize_deposition_text(page, filter_keywords, filter_exclude)
            summaries.append(summary)  # Store summaries in the list in order
        else:
            logging.info(f"[{id}]: Skipped page {page_num} with size {len(page)}")
        if race_check(id):
            return []
    return summaries

# Split extracted text into pages, assuming each page starts with "Page" followed by a number.
def split_text_by_page(text):
    lines = text.split('\n')
    pages = [""]
    page_num = 0
    for line in lines:
        i = 0
        l = len(line)
        while l > i and (ord(line[i]) <= 32 or ord(line[i]) >= 127):
            i += 1
        if l > i and line[i] == '1' and (l == i+1 or not line[i+1].isdigit()):
            pages.append(f"Page {page_num}:\n{line}")
            page_num += 1
        elif l > i:
            pages[-1] = f"{pages[-1]} {line}"
    return pages

def update_status_msg(id, msg):
    with session_lock:
        session = session_engine.SessionStore(id)
        if session.exists(id):
            session['status_msg'] = msg
            session.save()

def race_check(id):
    with session_lock:
        session = session_engine.SessionStore(id)
        return not session.exists(id) or not session.get('db_len') or session['db_len'] != -1

# Orchestrates the entire summary process: from removing marginal text, extracting text, splitting it into pages,
# summarizing it, and writing the final summaries to a PDF.
def create_summary(pdf_data, id, filter_keywords=None, filter_exclude=False):
    if not pdf_data:
        logging.error(f"[{id}]: No PDF data provided")
        return 0

    try:
        logging.info(f"Processing PDF data for session {id}")
        if filter_keywords == None:
            logging.info(f"No keyword filter used")
        else:
            logging.info(f"{'Exclude' if filter_exclude else 'Include'} filter for keywords: {filter_keywords}")
        update_status_msg(id, "Extracting text...")
        if race_check(id):
            return -1

        # Create BytesIO objects for processing
        pdf_buffer = io.BytesIO(pdf_data)
        cleaned_buffer = pdf_buffer #io.BytesIO()

        # Clean up marginal text
        #remove_marginal_text(pdf_buffer, cleaned_buffer)
        cleaned_buffer.seek(0)

        raw_text = extract_text_with_numbers(cleaned_buffer)
        if race_check(id):
            return -1

        text_pages = split_text_by_page(raw_text)
        if race_check(id):
            return -1

        #first page is residual fiiller, second a cover, no use
        text_pages = text_pages[2:]

        # Initialize chatbot for processing
        update_status_msg(id, "Configuring chatbot...")
        l = cb.initBot(raw_text, id)
        if race_check(id):
            return -1
        
        update_status_msg(id, "Starting summary...")
        if not settings.TEST_WITHOUT_AI:
            summarized_pages = summarize_deposition(text_pages, id, filter_keywords, filter_exclude)
            if race_check(id):
                return -1
            # Store summary in session as base64
            with session_lock:
                s = session_engine.SessionStore(id)
                if s.exists(id):
                    summary_buffer = io.BytesIO()
                    write_summaries_to_pdf(summarized_pages, summary_buffer)
                    if race_check(id):
                        return -1
                    s['summary_pdf'] = base64.b64encode(summary_buffer.getvalue()).decode('utf-8')
                    s.save()
        else:
            summary_buffer = io.BytesIO()
            write_summaries_to_pdf(text_pages[0:5], summary_buffer)
            if race_check(id):
                return -1
            with session_lock:
                s = session_engine.SessionStore(id)
                if s.exists(id):
                    s['summary_pdf'] = base64.b64encode(summary_buffer.getvalue()).decode('utf-8')
                    s.save()

        logging.info(f"[{id}]: Summary saved to session")
        update_status_msg(id, "Finishing up...")
        return l
    except Exception as e:
        logging.error(f"Error in create_summary: {str(e)}")
        return -2