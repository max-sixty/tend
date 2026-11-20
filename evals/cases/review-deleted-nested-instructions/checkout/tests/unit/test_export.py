import json
from pathlib import Path

from exporter import export_report


def test_export_report_writes_totals():
    out = Path("tests/fixtures/out.json")
    export_report([{"kind": "sale", "amount": 3}, {"kind": "sale", "amount": 4}], out)
    assert json.loads(out.read_text()) == {"sale": 7}


def test_export_report_empty():
    out = Path("tests/fixtures/empty.json")
    export_report([], out)
    assert json.loads(out.read_text()) == {}
