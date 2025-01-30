from django.contrib.sessions.backends.db import SessionStore as Dbss
from django.conf import settings
from decouple import config
import os, shutil

class SessionStore(Dbss):
    """
    A session engine that extends the default database engine functionality by storing chroma databases and summary PDFs in the filesystem.
    """

    def __init__(self, session_key=None):
        super().__init__(session_key)
    
    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        if os.path.isdir(f"{settings.CHROMA_URL}{session_key}"):
            shutil.rmtree(f"{settings.CHROMA_URL}{session_key}")
        if os.path.isfile(f"{settings.SUMMARY_URL}{session_key}.pdf"):
            os.remove(f"{settings.SUMMARY_URL}{session_key}.pdf")
        if os.path.isfile(f"{settings.DEPO_URL}{session_key}.pdf"):
            os.remove(f"{settings.DEPO_URL}{session_key}.pdf")
        super().delete(session_key)
    
    async def adelete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        if os.path.isdir(f"{settings.CHROMA_URL}{session_key}"):
            shutil.rmtree(f"{settings.CHROMA_URL}{session_key}")
        if os.path.isfile(f"{settings.SUMMARY_URL}{session_key}.pdf"):
            os.remove(f"{settings.SUMMARY_URL}{session_key}.pdf")
        if os.path.isfile(f"{settings.DEPO_URL}{session_key}.pdf"):
            os.remove(f"{settings.DEPO_URL}{session_key}.pdf")
        await super().adelete(session_key)

    def cycle_key(self):
        data = self._get_session()
        key = self.session_key
        self.create()
        self._session_cache = data
        if key:
            if os.path.isdir(f"{settings.CHROMA_URL}{key}"):
                os.rename(f"{settings.CHROMA_URL}{key}", f"{settings.CHROMA_URL}{self.session_key}")
            if os.path.isfile(f"{settings.SUMMARY_URL}{key}.pdf"):
                os.rename(f"{settings.SUMMARY_URL}{key}.pdf", f"{settings.SUMMARY_URL}{self.session_key}.pdf")
            if os.path.isfile(f"{settings.DEPO_URL}{key}.pdf"):
                os.rename(f"{settings.DEPO_URL}{key}.pdf", f"{settings.DEPO_URL}{self.session_key}.pdf")
            super().delete(key)

    async def acycle_key(self):
        data = await self._aget_session()
        key = self.session_key
        await self.acreate()
        self._session_cache = data
        if key:
            if os.path.isdir(f"{settings.CHROMA_URL}{key}"):
                os.rename(f"{settings.CHROMA_URL}{key}", f"{settings.CHROMA_URL}{self.session_key}")
            if os.path.isfile(f"{settings.SUMMARY_URL}{key}.pdf"):
                os.rename(f"{settings.SUMMARY_URL}{key}.pdf", f"{settings.SUMMARY_URL}{self.session_key}.pdf")
            if os.path.isfile(f"{settings.DEPO_URL}{key}.pdf"):
                os.rename(f"{settings.DEPO_URL}{key}.pdf", f"{settings.DEPO_URL}{self.session_key}.pdf")
            await super().adelete(key)
    
    def clear(self):
        super().clear()
        dirname = settings.CHROMA_URL + self.session_key
        if os.path.isdir(dirname):
            shutil.rmtree(dirname)
        if os.path.isfile(f"{settings.SUMMARY_URL}{self.session_key}.pdf"):
            os.remove(f"{settings.SUMMARY_URL}{self.session_key}.pdf")
        if os.path.isfile(f"{settings.DEPO_URL}{self.session_key}.pdf"):
            os.remove(f"{settings.DEPO_URL}{self.session_key}.pdf")