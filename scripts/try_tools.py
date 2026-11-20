"""Call each tool by hand and print what it returns.

Run from the project root:   python -m scripts.try_tools
This is exactly what the LLM will see when it calls these tools.
"""
import json

from app.tools import get_buyer_summary, get_overdue_invoices, resolve_buyer


def show(title: str, result: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(result, indent=2)[:1500])   # cut long output so it fits on screen


show("Overdue invoices (top of the list)", get_overdue_invoices())
show("Overdue by 60+ days", get_overdue_invoices(min_days_overdue=60))
show("Buyer summary: 'sharma'", get_buyer_summary("sharma"))
show("Ambiguous name: 'pharma'", resolve_buyer("pharma"))
show("Typo: 'Sharma Tradres'", resolve_buyer("Sharma Tradres"))
