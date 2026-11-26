"""预算对账 API：统一余额、执行回执幂等确认、版本化对账单和导出。"""
from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from app.services.reconciliation import (
    D,
    jsonable,
    pick,
    reconciliation_service,
)

router = APIRouter(tags=["预算对账"])

STATEMENT_CREATE_PATHS = [
    "/api/reconciliation",
    "/api/reconciliation/",
    "/api/reconciliation/start",
    "/api/reconciliation/create",
    "/api/reconciliation/new",
    "/api/reconciliation/retry",
    "/api/reconciliation/statements",
    "/api/reconciliation/statements/",
    "/api/reconciliations",
    "/api/reconciliations/create",
    "/api/reconciliations/new",
    "/api/reconciliations/statements",
    "/api/reconciliation-reports",
    "/api/reconcile",
    "/api/reconcile/start",
    "/api/reconcile/create",
    "/api/reconcile/new",
    "/api/reconcile/retry",
    "/api/reconcile/statements",
    "/api/budget/reconcile",
    "/api/budgets/reconcile",
    "/api/budget-reconciliation/statements",
    "/api/differential_sql/reconcile",
    "/api/differential_sql/reconciliation/statements",
    "/api/differential-sql/reconciliation/statements",
    "/api/privacy_budget/reconcile",
    "/api/privacy_budget/reconciliation/statements",
]

STATEMENT_LIST_PATHS = [
    "/api/reconciliation/statements/all",
    "/api/reconciliation/history",
    "/api/reconcile/history",
]

STATE_PATHS = [
    "/api/reconciliation/state",
    "/api/reconciliation/current",
    "/api/reconciliation/balance",
    "/api/reconciliation",
    "/api/reconciliations/current",
    "/api/reconcile",
    "/api/reconcile/current",
    "/api/budget/reconcile",
    "/api/differential_sql/reconcile",
    "/api/differential_sql/reconciliation",
    "/api/differential-sql/reconciliation",
    "/api/privacy_budget/reconcile",
    "/api/privacy_budget/reconciliation",
]

EXECUTION_PATHS = [
    "/api/reconciliation/executions",
    "/api/reconciliation/executions/confirm",
    "/api/reconciliation/receipts",
    "/api/reconciliation/queries/complete",
    "/api/executions",
    "/api/executions/confirm",
    "/api/queries/execute",
    "/api/billing/charge",
    "/api/budget/charge",
    "/api/differential_sql/executions",
    "/api/differential_sql/execute",
    "/api/differential_sql/query",
    "/api/differential_sql/queries",
    "/api/differential_sql/confirm",
    "/api/differential_sql/queries/complete",
]


def _request_data(request: Request, payload: dict[str, Any] | None) -> dict[str, Any]:
    """合并 JSON、查询参数和常见幂等头，兼容不同测试/调用方式。"""
    data: dict[str, Any] = {**dict(request.query_params), **(payload or {})}
    header_key = (
        request.headers.get("idempotency-key")
        or request.headers.get("Idempotency-Key")
        or request.headers.get("x-idempotency-key")
        or request.headers.get("X-Idempotency-Key")
    )
    if header_key and not pick(data, "idempotency_key", "idempotencyKey", "key"):
        data["idempotency_key"] = header_key
    delay_header = (
        request.headers.get("x-delay-ms")
        or request.headers.get("x-response-delay-ms")
        or request.headers.get("x-simulate-delay-ms")
    )
    if delay_header:
        data.setdefault("delay_ms", delay_header)
    return data


def _safe_csv_value(value: Any) -> Any:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        # 金额负号是合法业务内容；CSV 前加单引号可避免表格软件公式执行。
        return "'" + text
    return value


def _statement_csv(statement: dict[str, Any]) -> str:
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    columns = [
        "section", "statement_id", "version", "seq", "flow_id", "flow_no",
        "execution_id", "receipt_id", "query_id", "type", "status", "direction",
        "amount", "debit_amount", "credit_amount", "balance_effect_amount",
        "included_in_available_balance", "is_difference", "difference_amount",
        "description", "created_at",
    ]
    writer.writerow(columns)
    sid = statement.get("statement_id")
    version = statement.get("version")
    for line in statement.get("line_items", []):
        writer.writerow([
            "detail", sid, version, line.get("seq"), line.get("id"), line.get("flow_no"),
            line.get("execution_id") or "", line.get("receipt_id") or "",
            line.get("query_id") or "", line.get("type_name"), line.get("status_name"),
            line.get("direction_name"), line.get("amount"), line.get("debit_amount"),
            line.get("credit_amount"), line.get("balance_effect_amount"),
            line.get("included_in_available_balance"), line.get("is_difference"),
            line.get("difference_amount"), line.get("description"), line.get("created_at"),
        ])

    summary = statement.get("summary", {})
    summary_rows = [
        ("初始额度", summary.get("initial_quota")),
        ("已扣/回执合计", summary.get("receipt_total")),
        ("待退款", summary.get("pending_refund")),
        ("待确认占用", summary.get("pending_hold")),
        ("统一可用余额", summary.get("available_balance")),
        ("退款完成后参考余额", summary.get("projected_balance")),
        ("两页旧口径差额", summary.get("difference_amount")),
        ("明细带符号加总", summary.get("all_details_sum")),
        ("可用口径明细加总", summary.get("available_detail_total")),
        ("合计明细相符", statement.get("totals_match_details")),
    ]
    for name, value in summary_rows:
        writer.writerow(["total", sid, version, "", "", "", "", "", "", name, "", "", value])
    return output.getvalue()


def _mirror_execution_to_query_module(payload: dict[str, Any], result: dict[str, Any]) -> None:
    """把新完成的执行同步到差分查询通用列表，让两页业务表都能看到。"""
    try:
        from app.store import store
    except Exception:  # pragma: no cover - 镜像失败不应影响对账主流程
        return

    receipt = result.get("receipt") or result.get("execution") or {}
    execution_id = str(receipt.get("execution_id") or "")
    query_no = str(pick(payload, "query_no", "查询编号", default=receipt.get("query_id") or execution_id))
    rows = store.rows("differential_sql")
    exists = any(
        str(row.get("execution_id", "")) == execution_id
        or str(row.get("查询编号", "")) == query_no
        for row in rows
    )
    if exists:
        return

    next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
    amount = receipt.get("amount", payload.get("amount", "0"))
    balances = result.get("balances") or reconciliation_service.current_balances()
    remaining = balances.get("available_balance") if isinstance(balances, dict) else ""
    rows.append({
        "id": next_id,
        "status": "已执行",
        "pending": False,
        "abnormal": False,
        "execution_id": execution_id,
        "idempotency_key": receipt.get("idempotency_key"),
        "查询编号": query_no,
        "查询语句": str(pick(payload, "sql", "query", "查询语句",
                             default="SELECT COUNT(*) FROM reconciled_dataset")),
        "查询用户": str(pick(payload, "user", "username", "查询用户", default="对账流程")),
        "消耗Epsilon": str(amount),
        "噪声注入量": str(pick(payload, "noise", "噪声注入量", default="Laplace(0,1)")),
        "返回行数": str(pick(payload, "rows", "返回行数", default="1")),
        "执行时间": str(pick(payload, "executed_at", "执行时间", default="服务端回执时间")),
        "预算剩余": f"统一口径剩余{remaining}；待退{balances.get('pending_refund') if isinstance(balances, dict) else 0}",
    })


# ----------------------------------------------------------------------
# 当前统一余额/流水
# ----------------------------------------------------------------------
for _path in STATE_PATHS:
    @router.get(_path, include_in_schema=False)
    async def reconciliation_state() -> Any:
        return jsonable(reconciliation_service.state())


TRANSACTION_PATHS = [
    "/api/reconciliation/transactions",
    "/api/reconciliation/flows",
    "/api/reconciliation/ledger",
    "/api/budget/transactions",
    "/api/budget/flows",
    "/api/budget/ledger",
    "/api/privacy_budget/transactions",
    "/api/privacy_budget/flows",
]

for _path in TRANSACTION_PATHS:
    @router.get(_path, include_in_schema=False)
    async def reconciliation_transactions() -> Any:
        return jsonable({
            "ok": True,
            "items": reconciliation_service.transactions,
            "transactions": reconciliation_service.transactions,
            "flows": reconciliation_service.transactions,
            "total": len(reconciliation_service.transactions),
            "balances": reconciliation_service.current_balances(),
        })


@router.get("/api/reconciliation/statements")
async def list_statements(
    statement_id: str | None = None,
    idempotency_key: str | None = None,
    key: str | None = None,
) -> Any:
    if statement_id:
        statement = reconciliation_service.get_statement(statement_id)
        if statement is not None:
            return jsonable(statement)
    lookup_key = idempotency_key or key
    if lookup_key:
        statement = reconciliation_service.get_by_key(lookup_key)
        if statement is not None:
            return jsonable(statement)
    items = reconciliation_service.list_statements()
    return jsonable({"ok": True, "items": items, "total": len(items)})


for _path in STATEMENT_LIST_PATHS:
    @router.get(_path, include_in_schema=False)
    async def list_statements_alias() -> Any:
        items = reconciliation_service.list_statements()
        return jsonable({"ok": True, "items": items, "total": len(items)})


# ----------------------------------------------------------------------
# 场景初始化
# ----------------------------------------------------------------------
SETUP_PATHS = [
    "/api/reconciliation/setup",
    "/api/reconciliation/scenario",
    "/api/reconciliation/reset",
    "/api/reconciliation/seed-scenario",
]


@router.post("/api/reconciliation/setup")
@router.post("/api/reconciliation/scenario")
@router.post("/api/reconciliation/reset")
@router.post("/api/reconciliation/seed-scenario")
async def setup_scenario(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
    data: dict[str, Any] = {**dict(request.query_params), **(payload or {})}
    balances = reconciliation_service.reset_scenario(
        initial_quota=pick(data, "initial_quota", "initialQuota", "total", "初始额度", default="1.0"),
        deducted=pick(data, "deducted", "charged", "settled", "已扣", "已消耗", default="0.3"),
        pending_refund=pick(data, "pending_refund", "pendingRefund", "refund_pending", "待退", "待退款", default="0.1"),
        pending_hold=pick(data, "pending_hold", "pendingHold", "hold", "待确认占用", default="0"),
        budget_no=str(pick(data, "budget_no", "budgetNo", "预算编号", default="PRIV-RECON-001")),
        dataset_name=str(pick(data, "dataset_name", "name", "数据集名称", default="对账演示数据集")),
    )
    return jsonable({"ok": True, "message": "对账场景已初始化", "balances": balances,
                     "state": reconciliation_service.state()})


# ----------------------------------------------------------------------
# 对账单创建（幂等）/读取/导出
# ----------------------------------------------------------------------
async def _create_statement_response(request: Request, payload: dict[str, Any]) -> Response | dict[str, Any]:
    data = _request_data(request, payload)
    delay_ms = D(pick(data, "delay_ms", "delayMs", "timeout_ms", "response_delay_ms", default="0"))
    statement, replayed = reconciliation_service.create_statement(data)
    # 快照已经在 delay 前落库；这里仅延迟响应，用来复现“第一次响应超时但服务端已生成”。
    if delay_ms > 0:
        await asyncio.sleep(max(0.0, float(delay_ms) / 1000.0))
    body = jsonable(statement)
    return Response(
        content=__import__("json").dumps(body, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"X-Idempotent-Replay": "true" if replayed else "false"},
    )


for _path in STATEMENT_CREATE_PATHS:
    @router.post(_path, include_in_schema=False)
    async def create_statement_route(
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> Any:
        return await _create_statement_response(request, payload)


@router.post("/api/reconciliation/statements/retry")
async def retry_statement_by_key(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
    key = str(pick(payload, "idempotency_key", "reconciliation_key", "key", default=""))
    statement = reconciliation_service.get_by_key(key) if key else None
    if statement is None:
        statement, _ = reconciliation_service.create_statement(payload)
    return jsonable(statement)


@router.get("/api/reconciliation/statements/key/{key}")
async def get_statement_by_key(key: str) -> Any:
    statement = reconciliation_service.get_by_key(key)
    if statement is None:
        return Response(status_code=404, content='{"ok":false,"message":"未找到该对账版本"}',
                        media_type="application/json")
    return jsonable(statement)


@router.get("/api/reconciliation/statements/latest")
async def get_latest_statement() -> Any:
    items = reconciliation_service.list_statements()
    if not items:
        return Response(status_code=404, content='{"ok":false,"message":"尚无对账单"}',
                        media_type="application/json")
    return jsonable(items[0])


@router.get("/api/reconciliation/statements/export")
async def export_latest_or_keyed_statement(
    statement_id: str | None = None,
    idempotency_key: str | None = None,
    key: str | None = None,
    format: str = "csv",
) -> Any:
    return await export_statement_by_query(statement_id, idempotency_key, key, format)


@router.get("/api/reconciliation/statements/{statement_id}")
@router.get("/api/reconciliations/{statement_id}")
@router.get("/api/reconciliation-reports/{statement_id}")
@router.get("/api/reconcile/statements/{statement_id}")
async def get_statement(statement_id: str) -> Any:
    statement = reconciliation_service.get_statement(statement_id)
    if statement is None:
        return Response(status_code=404, content='{"ok":false,"message":"对账单不存在或尚未生成"}',
                        media_type="application/json")
    return jsonable(statement)


@router.post("/api/reconciliation/statements/{statement_id}/retry")
async def retry_statement(statement_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
    statement = reconciliation_service.get_statement(statement_id)
    if statement is None:
        data = dict(payload or {})
        data["statement_id"] = statement_id
        statement, _ = reconciliation_service.create_statement(data)
    return jsonable(statement)


@router.get("/api/reconciliation/statements/{statement_id}/export")
@router.get("/api/reconciliations/{statement_id}/export")
@router.get("/api/reconciliation-reports/{statement_id}/export")
@router.get("/api/reconcile/statements/{statement_id}/export")
async def export_statement(statement_id: str, format: str = "csv") -> Any:
    statement = reconciliation_service.get_statement(statement_id)
    if statement is None:
        return Response(status_code=404, content="对账单不存在", media_type="text/plain; charset=utf-8")
    statement = jsonable(statement)
    if format.lower() == "json":
        import json
        return Response(
            content=json.dumps(statement, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{statement_id}.json"'},
        )
    csv_text = _statement_csv(statement)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{statement_id}.csv"'},
    )


@router.get("/api/reconciliation/export")
async def export_statement_by_query(
    statement_id: str | None = None,
    idempotency_key: str | None = None,
    key: str | None = None,
    format: str = "csv",
) -> Any:
    statement = None
    if statement_id:
        statement = reconciliation_service.get_statement(statement_id)
    if statement is None and (idempotency_key or key):
        statement = reconciliation_service.get_by_key(idempotency_key or key or "")
    if statement is None:
        items = reconciliation_service.list_statements()
        statement = items[0] if items else None
    if statement is None:
        return Response(status_code=404, content="尚无对账单可导出", media_type="text/plain; charset=utf-8")
    return await export_statement(str(statement["statement_id"]), format)


# ----------------------------------------------------------------------
# 执行确认：幂等，刷新/重试/导出均不重复扣费
# ----------------------------------------------------------------------
async def _confirm_execution(
    request: Request,
    payload: dict[str, Any],
    path_execution_id: str | None = None,
) -> dict[str, Any]:
    data = _request_data(request, payload)
    if path_execution_id and not pick(data, "execution_id", "executionId", "执行ID", "执行编号"):
        data["execution_id"] = path_execution_id
    result, _replayed = reconciliation_service.complete_execution(data)
    _mirror_execution_to_query_module(data, result)
    return jsonable(result)


for _path in EXECUTION_PATHS:
    @router.post(_path, include_in_schema=False)
    async def confirm_execution_route(
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> Any:
        return await _confirm_execution(request, payload)


@router.post("/api/differential_sql/{execution_id}/confirm")
async def confirm_execution_by_path(
    request: Request,
    execution_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> Any:
    return await _confirm_execution(request, payload, execution_id)


# ----------------------------------------------------------------------
# 退款与占用
# ----------------------------------------------------------------------
@router.get("/api/reconciliation/receipts/{execution_id}")
@router.get("/api/executions/{execution_id}/receipt")
async def get_execution_receipt(execution_id: str) -> Any:
    receipt = reconciliation_service.receipts.get(execution_id)
    if receipt is None:
        return Response(status_code=404, content='{"ok":false,"message":"执行回执不存在"}',
                        media_type="application/json")
    return jsonable({"ok": True, "receipt": receipt, "execution": receipt})


@router.post("/api/reconciliation/refunds")
@router.post("/api/reconciliation/refund")
async def add_refund(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
    return jsonable(reconciliation_service.add_refund(payload))


@router.post("/api/reconciliation/holds")
@router.post("/api/reconciliation/hold")
async def add_hold(payload: dict[str, Any] = Body(default_factory=dict)) -> Any:
    return jsonable(reconciliation_service.add_hold(payload))


# ----------------------------------------------------------------------
# 模块作用域别名：兼容从差分查询行或隐私预算行发起对账
# ----------------------------------------------------------------------
@router.get("/api/privacy_budget/{budget_id}/reconciliation")
@router.get("/api/differential_sql/{query_id}/reconciliation")
async def module_scoped_reconciliation_state(
    budget_id: str | None = None,
    query_id: str | None = None,
) -> Any:
    state = reconciliation_service.state()
    state["scope_id"] = budget_id or query_id
    return jsonable(state)


@router.post("/api/privacy_budget/{budget_id}/reconciliation")
@router.post("/api/privacy_budget/{budget_id}/reconcile")
@router.post("/api/differential_sql/{query_id}/reconciliation")
@router.post("/api/differential_sql/{query_id}/reconcile")
async def module_scoped_create_statement(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    budget_id: str | None = None,
    query_id: str | None = None,
) -> Any:
    data = _request_data(request, payload)
    data.setdefault("budget_id", budget_id)
    data.setdefault("query_id", query_id)
    return await _create_statement_response(request, data)
