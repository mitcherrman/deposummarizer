import os
from decouple import config
from django.conf import settings
from threading import Lock
import boto3, json
from botocore.exceptions import ClientError

#used to avoid race conditions when modifying sessions outside of views
session_lock = Lock()

def clearTmp(name = None):
    TMP_URL = config("TMP_URL")
    for file in TMP_URL:
        if not name or name in file:
            os.remove(TMP_URL + file)
            
def get_secret(name, region = "us-east-2"):

    secret_name = name
    region_name = region

    # Create a Secrets Manager client
    session = boto3.session.Session(profile_name="db_access")
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        raise e

    secret = get_secret_value_response['SecretString']
    return secret
    
def get_db_sqlalchemy_url(psycopgFormat=False):
    if settings.DEBUG or settings.TEST_WITH_LOCAL_DB:
        return f"postgresql{'+psycopg' if not psycopgFormat else ''}://postgres:postgres@localhost/postgres"
    return f"postgresql{'+psycopg' if not psycopgFormat else ''}://{json.loads(get_secret(config('DB_SECRET_ARN')))['username']}:{json.loads(get_secret(config('DB_SECRET_ARN')))['password']}@{config('DB_HOST')}/{config('DB_NAME')}"

def get_pgvector_engine_args():
    if settings.DEBUG or settings.TEST_WITH_LOCAL_DB: return None
    return {
        "connect_args": {
            "sslmode": "verify-full",
            "sslrootcert": config('DB_CA_PATH')
        }
    }