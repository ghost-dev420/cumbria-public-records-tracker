from .contract_register_csv import extract_contract_register_csv
from .contracts_finder import extract_contracts_finder
from .contracts_periods import extract_contract_periods
from .grants import extract_grant_awards
from .housing_ombudsman import extract_housing_ombudsman
from .lgsco import extract_lgsco
from .modern_gov import extract_modern_gov
from .modern_gov_xml import extract_modern_gov_xml
from .payments import extract_payments
from .payments_xlsx import extract_payments_xlsx


def extract_contracts_bundle(**kwargs) -> int:
    return extract_contracts_finder(**kwargs) + extract_contract_periods(**kwargs)


def extract_payments_bundle(**kwargs) -> int:
    return extract_payments(**kwargs) + extract_payments_xlsx(**kwargs)


EXTRACTORS = {
    "contract_register_csv": extract_contract_register_csv,
    "contracts_finder": extract_contracts_bundle,
    "grants": extract_grant_awards,
    "housing_ombudsman": extract_housing_ombudsman,
    "lgsco": extract_lgsco,
    "modern_gov": extract_modern_gov,
    "modern_gov_xml": extract_modern_gov_xml,
    "payments": extract_payments_bundle,
}
