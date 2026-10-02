from .contracts_finder import extract_contracts_finder
from .contracts_periods import extract_contract_periods
from .grants import extract_grant_awards
from .housing_ombudsman import extract_housing_ombudsman
from .lgsco import extract_lgsco
from .modern_gov import extract_modern_gov
from .payments import extract_payments


def extract_contracts_bundle(**kwargs) -> int:
    return extract_contracts_finder(**kwargs) + extract_contract_periods(**kwargs)


EXTRACTORS = {
    "contracts_finder": extract_contracts_bundle,
    "grants": extract_grant_awards,
    "housing_ombudsman": extract_housing_ombudsman,
    "lgsco": extract_lgsco,
    "modern_gov": extract_modern_gov,
    "payments": extract_payments,
}
