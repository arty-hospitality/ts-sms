import json

import pytest

ASSIGNMENT = {
    "user_id": "user_123",
    "tenant_id": "tenant_A",
    "org_node_id": "child_node_tenant_A",
    "level": "Child",
    "role": "analyst",
    "data_classification": "CONFIDENTIAL",
    "effective_from": "2026-01-01T00:00:00Z",
    "effective_to": None,
}


def write_assignments(path, records):
    path.write_text(json.dumps({"assignments": records}))
    return str(path)


@pytest.fixture
def assignments(tmp_path, monkeypatch):
    """Configure an assignment store holding one active assignment; returns a writer for overrides."""
    path = tmp_path / "assignments.json"
    monkeypatch.setenv("TS_SMS_ASSIGNMENTS_PATH", write_assignments(path, [ASSIGNMENT]))
    return lambda records: write_assignments(path, records)
