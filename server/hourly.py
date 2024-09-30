#A script to be run hourly
import os, time, shutil
from decouple import config

#Clear old summary files

CHROMA_URL = config("CHROMA_URL") #path to store chroma vector databases
SUMMARY_URL = config("SUMMARY_URL") #path to store chroma vector databases
DEPO_URL = config("DEPO_URL") #path to store submitted depositions

FILE_EXPIRY_TIME = 86400 #1 day

for file in os.listdir(SUMMARY_URL):
    if os.path.isfile(SUMMARY_URL + file) and time.time() - os.path.getmtime(SUMMARY_URL + file) > FILE_EXPIRY_TIME:
        key = file[:-4]
        os.remove(SUMMARY_URL + file)
        os.remove(DEPO_URL + file)
        shutil.rmtree(CHROMA_URL + key)