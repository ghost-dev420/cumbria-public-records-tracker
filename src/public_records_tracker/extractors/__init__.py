from .contracts_finder import extract_contracts_finder
from .contracts_periods import extract_contract_periods
from .modern_gov import extract_modern_gov
from .payments import extract_payments


def extract_contracts_bundle(**kwargs) -> int:
    return extract_contracts_finder(**kwargs) + extract_contract_periods(**kwargs)


EXTRACTORS = {
    "contracts_finder": extract_contracts_bundle,
    "modern_gov": extract_modern_gov,
    "payments": extract_payments,
}
