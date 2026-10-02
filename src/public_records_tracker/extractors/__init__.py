from .contracts_finder import extract_contracts_finder
from .modern_gov import extract_modern_gov

EXTRACTORS = {
    "contracts_finder": extract_contracts_finder,
    "modern_gov": extract_modern_gov,
}
