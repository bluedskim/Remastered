from __future__ import annotations


def format_account_email_display(email: str, max_len: int = 28) -> str:
    """Short label for the header from a login email."""
    text = (email or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    local, sep, domain = text.partition("@")
    if not sep:
        return text[: max_len - 1] + "…"
    if len(local) + len(domain) + 2 <= max_len:
        return f"{local}@{domain}"
    local_budget = max(6, max_len - len(domain) - 2)
    if local_budget >= len(local):
        return f"{local}@{domain[: max_len - len(local) - 1]}…"
    short_local = local[: local_budget - 1] + "…"
    domain_budget = max_len - len(short_local) - 1
    if domain_budget < 4:
        return text[: max_len - 1] + "…"
    return f"{short_local}@{domain[: domain_budget - 1]}…"
