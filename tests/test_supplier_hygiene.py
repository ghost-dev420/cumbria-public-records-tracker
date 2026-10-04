from public_records_tracker.supplier_hygiene import (
    classify_noncommercial_payee,
    is_plausible_org_name,
    split_payment_channel,
)


def test_split_known_payment_channel_suffixes():
    assert split_payment_channel("NPOWER LTD (NCR)") == ("NPOWER LTD", "NCR")
    assert split_payment_channel("HMRC (CHAPS)") == ("HMRC", "CHAPS")
    assert split_payment_channel("  Example   Services Ltd  ") == (
        "Example Services Ltd",
        None,
    )


def test_does_not_strip_normal_parenthetical_business_name():
    assert split_payment_channel("Example Services (North) Ltd") == (
        "Example Services (North) Ltd",
        None,
    )


def test_date_cells_are_not_supplier_identities():
    assert split_payment_channel("01/09/2025") == ("", None)
    assert split_payment_channel("2025-09-01") == ("", None)
    assert not is_plausible_org_name("01/09/2025")
    assert not is_plausible_org_name("99999")
    assert is_plausible_org_name("3C Payment Limited")
    assert is_plausible_org_name("Compass Minerals UK Limited")


def test_only_obvious_noncommercial_payees_are_suppressed():
    assert classify_noncommercial_payee("HMRC (CHAPS)") == "tax_or_revenue_authority"
    assert classify_noncommercial_payee("Cumbria County Council") == "public_authority"
    assert classify_noncommercial_payee("99999") == "placeholder_or_accounting_code"
    assert classify_noncommercial_payee("01/09/2025") == "placeholder_or_accounting_code"
    assert classify_noncommercial_payee("National Westminster Bank") is None
    assert classify_noncommercial_payee("Npower Ltd") is None
    assert classify_noncommercial_payee("Cumbria Wildlife Trust") is None
