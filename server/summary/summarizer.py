import fitz  # PyMuPDF
import time
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from decouple import config
import io
import os
import server.summary.deposition_chatbot as cb
from django.conf import settings

# Initialize Langchain OpenAI model
llm = ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'))  # Use a secure method to handle your API key

# Define a prompt template for better input to the LLM
prompt = ChatPromptTemplate.from_messages([
    ("system", "The input is a legal deposition. Summarize into brief key points without adhering to grammatical rules. Only include the bare minimum amount of information."),
    ("user", "{input}")
])

# Initialize output parser to convert chat message to string
output_parser = StrOutputParser()

# Combine prompt, LLM, and output parser into a chain
chain = prompt | llm | output_parser

def extract_text_with_numbers(pdf_path, output_txt_path, id):
    # Open the PDF
    doc = fitz.open(pdf_path)
    all_text = []

    # Define position ranges to exclude (e.g., top and bottom margins)
    exclude_top = 40  # adjust as needed
    exclude_bottom = 70  # adjust as needed
    page_width = doc[0].rect.width

    def is_within_excluded_area(block, page_height):
        x0, y0, x1, y1 = block[:4]
        if y0 < exclude_top or y1 > page_height - exclude_bottom:
            return True
        return False

    # Extract text from each page
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        text_blocks = page.get_text("blocks")  # Get text blocks

        # Filter out text blocks within the excluded areas
        filtered_blocks = [block for block in text_blocks if not is_within_excluded_area(block, page_height)]

        # Combine the filtered blocks into a single string
        filtered_text = "\n".join(block[4] for block in filtered_blocks if block[4].strip() != '')

        if len(filtered_text) > 150:  # Only process pages with text length greater than 150 characters
            all_text.append(f"Page {page_num + 1}")
            all_text.append(filtered_text)
            all_text.append("")  # Add a space between pages for readability
            print(f"[{id}]: Processed page {page_num + 1} of size {len(filtered_text)}")
        else:
            print(f"[{id}]: Skipped page {page_num + 1} of size {len(filtered_text)}")

    if not output_txt_path: return "".join(all_text)

    # Write results to a text file
    with open(output_txt_path, 'w', encoding='utf-8') as f:
        for line in all_text:
            f.write(line + '\n')

def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()

def split_text_by_page(text):
    # Assuming each page starts with "Page" followed by a number
    pages = text.split('\nPage ')
    pages = [page if i == 0 else 'Page ' + page for i, page in enumerate(pages)]
    return pages

def write_summaries_to_pdf(summaries, output_path):
    pdf_file = io.BytesIO()
    doc = SimpleDocTemplate(pdf_file, pagesize=letter)
    styles = getSampleStyleSheet()

    # Define custom styles
    page_style = ParagraphStyle(
        name='Page',
        parent=styles['Normal'],
        fontSize=14,
        leading=12,
        spaceAfter=11,
        textColor='black',
        bold=True
    )
    summary_style = ParagraphStyle(
        name='Summary',
        parent=styles['Normal'],
        fontSize=12,
        leading=12,
        spaceAfter=11,
        textColor='black'
    )

    story = []

    for page_num, summary in enumerate(summaries, start=1):
        page_text = f"Page {page_num}"
        story.append(Paragraph(page_text, page_style))
        story.append(Paragraph(summary, summary_style))
        story.append(Spacer(1, 10))  # Add some space between summaries

    doc.build(story)
    pdf_file.seek(0)
    with open(output_path, 'wb') as f:
            f.write(pdf_file.getbuffer())

def summarize_deposition_text(text):
    response = chain.invoke({"input": text, "max_tokens": 52000})
    return response.strip()

def summarize_deposition(text_pages, id):
    summaries = [] 

    for page in text_pages:
        if len(page) > 150:  # Only process pages with text length greater than 150 characters
            try:
                summary = summarize_deposition_text(page)
                summaries.append(summary)
            except Exception as e:
                print(f"Error processing page: {e}")
        else:
            print(f"[{id}]: Skipped page of size {len(page)}")
    return summaries

def create_summary(request, id):
    file_path = request.get('file_path', False)
    if not file_path:
        return 0
    
    # grab the file name
    filename = os.path.basename(file_path)
    print("input filename is " + filename)

    # Provide the path to your PDF and the output text file path
    try:
        rawText = extract_text_with_numbers(file_path, None, id)
    except FileNotFoundError:
        return -2
    l = cb.initBot(rawText, id)

    # Split the input text by pages
    text_pages = split_text_by_page(rawText)
    if not settings.TEST_WITHOUT_AI:
        summarizedPages = summarize_deposition(text_pages, id)

        # Write the summaries to the output PDF file
        write_summaries_to_pdf(summarizedPages, f"{settings.SUMMARY_URL}{id}.pdf")
    else:
        write_summaries_to_pdf(text_pages[0], f"{settings.SUMMARY_URL}{id}.pdf")

    print(f"[{id}]: Summary saved to:", f"{settings.SUMMARY_URL}{id}.pdf")
    return l

