"""差分查询领域服务：字段、状态和动作来自业务配置。

除基类 CRUD 外，本服务把“查询确认”接到服务端对账账本：查询编号/执行 ID
是天然幂等键，重复确认、页面超时重试以及导出都不会产生第二笔扣费。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.reconciliation import D, reconciliation_service
from app.store import store


class DifferentialSqlService(BaseModuleService):
    MODULE = "differential_sql"
    ENTITY = "查询记录"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["查询编号", "查询语句", "查询用户", "消耗Epsilon", "噪声注入量", "返回行数", "执行时间", "预算剩余"]
    REQUIRED_FIELDS = ["查询编号", "查询语句", "查询用户"]
    STATUS_ORDER = ["已执行", "预算不足", "已拒绝", "已审计"]
    ACTION_RULES = {"标记拒绝": "已拒绝", "标记审计": "已审计"}
    NEGATIVE_ACTIONS = []
    CONFIRM_ACTIONS = {"确认查询", "确认计费", "确认回执", "确认"}

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        query_no = str(values.get("查询编号") or values.get("query_id") or "").strip()
        if query_no:
            for row in store.rows(self.MODULE):
                if str(row.get("查询编号", "")) == query_no:
                    # 超时后的同编号重试：找回原记录并确保回执已入账，不新建、不重复扣。
                    self._confirm_entry(row, idempotency_key=f"create:{query_no}")
                    return row, []

        entry, missing = super().create_entry(values)
        if entry is not None and D(entry.get("消耗Epsilon")) > 0:
            self._confirm_entry(entry, idempotency_key=f"create:{query_no or entry.get('id')}")
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action.strip() in self.CONFIRM_ACTIONS:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            result = self._confirm_entry(entry)
            if result.get("replayed"):
                return entry, "已找回查询执行回执，未重复计费"
            return entry, "查询已确认，并按执行回执完成一次计费"
        return super().run_action(entry_id, action)

    def _confirm_entry(
        self,
        entry: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        query_no = str(entry.get("查询编号") or f"DIFF-{entry.get('id')}")
        execution_id = str(entry.get("execution_id") or f"EXEC-{query_no}")
        key = idempotency_key or str(entry.get("idempotency_key") or f"confirm:{query_no}")
        result, _ = reconciliation_service.complete_execution({
            "execution_id": execution_id,
            "idempotency_key": key,
            "receipt_id": f"RCPT-{query_no}",
            "query_id": query_no,
            "query_no": query_no,
            "amount": entry.get("消耗Epsilon") or 0,
            "epsilon": entry.get("消耗Epsilon") or 0,
            "status": "completed" if entry.get("status") in {"已执行", None, ""} else entry.get("status"),
            "query": entry.get("查询语句"),
            "user": entry.get("查询用户"),
        })
        entry["execution_id"] = execution_id
        entry["idempotency_key"] = key
        entry["billing_status"] = "已按执行回执唯一计费"
        entry["计费状态"] = "已按执行回执唯一计费"
        if result.get("receipt", {}).get("status") == "completed":
            entry["pending"] = False
        balances = result.get("balances") or {}
        if balances:
            entry["统一预算余额"] = balances.get("available_balance")
            entry["预算剩余"] = (
                f"统一口径剩余{balances.get('available_balance')}；"
                f"待退{balances.get('pending_refund')}；"
                f"占用{balances.get('pending_hold')}"
            )
        if entry.get("status") in {None, ""}:
            entry["status"] = self.STATUS_ORDER[0]
        return result
