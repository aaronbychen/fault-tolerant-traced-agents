import pytest

from ft2_agent import tools
from ft2_agent.tools import ToolError, lookup_order, run_tool, search_policies


def test_search_finds_current_refund_policy():
    docs = search_policies("Can I return order 1042 after 20 days?")
    assert docs[0]["doc_id"] == "refund-policy"
    assert docs[0]["version"] == "2026.1"
    assert "14 days" in docs[0]["text"]


def test_search_ranks_by_title():
    docs = search_policies("How long does express shipping take?")
    assert docs[0]["doc_id"] == "shipping-policy"


def test_search_skips_archive_by_default():
    docs = search_policies("refund return policy")
    assert all(doc["version"] != "2025.2" for doc in docs)


def test_search_can_point_at_archive(monkeypatch):
    # How the seeded silent failure will work: search reads last year's documents.
    monkeypatch.setattr(tools, "POLICY_DIR", tools.DATA_DIR / "policies_archive")
    docs = search_policies("refund return policy")
    assert docs[0]["version"] == "2025.2"


def test_lookup_order():
    order = lookup_order("1042")
    assert order["delivered_date"] == "2026-10-24"
    assert lookup_order("#1042")["order_id"] == "1042"


def test_lookup_unknown_order():
    with pytest.raises(ToolError, match="not found"):
        lookup_order("9999")


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("delete_order", {"order_id": "1042"}),
        ("lookup_order", {}),
        ("lookup_order", {"order_id": "1042", "force": "true"}),
        ("lookup_order", {"order_id": 1042}),
    ],
)
def test_run_tool_rejects_calls_outside_the_allowlist(name, arguments):
    with pytest.raises(ToolError):
        run_tool(name, arguments)
