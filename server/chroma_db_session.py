from django.contrib.sessions.backends.db import SessionStore as Dbss
from django.conf import settings
from decouple import config
from django.db import connection
import chromadb
from server import util
import json
import psycopg
from langchain_openai import OpenAIEmbeddings

if (not (settings.DEBUG or settings.TEST_WITH_LOCAL_DB)):
    from langchain_postgres import PGVector

if (not (settings.DEBUG or settings.TEST_WITH_LOCAL_DB)):
    embed_connection = f"postgresql+psycopg://{json.loads(util.get_secret(config('DB_SECRET_ARN')))['username']}:{json.loads(util.get_secret(config('DB_SECRET_ARN')))['password']}@{config('DB_HOST')}/{config('DB_NAME')}"
embedding = OpenAIEmbeddings(model="text-embedding-3-small", api_key=config('OPENAI_KEY'))

class SessionStore(Dbss):
    """
    A session engine that extends the default database engine functionality by managing Chroma collections.
    """
    
    def __init__(self, session_key=None):
        super().__init__(session_key)
    
    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        self._delete_chroma_collection(session_key)
        super().delete(session_key)
    
    async def adelete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        self._delete_chroma_collection(session_key)
        await super().adelete(session_key)

    def cycle_key(self):
        data = self._get_session()
        key = self.session_key
        self.create()
        self._session_cache = data
        if key:
            self._rename_chroma_collection(key, self.session_key)
            super().delete(key)

    async def acycle_key(self):
        data = await self._aget_session()
        key = self.session_key
        await self.acreate()
        self._session_cache = data
        if key:
            self._rename_chroma_collection(key, self.session_key)
            await super().adelete(key)
    
    def clear(self):
        super().clear()
        if self.session_key:
            self._delete_chroma_collection(self.session_key)

    def _delete_chroma_collection(self, session_key):
        collection = f"collection_{session_key}"
        #debug
        try:
            client = chromadb.PersistentClient(path=settings.CHROMA_URL)
            client.delete_collection(collection)
        except:
            pass
        #production
        try:
            vector_store = PGVector(
                connection=embed_connection,
                collection_name=collection,
                embeddings=embedding,
                pre_delete_collection=True
            )
        except:
            pass

    def _rename_chroma_collection(self, old_key, new_key):
        old_collection = f"collection_{old_key}"
        new_collection = f"collection_{new_key}"
        #debug
        try:
            client = chromadb.PersistentClient(path=".chroma")
            # Get the old collection
            old_collection = client.get_collection(old_collection)
            # Create new collection
            new_collection = client.create_collection(new_collection)
            # Copy data
            if old_collection._embedding_function:
                new_collection._embedding_function = old_collection._embedding_function
            # Delete old collection
            client.delete_collection(old_collection)
        except:
            pass
        #production
        try:
            old_vector_store = PGVector(
                connection=embed_connection,
                collection_name=old_collection,
                embeddings=embedding
            )
            new_vector_store = PGVector(
                connection=embed_connection,
                collection_name=new_collection,
                embeddings=embedding
            )
            # Get all embeddings from old collection
            with old_vector_store._make_sync_session() as session:
                old_embeddings = session.query(old_vector_store.EmbeddingStore).all()
                
                # Extract data from old embeddings
                texts = [e.document for e in old_embeddings]
                embeddings = [e.embedding for e in old_embeddings]
                metadatas = [e.cmetadata for e in old_embeddings]
                ids = [e.id for e in old_embeddings]
                
                # Add to new collection
                new_vector_store.add_embeddings(
                    texts=texts,
                    embeddings=embeddings, 
                    metadatas=metadatas,
                    ids=ids
                )
                
            # Delete old collection
            old_vector_store.delete_collection()
        except:
            pass

    @classmethod
    def clear_expired(cls):
        super().clear_expired()
        try:
            connection = psycopg.connect(conninfo=embed_connection)
            with connection.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM langchain_pg_embedding
                    USING django_session, langchain_pg_collection
                    WHERE langchain_pg_embedding.collection_id IN (
                        DELETE FROM langchain_pg_collection
                        USING django_session
                        WHERE trim(LEADING 'collection_' FROM name) NOT IN (
                           SELECT session_key FROM django_session
                        )
                        RETURNING uuid
                    )
                """)
        except:
            pass