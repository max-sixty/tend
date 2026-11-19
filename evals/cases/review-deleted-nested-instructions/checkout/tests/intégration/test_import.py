import pytest
from exporter import import_report


@pytest.mark.integration
def test_import_report_round_trips(database):
    database.insert({"kind": "sale", "amount": 3})
    assert import_report(database) == {"sale": 3}


def test_import_report_empty(database):
    assert import_report(database) == {}
