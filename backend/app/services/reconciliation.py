"""差分查询与隐私预算的服务端对账领域。

这里是唯一的余额口径来源：执行回执决定已确认扣费，预算流水解释退款、
待确认占用和两页旧口径之间的时间性差额。对账单创建后做不可变快照，
因此重试旧幂等键会返回同一版本，之后发生的查询不会改写历史对账单。
"""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def D(value: Any, default: str = "0") -> Decimal:
    """把接口/种子中的数字安全转成 Decimal，避免浮点累加误差。"""
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return Decimal(default)


def amount_text(value: Decimal) -> str:
    """生成去掉多余尾零的金额文本，同时保留 0.10 这类业务输入可由前端格式化。"""
    text = format(value.quantize(Decimal("0.000001")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def jsonable(value: Any) -> Any:
    """递归把 Decimal 转成 JSON 友好的 float/string 并存结构。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def pick(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    """兼容中英文及常见命名差异，从请求体中取第一个非空字段。"""
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return default


class ReconciliationService:
    """内存对账账本；方法内部加锁，保证确认与重试竞争时不重复入账。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset_scenario()

    # ------------------------------------------------------------------
    # 场景与流水
    # ------------------------------------------------------------------
    def reset_scenario(
        self,
        initial_quota: Any = "1.0",
        deducted: Any = "0.3",
        pending_refund: Any = "0.1",
        pending_hold: Any = "0",
        *,
        budget_no: str = "PRIV-RECON-001",
        dataset_name: str = "对账演示数据集",
    ) -> dict[str, Any]:
        """重置为题设的 1.0 / 0.3 / 0.1 场景，便于 E2E 得到确定结果。"""
        with self._lock:
            self.account: dict[str, Any] = {
                "id": "budget-recon-001",
                "budget_id": "PRIV-RECON-001",
                "预算编号": budget_no,
                "name": dataset_name,
                "数据集名称": dataset_name,
                "initial_quota": D(initial_quota),
                "初始额度": D(initial_quota),
                "currency": "EPS",
            }
            self.transactions: list[dict[str, Any]] = []
            self.receipts: dict[str, dict[str, Any]] = {}
            self.statements: dict[str, dict[str, Any]] = {}
            self.key_to_statement: dict[str, str] = {}
            self.execution_keys: dict[str, str] = {}
            self.seq = 0
            self.statement_seq = 0
            self.revision = 0
            self.difference_transaction_id: str | None = None

            initial = D(initial_quota)
            deducted_amount = D(deducted)
            refund_amount = D(pending_refund)
            hold_amount = D(pending_hold)

            self._add_transaction(
                kind="initial_quota",
                type_name="初始额度",
                amount=initial,
                direction="credit",
                status="settled",
                status_name="已生效",
                description="预算周期开始时授予的初始额度",
            )

            if deducted_amount > 0:
                execution_id = "EXEC-SEED-DEDUCTION-001"
                receipt_id = "RCPT-SEED-001"
                self.receipts[execution_id] = {
                    "id": receipt_id,
                    "receipt_id": receipt_id,
                    "execution_id": execution_id,
                    "query_id": "DIFF-SEED-001",
                    "查询编号": "DIFF-SEED-001",
                    "amount": deducted_amount,
                    "epsilon": deducted_amount,
                    "status": "completed",
                    "status_name": "执行成功回执已确认",
                    "idempotency_key": "seed-deduction-001",
                }
                self._add_transaction(
                    kind="query_charge",
                    type_name="差分查询扣费",
                    amount=deducted_amount,
                    direction="debit",
                    status="settled",
                    status_name="已扣款",
                    execution_id=execution_id,
                    receipt_id=receipt_id,
                    query_id="DIFF-SEED-001",
                    idempotency_key="seed-deduction-001",
                    description="已完成执行的服务端回执扣费",
                )

            if refund_amount > 0:
                txn = self._add_transaction(
                    kind="refund",
                    type_name="退款",
                    amount=refund_amount,
                    direction="credit",
                    status="pending_refund",
                    status_name="待退款",
                    refund_id="REFUND-PENDING-001",
                    description="退款已登记但尚未到账，造成两页旧口径相差该金额",
                    causes_difference=True,
                    difference_reason="待退款被旧预算页提前计入余额，查询回执口径尚未计入",
                )
                self.difference_transaction_id = str(txn["id"])

            if hold_amount > 0:
                self._add_transaction(
                    kind="hold",
                    type_name="待确认占用",
                    amount=hold_amount,
                    direction="debit",
                    status="pending_confirmation",
                    status_name="待确认占用",
                    execution_id="HOLD-SEED-001",
                    description="查询已预占额度，等待执行回执确认；与退款分开列示",
                )

            return self.current_balances()

    def _add_transaction(
        self,
        *,
        kind: str,
        type_name: str,
        amount: Decimal | Any,
        direction: str,
        status: str,
        status_name: str,
        description: str = "",
        execution_id: str | None = None,
        receipt_id: str | None = None,
        query_id: str | None = None,
        idempotency_key: str | None = None,
        refund_id: str | None = None,
        causes_difference: bool = False,
        difference_reason: str = "",
        reverses_txn_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        amount_decimal = D(amount)
        self.seq += 1
        self.revision += 1
        signed = amount_decimal if direction == "credit" else -amount_decimal

        # 待退款在真正退款成功前不进入可用余额；待确认占用则必须冻结。
        if kind == "refund" and status in {"pending_refund", "refunding"}:
            balance_effect = Decimal("0")
            included_in_available = False
        else:
            balance_effect = signed
            included_in_available = True

        txn_id = f"TXN-{self.seq:04d}"
        txn: dict[str, Any] = {
            "id": txn_id,
            "seq": self.seq,
            "flow_no": f"FLOW-{self.seq:04d}",
            "流水号": f"FLOW-{self.seq:04d}",
            "kind": kind,
            "type": kind,
            "type_name": type_name,
            "类型": type_name,
            "direction": direction,
            "direction_name": "收入/退回" if direction == "credit" else "支出/冻结",
            "方向": "收入/退回" if direction == "credit" else "支出/冻结",
            "status": status,
            "status_name": status_name,
            "状态": status_name,
            "amount": signed,
            "signed_amount": signed,
            "amount_text": amount_text(signed),
            "amount_value": amount_decimal,
            "amount_value_text": amount_text(amount_decimal),
            "金额": signed,
            "金额文本": amount_text(signed),
            "金额绝对值": amount_decimal,
            "debit_amount": amount_decimal if direction == "debit" else Decimal("0"),
            "credit_amount": amount_decimal if direction == "credit" else Decimal("0"),
            "借方金额": amount_decimal if direction == "debit" else Decimal("0"),
            "贷方金额": amount_decimal if direction == "credit" else Decimal("0"),
            "balance_effect_amount": balance_effect,
            "余额影响金额": balance_effect,
            "included_in_available_balance": included_in_available,
            "execution_id": execution_id,
            "receipt_id": receipt_id,
            "query_id": query_id,
            "idempotency_key": idempotency_key,
            "refund_id": refund_id,
            "reverses_txn_id": reverses_txn_id,
            "description": description,
            "摘要": description or type_name,
            "causes_difference": causes_difference,
            "is_difference": causes_difference,
            "是否差额流水": causes_difference,
            "difference_amount": amount_decimal if causes_difference else Decimal("0"),
            "差额金额": amount_decimal if causes_difference else Decimal("0"),
            "difference_reason": difference_reason,
            "差额原因": difference_reason,
            "ledger_revision": self.revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            txn.update(extra)
        self.transactions.append(txn)
        return txn

    # ------------------------------------------------------------------
    # 查询执行回执：同一 execution_id / idempotency_key 只扣一次
    # ------------------------------------------------------------------
    def complete_execution(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            execution_id = str(pick(
                payload, "execution_id", "executionId", "receipt_execution_id",
                "执行ID", "执行编号", default="",
            ) or "")
            key = str(pick(
                payload, "idempotency_key", "idempotencyKey", "client_token",
                "request_id", "key", "幂等键", default="",
            ) or "")
            query_id = str(pick(
                payload, "query_id", "queryId", "differential_id",
                "查询编号", "查询ID", default="",
            ) or "")
            amount = pick(
                payload, "amount", "epsilon", "cost", "charged",
                "消耗Epsilon", "扣费金额", "金额", default="0",
            )
            status = str(pick(payload, "status", "receipt_status", default="completed"))
            if status in {"success", "succeeded", "done", "已执行", "成功"}:
                status = "completed"

            # 幂等键优先找回第一次执行；执行回执 ID 是服务端去重的最终约束。
            if key and key in self.execution_keys:
                existing_id = self.execution_keys[key]
                receipt = self.receipts[existing_id]
                return self._execution_response(receipt, replayed=True), True

            if not execution_id:
                execution_id = f"EXEC-{key}" if key else f"EXEC-{self.seq + 1:04d}"

            if execution_id in self.receipts:
                receipt = self.receipts[execution_id]
                if key:
                    self.execution_keys[key] = execution_id
                return self._execution_response(receipt, replayed=True), True

            amount_decimal = D(amount)
            receipt_id = str(pick(payload, "receipt_id", "receiptId", "回执编号",
                                  default=f"RCPT-{execution_id}"))
            receipt: dict[str, Any] = {
                "id": receipt_id,
                "receipt_id": receipt_id,
                "execution_id": execution_id,
                "query_id": query_id or execution_id,
                "查询编号": query_id or execution_id,
                "amount": amount_decimal,
                "epsilon": amount_decimal,
                "消耗Epsilon": amount_decimal,
                "status": status,
                "status_name": "执行成功回执已确认" if status == "completed" else status,
                "idempotency_key": key or execution_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.receipts[execution_id] = receipt
            if key:
                self.execution_keys[key] = execution_id

            txn = self._add_transaction(
                kind="query_charge",
                type_name="差分查询扣费",
                amount=amount_decimal,
                direction="debit",
                status="settled" if status == "completed" else status,
                status_name="已扣款" if status == "completed" else status,
                execution_id=execution_id,
                receipt_id=receipt_id,
                query_id=query_id or execution_id,
                idempotency_key=key or execution_id,
                description="按执行回执确认的唯一扣费；刷新、重试和导出不会再次入账",
            )
            receipt["transaction_id"] = txn["id"]
            response = self._execution_response(receipt, replayed=False)
            response["transaction"] = jsonable(txn)
            return response, False

    def _execution_response(self, receipt: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
        txn_id = receipt.get("transaction_id")
        txn = next((item for item in self.transactions if item.get("id") == txn_id), None)
        return {
            "ok": True,
            "replayed": replayed,
            "duplicate": replayed,
            "duplicate_charge_prevented": replayed,
            "billing_count_for_execution": 1,
            "message": "已找回原执行回执，未重复计费" if replayed else "执行回执已确认并完成一次计费",
            "receipt": jsonable(receipt),
            "execution": jsonable(receipt),
            "transaction_id": txn_id,
            "transaction": jsonable(txn) if txn else None,
            "balances": self.current_balances(),
        }

    # ------------------------------------------------------------------
    # 退款/占用辅助接口
    # ------------------------------------------------------------------
    def add_refund(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            amount = pick(payload, "amount", "refund_amount", "待退金额", "金额", default="0")
            pending = bool(pick(payload, "pending", default=True))
            txn = self._add_transaction(
                kind="refund",
                type_name="退款",
                amount=amount,
                direction="credit",
                status="pending_refund" if pending else "refunded",
                status_name="待退款" if pending else "已退款",
                refund_id=str(pick(payload, "refund_id", "退款单号",
                                   default=f"REFUND-{self.seq + 1:04d}")),
                description=str(pick(payload, "description", "摘要",
                                     default="退款流水，与待确认占用分开呈现")),
                causes_difference=pending,
                difference_reason="待退款尚未到账形成的时间性差额" if pending else "",
            )
            if pending:
                self.difference_transaction_id = txn["id"]
            return {"ok": True, "transaction": jsonable(txn), "balances": self.current_balances()}

    def add_hold(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            amount = pick(payload, "amount", "hold_amount", "待确认占用", "金额", default="0")
            execution_id = str(pick(payload, "execution_id", "执行ID",
                                    default=f"HOLD-{self.seq + 1:04d}"))
            txn = self._add_transaction(
                kind="hold",
                type_name="待确认占用",
                amount=amount,
                direction="debit",
                status="pending_confirmation",
                status_name="待确认占用",
                execution_id=execution_id,
                description="查询确认前的额度冻结；与退款分别呈现",
            )
            return {"ok": True, "transaction": jsonable(txn), "balances": self.current_balances()}

    # ------------------------------------------------------------------
    # 统一余额口径
    # ------------------------------------------------------------------
    def current_balances(self) -> dict[str, Any]:
        with self._lock:
            return jsonable(self._balances_from_lines(self.transactions, list(self.receipts.values())))

    def _balances_from_lines(
        self,
        lines: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        initial = sum((D(x.get("amount_value")) for x in lines if x.get("kind") == "initial_quota"), Decimal("0"))
        settled_charges = sum(
            (D(x.get("amount_value")) for x in lines
             if x.get("kind") == "query_charge" and x.get("status") == "settled"),
            Decimal("0"),
        )
        pending_refund = sum(
            (D(x.get("amount_value")) for x in lines if x.get("status") == "pending_refund"),
            Decimal("0"),
        )
        refunded = sum(
            (D(x.get("amount_value")) for x in lines
             if x.get("kind") == "refund" and x.get("status") in {"refunded", "settled"}),
            Decimal("0"),
        )
        pending_hold = sum(
            (D(x.get("amount_value")) for x in lines
             if x.get("kind") == "hold" and x.get("status") == "pending_confirmation"),
            Decimal("0"),
        )
        receipt_total = sum(
            (D(r.get("amount")) for r in receipts if r.get("status") == "completed"),
            Decimal("0"),
        )

        signed_detail_total = sum((D(x.get("amount")) for x in lines), Decimal("0"))
        available_detail_total = sum(
            (D(x.get("balance_effect_amount")) for x in lines), Decimal("0")
        )
        confirmed_balance = initial - settled_charges
        available_balance = initial - settled_charges - pending_hold
        projected_balance = available_balance + pending_refund + refunded
        # 旧预算页常把“待退款”提前算作可用余额；这正是要定位的时间性差额。
        legacy_budget_balance = initial - settled_charges - pending_hold + pending_refund
        legacy_query_balance = available_balance
        difference = legacy_budget_balance - legacy_query_balance

        execution_counts: dict[str, int] = {}
        for line in lines:
            execution_id = line.get("execution_id")
            if execution_id and line.get("kind") == "query_charge":
                execution_counts[execution_id] = execution_counts.get(execution_id, 0) + 1
        duplicate_executions = {key: count for key, count in execution_counts.items() if count > 1}

        category_totals = {
            "initial_quota": initial,
            "settled_charges": settled_charges,
            "pending_refund": pending_refund,
            "refunded": refunded,
            "pending_hold": pending_hold,
        }
        checks = {
            "available_total_matches_details": available_balance == available_detail_total,
            "signed_total_matches_details": signed_detail_total == (
                initial - settled_charges + pending_refund + refunded - pending_hold
            ),
            "receipts_match_charge_flows": receipt_total == settled_charges,
            "no_duplicate_execution_charge": not duplicate_executions,
        }
        return {
            "initial_quota": initial,
            "初始额度": initial,
            "total_budget": initial,
            "total_quota": initial,
            "deducted": settled_charges,
            "已扣": settled_charges,
            "consumed": settled_charges,
            "已消耗Epsilon": settled_charges,
            "receipt_total": receipt_total,
            "回执扣费合计": receipt_total,
            "settled_charge_total": settled_charges,
            "pending_refund": pending_refund,
            "待退": pending_refund,
            "待退款": pending_refund,
            "refund_pending": pending_refund,
            "refunded": refunded,
            "已退款": refunded,
            "pending_hold": pending_hold,
            "待确认占用": pending_hold,
            "hold_total": pending_hold,
            "confirmed_balance": confirmed_balance,
            "已确认余额": confirmed_balance,
            "available_balance": available_balance,
            "可用余额": available_balance,
            "unified_balance": available_balance,
            "统一余额": available_balance,
            "balance": available_balance,
            "remaining": available_balance,
            "预算剩余": available_balance,
            "projected_balance": projected_balance,
            "退款完成后余额": projected_balance,
            "balance_including_pending_refund": legacy_budget_balance,
            "legacy_query_page_balance": legacy_query_balance,
            "legacy_budget_page_balance": legacy_budget_balance,
            "old_query_balance": legacy_query_balance,
            "old_budget_balance": legacy_budget_balance,
            "difference_amount": difference,
            "差额": difference,
            "balance_difference": difference,
            "gap": difference,
            "signed_detail_total": signed_detail_total,
            "all_details_sum": signed_detail_total,
            "ledger_balance": signed_detail_total,
            "statement_balance": signed_detail_total,
            "reconciliation_balance": signed_detail_total,
            "closing_ledger_balance": signed_detail_total,
            "closing_available_balance": available_balance,
            "明细金额加总": signed_detail_total,
            "available_detail_total": available_detail_total,
            "可用口径明细加总": available_detail_total,
            "category_totals": category_totals,
            "duplicate_executions": duplicate_executions,
            "checks": checks,
            "totals_match_details": all(checks.values()),
            "balanced": all(checks.values()),
        }

    # ------------------------------------------------------------------
    # 对账单版本与历史快照
    # ------------------------------------------------------------------
    def create_statement(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        payload = payload or {}
        with self._lock:
            key = str(pick(
                payload, "idempotency_key", "idempotencyKey", "reconciliation_key",
                "reconciliationKey", "client_token", "request_id", "key",
                "幂等键", "对账键", default="",
            ) or "")
            requested_id = str(pick(
                payload, "statement_id", "statementId", "reconciliation_id",
                "id", "对账单编号", default="",
            ) or "")

            if key and key in self.key_to_statement:
                statement_id = self.key_to_statement[key]
                statement = deepcopy(self.statements[statement_id])
                statement["replayed"] = True
                statement["idempotent_replay"] = True
                statement["duplicate_request"] = True
                statement["message"] = "已通过幂等键找回原对账版本，未重新计算快照"
                return statement, True
            if requested_id and requested_id in self.statements:
                statement = deepcopy(self.statements[requested_id])
                statement["replayed"] = True
                statement["idempotent_replay"] = True
                if key:
                    self.key_to_statement[key] = requested_id
                statement["message"] = "已找回指定历史对账单"
                return statement, True

            self.statement_seq += 1
            statement_id = requested_id or f"REC-{self.statement_seq:04d}"
            # 允许调用方指定一个不冲突的可读编号；冲突时按幂等/历史单返回。
            if statement_id in self.statements:
                statement = deepcopy(self.statements[statement_id])
                statement["replayed"] = True
                statement["idempotent_replay"] = True
                statement["message"] = "已找回同编号历史对账单"
                return statement, True

            lines = deepcopy(self.transactions)
            receipts = [deepcopy(item) for item in self.receipts.values()]
            balances = self._balances_from_lines(lines, receipts)
            difference_id = pick(payload, "focus_transaction_id", "difference_transaction_id",
                                 "focus", "定位流水", default=self.difference_transaction_id)
            focus_id = str(difference_id) if difference_id else None
            if focus_id == "difference":
                focus_id = self.difference_transaction_id
            difference_transaction = next(
                (line for line in lines if line.get("id") == self.difference_transaction_id),
                None,
            )

            line_sum = sum(D(line.get("amount")) for line in lines)
            available_sum = sum(D(line.get("balance_effect_amount")) for line in lines)
            statement: dict[str, Any] = {
                "ok": True,
                "replayed": False,
                "idempotent_replay": False,
                "message": "对账快照已生成",
                "id": statement_id,
                "statement_id": statement_id,
                "reconciliation_id": statement_id,
                "对账单编号": statement_id,
                "version": self.statement_seq,
                "reconciliation_version": self.statement_seq,
                "对账版本": self.statement_seq,
                "idempotency_key": key or None,
                "budget": deepcopy(self.account),
                "budget_id": self.account.get("budget_id"),
                "预算编号": self.account.get("预算编号"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "as_of_revision": self.revision,
                "ledger_revision": self.revision,
                "line_items": lines,
                "transactions": lines,
                "entries": lines,
                "items": lines,
                "流水明细": lines,
                "receipts": receipts,
                "execution_receipts": receipts,
                "执行回执": receipts,
                "summary": balances,
                "totals": balances,
                "balances": balances,
                **{k: v for k, v in balances.items() if k not in {"category_totals", "checks", "duplicate_executions"}},
                "line_count": len(lines),
                "detail_count": len(lines),
                "total_count": len(lines),
                "line_item_sum": line_sum,
                "detail_sum": line_sum,
                "total_amount": line_sum,
                "明细加总": line_sum,
                "available_detail_sum": available_sum,
                "difference_transaction_id": self.difference_transaction_id,
                "difference_transaction": difference_transaction,
                "差额流水ID": self.difference_transaction_id,
                "差额流水": difference_transaction,
                "focus_transaction_id": focus_id,
                "定位流水ID": focus_id,
                "export_url": f"/api/reconciliation/statements/{statement_id}/export",
                "historical": True,
                "immutable": True,
            }
            statement["sum_checks"] = {
                "line_sum_matches_summary": line_sum == D(balances["all_details_sum"]),
                "available_sum_matches_summary": available_sum == D(balances["available_detail_total"]),
                "count_matches_lines": statement["line_count"] == len(lines),
            }
            statement["totals_match_details"] = (
                bool(balances["totals_match_details"])
                and all(statement["sum_checks"].values())
            )
            statement["balanced"] = statement["totals_match_details"]

            statement = jsonable(statement)
            self.statements[statement_id] = deepcopy(statement)
            if key:
                self.key_to_statement[key] = statement_id
            return deepcopy(statement), False

    def get_statement(self, statement_id: str) -> dict[str, Any] | None:
        with self._lock:
            statement = self.statements.get(statement_id)
            return deepcopy(statement) if statement else None

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            statement_id = self.key_to_statement.get(key)
            return deepcopy(self.statements[statement_id]) if statement_id else None

    def list_statements(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [deepcopy(item) for item in self.statements.values()]
            items.sort(key=lambda item: int(item.get("version", 0)), reverse=True)
            return items

    def state(self) -> dict[str, Any]:
        with self._lock:
            balances = self.current_balances()
            statements = self.list_statements()
            latest = statements[0] if statements else None
            return {
                "ok": True,
                "account": deepcopy(self.account),
                "budget": deepcopy(self.account),
                "transactions": deepcopy(self.transactions),
                "流水": deepcopy(self.transactions),
                "receipts": list(deepcopy(self.receipts).values()),
                "balances": balances,
                "summary": balances,
                "statements": [
                    {
                        "id": item["id"],
                        "statement_id": item["statement_id"],
                        "version": item["version"],
                        "created_at": item["created_at"],
                        "difference_amount": item.get("difference_amount"),
                        "totals_match_details": item.get("totals_match_details"),
                        "export_url": item.get("export_url"),
                    }
                    for item in statements
                ],
                "latest_statement": latest,
                "latest_statement_id": latest["id"] if latest else None,
                "revision": self.revision,
            }


reconciliation_service = ReconciliationService()
