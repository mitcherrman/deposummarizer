#A script to be run hourly
import os, time, shutil
from decouple import config

#Clear old summary files

CHROMA_URL = config("CHROMA_URL") #path to store chroma vector databases
SUMMARY_URL = config("SUMMARY_URL") #path to store chroma vector databases
DEPO_URL = config("DEPO_URL") #path to store submitted depositions

FILE_EXPIRY_TIME = 60 * 60 * 24 #24 hours

def cleanup():
    #clean up old files
    for dir in os.listdir(CHROMA_URL):
        dir_path = os.path.join(CHROMA_URL, dir)
        if os.path.isdir(dir_path) and time.time() - os.path.getmtime(dir_path) > FILE_EXPIRY_TIME:
            shutil.rmtree(dir_path)

if __name__ == "__main__":
    cleanup()