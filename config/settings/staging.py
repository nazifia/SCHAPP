from .base import SPECTACULAR_SETTINGS
from .prod import *  # noqa: F403

# Staging mirrors production but keeps the API docs reachable.
SPECTACULAR_SETTINGS = {**SPECTACULAR_SETTINGS, "SERVE_INCLUDE_SCHEMA": True}
SERVE_API_DOCS = True
