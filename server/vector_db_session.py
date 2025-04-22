from django.contrib.sessions.backends.db import SessionStore as Dbss
from django.conf import settings
from decouple import config
from django.db import connection
from server import util
import psycopg
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

embedding = OpenAIEmbeddings(model="text-embedding-3-small", api_key=config('OPENAI_KEY'))

class SessionStore(Dbss):
    """
    A session engine that extends the default database engine functionality by managing PGVector collections.
    """
    
    def __init__(self, session_key=None):
        super().__init__(session_key)
    
    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        self._delete_vector_collection(session_key)
        super().delete(session_key)
    
    async def adelete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        self._delete_vector_collection(session_key)
        await super().adelete(session_key)

    def cycle_key(self):
        data = self._get_session()
        key = self.session_key
        self.create()
        self._session_cache = data
        if key:
            self._rename_vector_collection(key, self.session_key)
            super().delete(key)

    async def acycle_key(self):
        data = await self._aget_session()
        key = self.session_key
        await self.acreate()
        self._session_cache = data
        if key:
            self._rename_vector_collection(key, self.session_key)
            await super().adelete(key)
    
    def clear(self):
        super().clear()
        if self.session_key:
            self._delete_vector_collection(self.session_key)

    def _delete_vector_collection(self, session_key):
        collection = f"collection_{session_key}"
        try:
            vector_store = PGVector(
                connection=util.get_db_sqlalchemy_url(),
                collection_name=collection,
                embeddings=embedding,
                engine_args=util.get_pgvector_engine_args(),
                pre_delete_collection=True
            )
        except:
            pass

    def _rename_vector_collection(self, old_key, new_key):
        old_collection = f"collection_{old_key}"
        new_collection = f"collection_{new_key}"
        try:
            old_vector_store = PGVector(
                connection=util.get_db_sqlalchemy_url(),
                collection_name=old_collection,
                embeddings=embedding,
                engine_args=util.get_pgvector_engine_args()
            )
            new_vector_store = PGVector(
                connection=util.get_db_sqlalchemy_url(),
                collection_name=new_collection,
                embeddings=embedding,
                engine_args=util.get_pgvector_engine_args()
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
            with psycopg.connect(conninfo=util.get_db_sqlalchemy_url(psycopgFormat=True)) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        DELETE FROM langchain_pg_collection
                        USING django_session
                        WHERE trim(LEADING 'collection_' FROM name) NOT IN (
                            SELECT session_key FROM django_session
                        )
                    """)
                    cursor.execute("""
                        DELETE FROM langchain_pg_embedding
                        USING langchain_pg_collection
                        WHERE langchain_pg_embedding.collection_id NOT IN (
                            SELECT uuid FROM langchain_pg_collection
                        )
                    """)
        except:
            pass