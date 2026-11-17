"""Only ordinary, non-secret media request context is retained."""
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0'


def safe_user_agent(value):
    return value if isinstance(value, str) and 0 < len(value) <= 300 and not any(ord(c) < 32 for c in value) else DEFAULT_USER_AGENT
