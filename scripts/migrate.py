"""Bring an existing database up to date.   Run: python -m scripts.migrate"""
from app.migrations import run_migrations

if __name__ == "__main__":
    print("Applied:", ", ".join(run_migrations()) or "nothing to apply")
