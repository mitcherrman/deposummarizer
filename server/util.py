import os
from decouple import config
from django.conf import settings
from django.http import HttpResponse
from threading import Lock
import boto3, json
from botocore.exceptions import ClientError
from typing import List
import base64

#used to avoid race conditions when modifying sessions outside of views
session_lock = Lock()
            
def get_secret(name, region = "us-east-2"):
    """
    Gets a secret from AWS secrets manager if properly authenticated.
    """
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
    """
    Returns a SQLAlchemy URL to the database in use.
    """
    if settings.DEBUG or settings.TEST_WITH_LOCAL_DB:
        return f"postgresql{'+psycopg' if not psycopgFormat else ''}://postgres:postgres@localhost/postgres"
    return f"postgresql{'+psycopg' if not psycopgFormat else ''}://{json.loads(get_secret(config('DB_SECRET_ARN')))['username']}:{json.loads(get_secret(config('DB_SECRET_ARN')))['password']}@{config('DB_HOST')}/{config('DB_NAME')}"

def get_pgvector_engine_args():
    """
    Returns arguments that should be used in SQLAlchemy engine.
    """
    if settings.DEBUG or settings.TEST_WITH_LOCAL_DB: return None
    return {
        "connect_args": {
            "sslmode": "verify-full",
            "sslrootcert": config('DB_CA_PATH')
        }
    }

def get_encryption_key():
    """
    Returns the encryption key for PGVector encryption.
    """
    key_str = config('PGVECTOR_ENCRYPTION_KEY').decode()
    # Ensure key is exactly 32 bytes for AES-256
    key_bytes = base64.b64decode(key_str)
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b'\0')
    elif len(key_bytes) > 32:
        key_bytes = key_bytes[:32]
    return key_bytes

def params_to_dict(request: HttpResponse, *params: List[str]):
    """
    Gets parameters from an HttpResponse object and converts them to a dict that includes specified params for easy use in templates.
    """
    return {param: request.GET[param] for param in params if param in request.GET}