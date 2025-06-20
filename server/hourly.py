#A script to be run hourly
import os
from decouple import config

#Clear old summary files

def cleanup():
    #clear sessions
    os.system(f"cd {config('CODE_PATH')}; python3 manage.py clearsessions")

if __name__ == "__main__":
    cleanup()