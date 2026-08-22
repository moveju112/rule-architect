import os


def get_engine():
    return os.environ.get("DSN")
