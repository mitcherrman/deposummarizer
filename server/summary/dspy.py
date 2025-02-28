import fitz  # PyMuPDF
import dspy
from sentence_transformers import SentenceTransformer, util

# Configure DSPy with GPT-4o-mini
dspy.configure(lm=dspy.LM('openai/gpt-4o-mini'))

# Define NLP Similarity Model
model = SentenceTransformer('all-MiniLM-L6-v2')

# Define DSPy Summarization Signature
class SummarizeDeposition(dspy.Signature):
    deposition_text = dspy.InputField()
    summary: str = dspy.OutputField()

summary_program = dspy.Predict(SummarizeDeposition)

def extract_text_from_pdf(pdf_path):
    """Extracts text from a deposition PDF."""
    doc = fitz.open(pdf_path)
    return [page.get_text("text").strip() for page in doc if page.get_text("text").strip()]

# Load deposition PDF
pdf_path = "C:\Users\bobbu\OneDrive\Desktop\Deposition Summarizer\depo original pdfs\Kurt Decker.pdf"  # 🔄 Update this path
deposition_pages = extract_text_from_pdf(pdf_path)

# Auto-generate summaries (Optional: Replace with manual summaries)
trainset = [
    dspy.Example(deposition_text=page_text, summary=summary_program(deposition_text=page_text).summary)
    for page_text in deposition_pages[:10]  # Process first 10 pages
]

# Define NLP-based Metric
def metric_summary_quality(example, pred, trace=None):
    ref_summary = example.summary.strip().lower()
    gen_summary = pred.summary.strip().lower()
    similarity = util.pytorch_cos_sim(model.encode(ref_summary), model.encode(gen_summary)).item()
    return similarity

# Optimize DSPy Model
bootstrap_optimizer = dspy.BootstrapRS(metric=metric_summary_quality, num_threads=12)
summary_program = bootstrap_optimizer.compile(summary_program, trainset=trainset)

mipro_optimizer = dspy.MIPROv2(metric=metric_summary_quality, auto="light", num_threads=24)
optimized_program = mipro_optimizer.compile(summary_program, trainset=trainset)

print("🚀 Optimization complete!")
