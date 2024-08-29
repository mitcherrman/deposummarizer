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

LOAD_DB_FROM_FOLDER = True
DEBUG_MODE = False
DB_PIECE_SIZE = 1

#load model
model = ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'))
embedding = OpenAIEmbeddings(model="text-embedding-3-small", api_key=config('OPENAI_KEY'))

def initBot(fullText, id):
    print(f"Document length = {len(fullText)} characters")
    file = open("pdf_text.txt","w+t")
    file.write(fullText)
    file.close()
    print("Setting up model context...")
    persist_path = f"databases/vectordb_data_{DB_PIECE_SIZE}k_" + id + "_OpenAI"
    split = RecursiveCharacterTextSplitter(chunk_size = DB_PIECE_SIZE*1000, chunk_overlap = DB_PIECE_SIZE*200, add_start_index = True)
    pieces = split.split_text(fullText)
    #loads database from folder if possible, not used in production but helpful when testing to save on api calls
    if LOAD_DB_FROM_FOLDER and os.path.isdir(persist_path):
        print("Saved vector database found, loading from file...")
        vectordb = Chroma(persist_directory=persist_path, embedding_function=embedding)
    else:
        print("Saved vector database not found/not allowed, building and saving vector database...")
        vectordb = Chroma.from_texts(texts=pieces, embedding=embedding, persist_directory=persist_path)
    l = len(pieces)
    print("Context creation finished.")
    return l

def combine_text(msgs):
    return "\n\n".join(msg.page_content for msg in msgs)

#begin Q/A loop
def askQuestion(question, id, prompt_append, l):
    #set up vectordb retriever
    path = f"databases/vectordb_data_{DB_PIECE_SIZE}k_" + id + "_OpenAI"
    vectordb = Chroma(persist_directory=path, embedding_function=embedding)
    retriever = vectordb.as_retriever(search_type="similarity",search_kwargs={"k":max(6,int(l/32))}) #play with this number
    #set up context
    context = combine_text(retriever.invoke(question))
    print(f"Context length = {len(context)} characters")
    #set up prompt (derived from https://smith.langchain.com/hub/rlm/rag-prompt)
    prompt = [
        {"role":"system","content":f"You are an assistant for question-answering tasks. Use the following exerpts from a court deposition to answer the user's question. If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise. Include the exact quote(s) you got the answer from.\nExerpt: {context}"},
    ]
    prompt.extend(prompt_append)
    prompt.append(
        {"role":"user","content":question}
    )
    result = model.invoke(prompt)
    parsed_result = result.content
    prompt_append.extend([
        {"role":"user","content":question},
        {"role":"assistant","content":parsed_result}
    ])
    return [(result if DEBUG_MODE else parsed_result), prompt_append]