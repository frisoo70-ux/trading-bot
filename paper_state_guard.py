"""Safety checks for the legacy PAPER trade-history backup file.

This module is intentionally independent from the LIVE execution engine.
"""
import json


def paper_trade_count(content):
    """Return the number of PAPER trades, or None for invalid/unrecognized data."""
    if content is None:
        return None
    try:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        payload = json.loads(content)
    except (UnicodeDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
        return None
    return len(payload["trades"])


def should_restore_paper_state(local_content, remote_content):
    """Restore only valid remote state that is not less complete by trade count."""
    remote_count = paper_trade_count(remote_content)
    if remote_count is None:
        return False
    local_count = paper_trade_count(local_content)
    if local_count is not None and local_count > remote_count:
        return False
    return True


def should_upload_paper_state(local_content, remote_content):
    """Never upload invalid or shorter local history over a valid remote history."""
    local_count = paper_trade_count(local_content)
    if local_count is None:
        return False
    if remote_content is None:
        return True
    remote_count = paper_trade_count(remote_content)
    if remote_count is None:
        return False
    return local_count >= remote_count
