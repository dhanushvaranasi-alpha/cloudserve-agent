import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.persistence.decision_log import DecisionLog


@pytest.fixture
def tmp_decision_log(tmp_path):
    return DecisionLog(path=str(tmp_path / "decisions.db"))


@pytest.fixture
def sample_ticket_email():
    return {
        "ticket_id": "TEST-0001",
        "channel": "email",
        "subject": "Cannot log in to the console",
        "body": "I keep getting an invalid credentials error when I try to sign in.",
        "received_at": "2026-03-14T09:22:00Z",
        "customer_id": "CUST-1042",
        "customer_name": "Test Customer",
        "customer_tier": "business",
        "customer_region": "europe",
        "language_fluency": "fluent",
    }


@pytest.fixture
def sample_documentation(tmp_path):
    docs = [
        {
            "doc_id": "DOC-AUTH-001",
            "title": "Resolving invalid credential errors on login",
            "category": "authentication",
            "applies_to": "Console, CLI, SDK",
            "content": "# Resolving invalid credential errors\n\n## Symptoms\nLogin fails with an invalid credentials message.\n\n## Resolution\n1. Reset your password.\n2. Clear cached tokens.\n3. Retry the login.",
            "related_docs": [],
            "last_reviewed_days_ago": 0,
        }
    ]
    path = tmp_path / "documentation.json"
    path.write_text(json.dumps(docs))
    return str(path)
