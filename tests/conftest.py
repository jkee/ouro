"""Root test configuration — load .env so env vars are available during test collection."""
import os

from dotenv import load_dotenv

load_dotenv()

# Fallback for tests when .env is missing or incomplete
os.environ.setdefault("OURO_BRANCH_PREFIX", "test")
