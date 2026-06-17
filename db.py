import os
import psycopg2
from sqlalchemy import create_engine

_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_connection() -> psycopg2.extensions.connection:
    url = os.environ.get("DATABASE_URL", _DATABASE_URL)
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg2.connect(url)


def get_engine():
    url = os.environ.get("DATABASE_URL", _DATABASE_URL)
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return create_engine(url)
