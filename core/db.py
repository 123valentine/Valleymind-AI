import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ConfigurationError
from core.config import get_config

def get_db():
    uri = get_config().mongodb_uri
    if not uri:
        return None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=500, connectTimeoutMS=500)
        # Force a connection check
        client.admin.command('ping')
        return client.valleymind_db
    except (ConnectionFailure, ConfigurationError, Exception):
        return None
