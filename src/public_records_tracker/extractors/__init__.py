from .contracts_finder import extract_contracts_finder
from .modern_gov import extract_modern_gov
from .payments import extract_payments

EXTRACTORS = {
    "contracts_finder": extract_contracts_finder,
    "modern_gov": extract_modern_gov,
    "payments": extract_payments,
}
