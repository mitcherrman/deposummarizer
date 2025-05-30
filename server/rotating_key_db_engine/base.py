from django.db.backends.postgresql import base
from server import util
from decouple import config
from django.conf import settings
from psycopg import OperationalError
import json

class DatabaseWrapper(base.DatabaseWrapper):

    cached_auth = {
        "username": "default",
        "password": "default"
    }

    def get_connection_params(self):
        conn_params = super().get_connection_params()
        if not (settings.DEBUG or settings.TEST_WITH_LOCAL_DB):
            conn_params['user'] = self.cached_auth['username']
            conn_params['password'] = self.cached_auth['password']
        return conn_params
    
    def refresh_conn_cache(self, conn_params=None):
        secret = json.loads(util.get_secret(config("DB_SECRET_ARN")))
        self.cached_auth['username'] = secret['username']
        self.cached_auth['password'] = secret['password']
        if conn_params:
            conn_params['user'] = secret['username']
            conn_params['password'] = secret['password']
    
    def get_new_connection(self, conn_params):
        try:
            return super().get_new_connection(conn_params)
        except OperationalError as e:
            if settings.DEBUG or settings.TEST_WITH_LOCAL_DB:
                raise e
            self.refresh_conn_cache(conn_params)
            return super().get_new_connection(conn_params)
