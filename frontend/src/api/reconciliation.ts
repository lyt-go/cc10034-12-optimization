import { request } from './client'

export type JsonRecord = Record<string, any>

async function jsonResponse<T = JsonRecord>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = ''
    try {
      detail = JSON.stringify(await response.json())
    } catch {
      detail = await response.text().catch(() => '')
    }
    throw new Error(detail || `请求失败（${response.status}）`)
  }
  return (await response.json()) as T
}

export async function getReconciliationState(): Promise<JsonRecord> {
  return jsonResponse(await request('/api/reconciliation/state'))
}

export async function setupScenario(values: JsonRecord): Promise<JsonRecord> {
  return jsonResponse(
    await request('/api/reconciliation/setup', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export async function createStatement(
  values: JsonRecord = {},
  timeoutMs = 0,
): Promise<JsonRecord> {
  const controller = timeoutMs > 0 ? new AbortController() : null
  const timer = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : undefined
  try {
    const response = await request('/api/reconciliation/statements', {
      method: 'POST',
      body: JSON.stringify(values),
      signal: controller?.signal,
    })
    return await jsonResponse(response)
  } finally {
    if (timer) window.clearTimeout(timer)
  }
}

export async function getStatement(statementId: string): Promise<JsonRecord> {
  return jsonResponse(
    await request(`/api/reconciliation/statements/${encodeURIComponent(statementId)}`),
  )
}

export async function getStatementByKey(key: string): Promise<JsonRecord> {
  return jsonResponse(
    await request(`/api/reconciliation/statements/key/${encodeURIComponent(key)}`),
  )
}

export async function listStatements(): Promise<{ items: JsonRecord[] }> {
  return jsonResponse(await request('/api/reconciliation/statements'))
}

export async function completeExecution(values: JsonRecord): Promise<JsonRecord> {
  return jsonResponse(
    await request('/api/reconciliation/executions', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export async function addRefund(values: JsonRecord): Promise<JsonRecord> {
  return jsonResponse(
    await request('/api/reconciliation/refunds', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export async function addHold(values: JsonRecord): Promise<JsonRecord> {
  return jsonResponse(
    await request('/api/reconciliation/holds', {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  )
}

export function statementExportUrl(statementId: string, format = 'csv'): string {
  return `/api/reconciliation/statements/${encodeURIComponent(statementId)}/export?format=${format}`
}

export async function downloadStatement(statementId: string): Promise<void> {
  const response = await request(statementExportUrl(statementId))
  if (!response.ok) throw new Error('对账单导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${statementId}.csv`
  anchor.dataset.testid = 'download-statement-anchor'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
