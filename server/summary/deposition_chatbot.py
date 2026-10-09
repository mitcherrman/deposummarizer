#Based on guide from https://python.langchain.com/v0.2/docs/tutorials/rag/
#Price modeling using openAI - on 500k page document, 0.01 per 4 runs to create embeddings (3-small) for local VDB storage, 0.01 per 16 queries to model (gpt-3.5-turbo)

from langchain_text_splitters import RecursiveCharacterTextSplitter
from threading import Lock
from server import util
from server.PGVector_encrypt.vectorstores import PGVectorEncrypt
from server.summary import ai_clients

LOAD_DB_FROM_FOLDER = True
DB_PIECE_SIZE = 1

#model and embeddings are built on first use (same models and settings as
#before), so importing this module needs no OPENAI_KEY; tests may assign fakes
model = None
embedding = None

def _model():
    return model if model is not None else ai_clients.chatbot_model()

def _embedding():
    return embedding if embedding is not None else ai_clients.chatbot_embeddings()

#thread locks
db_lock = Lock() #used to access chroma database

def initBot(fullText, id, still_current=None):
    """
    Rebuilds collection_<id> from fullText. still_current (optional) is checked
    inside db_lock right before the collection is replaced; if it returns False
    the caller's job is stale and nothing is touched (returns None).
    """
    print(f"[{id}]: Document length = {len(fullText)} characters")
    print(f"[{id}]: Setting up model context...")
    
    #split text into chunks
    split = RecursiveCharacterTextSplitter(chunk_size=DB_PIECE_SIZE*1000, chunk_overlap=DB_PIECE_SIZE*200, add_start_index=True)
    pieces = split.split_text(fullText)
    
    #set up chroma with PostgreSQL backend
    collection_name = f"collection_{id}"
    with db_lock:
        if still_current is not None and not still_current():
            print(f"[{id}]: Stale job, collection left untouched.")
            return None
        vector_store = PGVectorEncrypt(
            key=util.get_encryption_key(),
            connection=util.get_db_sqlalchemy_url(),
            collection_name=collection_name,
            embeddings=_embedding(),
            engine_args=util.get_pgvector_engine_args(),
            pre_delete_collection=True
        )
        vector_store.create_collection()
        vector_store.add_texts(pieces)
        
    l = len(pieces)
    print(f"[{id}]: Context creation finished.")
    return l

def askQuestion(question, id, prompt_append, l):
    #set up vectordb retriever
    collection_name = f"collection_{id}"
    retriever = None
    vector_store = PGVectorEncrypt(
        key=util.get_encryption_key(),
        connection=util.get_db_sqlalchemy_url(),
        collection_name=collection_name,
        embeddings=_embedding(),
        engine_args=util.get_pgvector_engine_args()
    )
    retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k":max(6,int(l/32))})

    #set up context
    context = combine_text(retriever.invoke(question))
    print(f"[{id}]: Context length = {len(context)} characters")
    
    #set up prompt
    prompt = [
        {"role":"system","content":f"""
        You are an assistant for question-answering tasks.
        You will be given excerpts from a court deposition, and you will try to answer the user's question using the information in the excerpts.
        If you don't know the answer, just say that you don't know.
        Use three sentences maximum and keep the answer concise.
        Include the exact quote(s) you got the answer from.
        Excerpt: {context}
        """},
        #{"role":"system","content":f"You are an assistant for question-answering tasks. Use the following exerpts from a court deposition to answer the user's question. If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise. Include the exact quote(s) you got the answer from.\nExerpt: {context}"},
    ]
    prompt.extend(prompt_append)
    prompt.append(
        {"role":"user","content":question}
    )
    try:
        result = _model().invoke(prompt)
    except:
        return None
    parsed_result = result.content
    prompt_append.extend([
        {"role":"user","content":question},
        {"role":"assistant","content":parsed_result}
    ])
    return [parsed_result, prompt_append]

def combine_text(msgs):
    return "\n\n".join(msg.page_content for msg in msgs)