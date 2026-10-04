from __future__ import annotations

import re


_PAYMENT_CHANNEL_RE = re.compile(r"\s*\((CHAPS|NCR)\)\s*$", re.IGNORECASE)
_PUBLIC_AUTHORITY_ABBREVIATION_RE = re.compile(
    r"(?:^|\s)(?:T\s*\.\s*C\s*\.|P\s*\.\s*C\s*\.)\s*$",
    re.IGNORECASE,
)
_PLACEHOLDER_NAMES = {
    "99999",
    "unknown",
    "not known",
    "not applicable",
    "n a",
    "na",
    "miscellaneous",
    "various",
}
_REVENUE_AUTHORITY_NAMES = {
    "hmrc",
    "hm revenue and customs",
    "hm revenue customs",
    "her majestys revenue and customs",
    "his majestys revenue and customs",
}
_PUBLIC_AUTHORITY_SUFFIXES = (
    " council",
    " combined authority",
    " police and crime commissioner",
    " fire and rescue authority",
)


def split_payment_channel(value: str) -> tuple[str, str | None]:
    """Remove a known payment-rail suffix from a payee name.

    Council payment exports sometimes append the settlement mechanism to the
    payee itself, e.g. ``NPOWER LTD (NCR)`` or ``HMRC (CHAPS)``.  That is
    transaction metadata, not part of the supplier identity.
    """
    raw = " ".join(str(value or "").split()).strip()
    if not raw:
        return "", None
    match = _PAYMENT_CHANNEL_RE.search(raw)
    if not match:
        return raw, None
    cleaned = raw[: match.start()].rstrip(" -–—")
    return cleaned or raw, match.group(1).upper()


def basic_org_key(value: str) -> str:
    """Small dependency-free key used for conservative payee classification."""
    cleaned, _ = split_payment_channel(value)
    cleaned = cleaned.casefold().replace("&", " and ")
    cleaned = re.sub(r"[’'`]", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def classify_noncommercial_payee(value: str) -> str | None:
    """Classify only obvious non-commercial/housekeeping payees.

    This is intentionally narrow.  Banks, utilities, charities and other
    organisations that *might* be legitimate suppliers are not suppressed.
    """
    raw, _ = split_payment_channel(value)
    if _PUBLIC_AUTHORITY_ABBREVIATION_RE.search(raw):
        return "public_authority"

    key = basic_org_key(raw)
    compact = key.replace(" ", "")
    if not key or compact.isdigit() or key in _PLACEHOLDER_NAMES:
        return "placeholder_or_accounting_code"
    if key in _REVENUE_AUTHORITY_NAMES or key.startswith("hm revenue and customs "):
        return "tax_or_revenue_authority"
    if key.endswith(_PUBLIC_AUTHORITY_SUFFIXES):
        return "public_authority"
    return None
