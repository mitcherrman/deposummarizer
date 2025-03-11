from django.contrib.sessions.backends.db import SessionStore as Dbss
from django.conf import settings
from decouple import config
from django.db import connection
import chromadb

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
        try:
            if settings.DEBUG or settings.TEST_WITH_LOCAL_DB:
                client = chromadb.PersistentClient(path=".chroma")
            else:
                db_settings = settings.DATABASES['default']
                client = chromadb.PostgresClient(
                    host=db_settings['HOST'],
                    port=db_settings['PORT'],
                    database=db_settings['NAME'],
                    user=db_settings['USER'],
                    password=db_settings['PASSWORD']
                )
            client.delete_collection(f"collection_{session_key}")
        except:
            pass

    def _rename_chroma_collection(self, old_key, new_key):
        try:
            if settings.DEBUG or settings.TEST_WITH_LOCAL_DB:
                client = chromadb.PersistentClient(path=".chroma")
            else:
                db_settings = settings.DATABASES['default']
                client = chromadb.PostgresClient(
                    host=db_settings['HOST'],
                    port=db_settings['PORT'],
                    database=db_settings['NAME'],
                    user=db_settings['USER'],
                    password=db_settings['PASSWORD']
                )
            # Get the old collection
            old_collection = client.get_collection(f"collection_{old_key}")
            # Create new collection
            new_collection = client.create_collection(f"collection_{new_key}")
            # Copy data
            if old_collection._embedding_function:
                new_collection._embedding_function = old_collection._embedding_function
            # Delete old collection
            client.delete_collection(f"collection_{old_key}")
        except:
            pass