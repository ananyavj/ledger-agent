"""Central settings for the whole app, read once from the .env file.

Every other module imports values from here instead of calling os.getenv itself,
so there is exactly one place that knows where configuration comes from.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # copies the KEY=value lines from .env into environment variables

POSTGRES_USER = os.getenv("POSTGRES_USER", "collections")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.getenv("POSTGRES_DB", "collections")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5433")

DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

# The "today" the app works with. Our sample data is frozen at 2026-11-15, so the
# demo and the evals use that date. A real deployment would use date.today().
AS_OF_DATE = os.getenv("AS_OF_DATE", "2026-11-15")
