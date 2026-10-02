from public_records_tracker.collectors.contracts_finder import ContractsFinderCollector


def test_extracts_buyer_names_from_buyer_and_parties():
    release = {
        "buyer": {"name": "Cumberland Council"},
        "parties": [
            {"name": "Other", "roles": ["supplier"]},
            {"name": "Home Group Limited", "roles": ["buyer"]},
        ],
    }
    assert ContractsFinderCollector._buyer_names(release) == [
        "Cumberland Council", "Home Group Limited"
    ]
