import os
from decouple import config
from threading import Lock

#used to avoid race conditions when modifying sessions outside of views
session_lock = Lock()

def clearTmp(name = None):
    TMP_URL = config("TMP_URL")
    for file in TMP_URL:
        if not name or name in file:
            os.remove(TMP_URL + file)