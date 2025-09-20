from langchain_postgres import PGVector
from Crypto.Cipher import AES
import base64
from langchain_core.documents.base import Document

class PGVectorEncrypt(PGVector):

    def __init__(self, key: bytes, *args, **kwargs):
        self.key = key
        super().__init__(*args, **kwargs)

    def add_embeddings(self, texts, embeddings, metadatas=None, ids=None, **kwargs):
        encrypted_texts = [self._encrypt(text) for text in texts]
        super().add_embeddings(encrypted_texts, embeddings, metadatas, ids, **kwargs)
    
    def aadd_embeddings(self, texts, embeddings, metadatas=None, ids=None, **kwargs):
        encrypted_texts = [self._encrypt(text) for text in texts]
        return super().aadd_embeddings(encrypted_texts, embeddings, metadatas, ids, **kwargs)

    def similarity_search_with_score_by_vector(self, embedding, k=4, filter=None):
        docs_scores_encrypted = super().similarity_search_with_score_by_vector(embedding, k, filter)
        decrypted_docs = [Document(page_content=self._decrypt(doc.page_content), metadata=doc.metadata) for doc, score in docs_scores_encrypted]
        docs_scores = [(doc, docscore[1]) for doc, docscore in zip(decrypted_docs, docs_scores_encrypted)]
        return docs_scores
    
    async def asimilarity_search_with_score_by_vector(self, embedding, k=4, filter=None):
        docs_scores_encrypted = await super().asimilarity_search_with_score_by_vector(embedding, k, filter)
        decrypted_docs = [Document(page_content=self._decrypt(doc.page_content), metadata=doc.metadata) for doc, score in docs_scores_encrypted]
        docs_scores = [(doc, docscore[1]) for doc, docscore in zip(decrypted_docs, docs_scores_encrypted)]
        return docs_scores
    
    #encrypt text using AES-CTR mode, return base64 encoded text
    def _encrypt(self, text: str) -> str:
        cipher = AES.new(self.key, AES.MODE_CTR)
        nonce = cipher.nonce
        encrypted_data = cipher.encrypt(text.encode())
        return f"{base64.b64encode(encrypted_data).decode()};{base64.b64encode(nonce).decode()}"

    #decrypt text using AES-CTR mode, return decrypted text
    def _decrypt(self, text: str) -> str:
        ciphertext, nonce = text.split(";")
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=base64.b64decode(nonce))
        return cipher.decrypt(base64.b64decode(ciphertext)).decode()