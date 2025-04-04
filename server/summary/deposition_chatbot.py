#Based on guide from https://python.langchain.com/v0.2/docs/tutorials/rag/
#Price modeling using openAI - on 500k page document, 0.01 per 4 runs to create embeddings (3-small) for local VDB storage, 0.01 per 16 queries to model (gpt-3.5-turbo)

import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from decouple import config
from threading import Lock
from django.conf import settings
import chromadb
from server import util
import json

if (not (settings.DEBUG or settings.TEST_WITH_LOCAL_DB)):
    from langchain_postgres import PGVector

LOAD_DB_FROM_FOLDER = True
DB_PIECE_SIZE = 1

#load model
model = ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'))
embedding = OpenAIEmbeddings(model="text-embedding-3-small", api_key=config('OPENAI_KEY'))

#thread locks
db_lock = Lock() #used to access chroma database

if (not (settings.DEBUG or settings.TEST_WITH_LOCAL_DB)):
    embed_connection = f"postgresql+psycopg://{json.loads(util.get_secret(config('DB_SECRET_ARN')))['username']}:{json.loads(util.get_secret(config('DB_SECRET_ARN')))['password']}@{config('DB_HOST')}/{config('DB_NAME')}"

def get_chroma_client():
    return chromadb.PersistentClient(path=settings.CHROMA_URL)

def initBot(fullText, id):
    print(f"[{id}]: Document length = {len(fullText)} characters")
    print(f"[{id}]: Setting up model context...")
    
    #split text into chunks
    split = RecursiveCharacterTextSplitter(chunk_size=DB_PIECE_SIZE*1000, chunk_overlap=DB_PIECE_SIZE*200, add_start_index=True)
    pieces = split.split_text(fullText)
    
    #set up chroma with PostgreSQL backend
    collection_name = f"collection_{id}"
    with db_lock:
        if (settings.DEBUG or settings.TEST_WITH_LOCAL_DB):
            client = get_chroma_client()
            
            # Delete collection if it exists
            try:
                client.delete_collection(collection_name)
            except:
                pass
                
            # Create new collection
            collection = client.create_collection(name=collection_name)
            
            # Create Langchain Chroma instance
            vectordb = Chroma(
                client=client,
                collection_name=collection_name,
                embedding_function=embedding
            )
            
            # Add documents
            vectordb.add_texts(pieces)
        else:
            vector_store = PGVector(
                connection=embed_connection,
                collection_name=collection_name,
                embeddings=embedding,
                pre_delete_collection=True
            )

            vector_store.create_collection()
            vector_store.add_texts(pieces)
        
    l = len(pieces)
    print(f"[{id}]: Context creation finished.")
    return l

def askQuestion(question, id, prompt_append, l):
    #set up vectordb retriever
    client = get_chroma_client()
    collection_name = f"collection_{id}"
    retriever = None
    if (settings.DEBUG or settings.TEST_WITH_LOCAL_DB):
        vectordb = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=embedding
        )
        retriever = vectordb.as_retriever(search_type="similarity", search_kwargs={"k":max(6,int(l/32))})
    else:
        vector_store = vector_store = PGVector(
            connection=embed_connection,
            collection_name=collection_name,
            embeddings=embedding
        )
        retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k":max(6,int(l/32))})

    #set up context
    context = combine_text(retriever.invoke(question))
    print(f"[{id}]: Context length = {len(context)} characters")
    
    #set up prompt
    prompt = [
        {"role":"system","content":f"You are an assistant for question-answering tasks. Use the following exerpts from a court deposition to answer the user's question. If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise. Include the exact quote(s) you got the answer from.\nExerpt: {context}"},
    ]
    prompt.extend(prompt_append)
    prompt.append(
        {"role":"user","content":question}
    )
    try:
        result = model.invoke(prompt)
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