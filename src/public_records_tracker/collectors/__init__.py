from .contracts_finder import ContractsFinderCollector
from .find_tender import FindTenderCollector
from .listing import HtmlListingCollector
from .modern_gov import ModernGovCollector

COLLECTORS = {
    "html_listing": HtmlListingCollector,
    "contracts_finder": ContractsFinderCollector,
    "find_tender": FindTenderCollector,
    "modern_gov": ModernGovCollector,
}
