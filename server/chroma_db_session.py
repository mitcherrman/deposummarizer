from django.contrib.sessions.backends.db import SessionStore as Dbss
from django.conf import settings
from langchain_core.vectorstores.base import VectorStore
import os, shutil

class SessionStore(Dbss):

    def __init__(self, session_key=None):
        super().__init__(session_key)
    
    def exists(self, session_key):
        return super().exists(session_key)
    
    def create(self):
        return super().create()
    
    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        if os.path.isdir(settings.CHROMA_URL + session_key):
            shutil.rmtree(settings.CHROMA_URL + session_key)
        super().delete(session_key)
