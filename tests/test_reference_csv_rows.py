from public_records_tracker.reference import _row_value


def test_row_value_ignores_dictreader_overflow_columns():
    row = {
        "regno": "123456",
        "name": "Example Charity",
        None: ["unexpected", "overflow"],
    }

    assert _row_value(row, "regno") == "123456"
    assert _row_value(row, "name") == "Example Charity"


def test_row_value_handles_named_list_cell_defensively():
    row = {"name": ["Example", "Charity"]}
    assert _row_value(row, "name") == "Example Charity"
