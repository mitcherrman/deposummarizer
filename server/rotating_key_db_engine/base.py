from django.db.backends.postgresql import base
from server import util
from decouple import config
from django.conf import settings
import json

class DatabaseWrapper(base.DatabaseWrapper):

    def get_connection_params(self):
        conn_params = super().get_connection_params()
        if not (settings.DEBUG | settings.TEST_WITH_LOCAL_DB):
            try:
                secret = json.loads(util.get_secret(config("DB_SECRET_ARN")))
                conn_params['user'] = secret['username']
                conn_params['password'] = secret['password']
            except Exception as e:
                raise Exception("Failed to retrieve most recent connection details", e)
        return conn_params