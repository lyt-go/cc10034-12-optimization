"""对账口径、幂等计费和历史版本快照测试。"""
from __future__ import annotations

import pytest

from app.services.reconciliation import reconciliation_service


@pytest.fixture(autouse=True)
def reset_case():
    reconciliation_service.reset_scenario()
    yield
    reconciliation_service.reset_scenario()


def test_scenario_balances_and_detail_sum_match():
    balances = reconciliation_service.current_balances()
    assert balances["initial_quota"] == pytest.approx(1.0)
    assert balances["receipt_total"] == pytest.approx(0.3)
    assert balances["pending_refund"] == pytest.approx(0.1)
    assert balances["pending_hold"] == pytest.approx(0)
    # 待退款尚未到账，不进入可用余额；但作为贷方流水参与带符号明细合计。
    assert balances["available_balance"] == pytest.approx(0.7)
    assert balances["projected_balance"] == pytest.approx(0.8)
    assert balances["difference_amount"] == pytest.approx(0.1)
    assert balances["totals_match_details"] is True

    statement, _ = reconciliation_service.create_statement({"idempotency_key": "case-key"})
    assert statement["line_count"] == 3
    assert statement["all_details_sum"] == pytest.approx(0.8)
    assert statement["available_detail_total"] == pytest.approx(0.7)
    assert statement["difference_transaction_id"] == "TXN-0003"
    assert statement["totals_match_details"] is True


def test_same_execution_is_charged_exactly_once():
    first, replayed = reconciliation_service.complete_execution({
        "execution_id": "EXEC-020",
        "idempotency_key": "exec-020",
        "amount": "0.2",
    })
    assert replayed is False
    assert first["balances"]["receipt_total"] == pytest.approx(0.5)

    second, replayed_again = reconciliation_service.complete_execution({
        "execution_id": "EXEC-020",
        "idempotency_key": "exec-020",
        "amount": "0.2",
    })
    assert replayed_again is True
    assert second["billing_count_for_execution"] == 1
    charges = [
        txn for txn in reconciliation_service.transactions
        if txn.get("execution_id") == "EXEC-020" and txn.get("kind") == "query_charge"
    ]
    assert len(charges) == 1
    balances = reconciliation_service.current_balances()
    assert balances["receipt_total"] == pytest.approx(0.5)
    assert balances["available_balance"] == pytest.approx(0.5)


def test_old_key_returns_old_snapshot_but_new_statement_contains_new_query():
    old, created = reconciliation_service.create_statement({"idempotency_key": "old"})
    assert created is False  # service returns replayed flag; first call flag is False
    assert old["version"] == 1
    assert old["available_balance"] == pytest.approx(0.7)

    reconciliation_service.complete_execution({
        "execution_id": "EXEC-LATE-020",
        "idempotency_key": "late-020",
        "amount": "0.2",
    })

    retried_old, replayed = reconciliation_service.create_statement({"idempotency_key": "old"})
    assert replayed is True
    assert retried_old["replayed"] is True
    assert retried_old["statement_id"] == old["statement_id"]
    assert retried_old["version"] == 1
    assert retried_old["available_balance"] == pytest.approx(0.7)
    assert len(retried_old["line_items"]) == 3

    new, _ = reconciliation_service.create_statement({"idempotency_key": "new"})
    assert new["version"] == 2
    assert new["available_balance"] == pytest.approx(0.5)
    assert new["all_details_sum"] == pytest.approx(0.6)
    assert len(new["line_items"]) == 4
    assert new["totals_match_details"] is True

    fetched = reconciliation_service.get_statement(old["statement_id"])
    assert fetched is not None
    assert fetched["version"] == 1
    assert len(fetched["line_items"]) == 3
