import os
from decouple import config

def clearTmp(name = None):
    TMP_URL = config("TMP_URL")
    for file in TMP_URL:
        if not name or name in file:
            os.remove(TMP_URL + file)