from .contracts_finder import ContractsFinderCollector
from .find_tender import FindTenderCollector
from .listing import HtmlListingCollector
from .modern_gov import ModernGovCollector
from .modern_gov_browser import ModernGovBrowserCollector
from .modern_gov_xml import ModernGovXmlCollector

COLLECTORS = {
    "html_listing": HtmlListingCollector,
    "contracts_finder": ContractsFinderCollector,
    "find_tender": FindTenderCollector,
    "modern_gov": ModernGovCollector,
    "modern_gov_browser": ModernGovBrowserCollector,
    "modern_gov_xml": ModernGovXmlCollector,
}
