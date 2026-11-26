<template>
  <section class="recon-panel" data-testid="reconciliation-panel" :data-module="moduleKey">
    <div class="recon-head">
      <div>
        <h3>预算对账工作台</h3>
        <p>服务端按执行回执与预算流水统一计算；刷新、重试和导出不会重复计费。</p>
      </div>
      <RouterLink
        v-if="statement"
        class="btn ghost"
        :to="`/reconciliation/${statement.statement_id}?focus=difference`"
        data-testid="open-standalone-statement"
      >
        独立页/可回查链接
      </RouterLink>
    </div>

    <div
      v-if="state?.balances"
      class="recon-balances"
      data-testid="unified-budget-balance"
      :data-available="Number(state.balances.available_balance)"
      :data-pending-refund="Number(state.balances.pending_refund)"
      :data-pending-hold="Number(state.balances.pending_hold)"
    >
      <article class="recon-balance" data-testid="balance-initial">
        <span>初始额度</span>
        <strong>{{ fmt(state.balances.initial_quota) }}</strong>
      </article>
      <article class="recon-balance" data-testid="balance-deducted">
        <span>已扣/回执合计</span>
        <strong>{{ fmt(state.balances.receipt_total) }}</strong>
      </article>
      <article class="recon-balance refund" data-testid="balance-pending-refund">
        <span>待退款（单列）</span>
        <strong>{{ fmt(state.balances.pending_refund) }}</strong>
      </article>
      <article class="recon-balance hold" data-testid="balance-pending-hold">
        <span>待确认占用（单列）</span>
        <strong>{{ fmt(state.balances.pending_hold) }}</strong>
      </article>
      <article class="recon-balance available" data-testid="balance-available">
        <span>统一可用余额</span>
        <strong>{{ fmt(state.balances.available_balance) }}</strong>
      </article>
      <article class="recon-balance projected" data-testid="balance-projected">
        <span>退款完成后参考</span>
        <strong>{{ fmt(state.balances.projected_balance) }}</strong>
      </article>
    </div>

    <form class="recon-setup" data-testid="reconciliation-setup" @submit.prevent="applyScenario">
      <label>
        <span>初始额度</span>
        <input v-model="form.initial_quota" data-testid="input-initial-quota" inputmode="decimal" />
      </label>
      <label>
        <span>已扣</span>
        <input v-model="form.deducted" data-testid="input-deducted" inputmode="decimal" />
      </label>
      <label>
        <span>待退</span>
        <input v-model="form.pending_refund" data-testid="input-pending-refund" inputmode="decimal" />
      </label>
      <label>
        <span>待确认占用</span>
        <input v-model="form.pending_hold" data-testid="input-pending-hold" inputmode="decimal" />
      </label>
      <button class="btn" type="submit" data-testid="setup-scenario" :disabled="busy">
        载入对账场景
      </button>
      <label class="timeout-toggle">
        <input v-model="simulateTimeout" type="checkbox" data-testid="simulate-timeout" />
        <span>首次响应超时（800ms 前端超时，服务端保留快照）</span>
      </label>
    </form>

    <div class="recon-extra" data-testid="pending-flows">
      <label>
        <span>登记待退款</span>
        <input v-model="refundAmount" data-testid="input-add-refund" inputmode="decimal" placeholder="例如 0.05" />
      </label>
      <button class="btn" type="button" data-testid="add-refund" :disabled="busy" @click="registerRefund">
        添加退款流水
      </button>
      <label>
        <span>登记待确认占用</span>
        <input v-model="holdAmount" data-testid="input-add-hold" inputmode="decimal" placeholder="例如 0.02" />
      </label>
      <button class="btn" type="button" data-testid="add-hold" :disabled="busy" @click="registerHold">
        添加占用流水
      </button>
    </div>

    <div class="recon-actions">
      <button
        class="btn primary"
        type="button"
        id="reconcile-submit"
        data-testid="create-reconciliation"
        :disabled="busy"
        @click="startReconciliation"
      >
        发起对账
      </button>
      <button
        class="btn"
        type="button"
        data-testid="retry-reconciliation"
        :disabled="busy || !activeKey"
        @click="retryOldReconciliation"
      >
        重试本次旧对账
      </button>
      <button
        class="btn"
        type="button"
        data-testid="new-reconciliation"
        :disabled="busy"
        @click="createNewReconciliation"
      >
        发起新对账
      </button>
      <button
        class="btn"
        type="button"
        data-testid="complete-query-02"
        :disabled="busy"
        @click="completeScenarioQuery"
      >
        其间完成一笔 0.2 查询
      </button>
      <button
        class="btn"
        type="button"
        data-testid="export-reconciliation"
        id="export-statement"
        :disabled="busy || !statement"
        @click="exportCurrent"
      >
        导出当前对账单
      </button>
      <button
        class="btn ghost"
        type="button"
        data-testid="locate-difference"
        :disabled="!statement"
        @click="locateDifference"
      >
        定位差额流水
      </button>
    </div>

    <p v-if="activeKey" class="recon-key" data-testid="active-reconciliation-key">
      当前对账幂等键：<code>{{ activeKey }}</code>
    </p>
    <p v-if="notice" class="recon-notice" data-testid="reconciliation-notice">{{ notice }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="reconciliation-error">{{ errorMessage }}</p>

    <div v-if="history.length" class="recon-history" data-testid="reconciliation-history">
      <span>历史版本：</span>
      <button
        v-for="item in history"
        :key="item.statement_id || item.id"
        class="link history-link"
        type="button"
        :data-testid="`history-statement-${item.statement_id || item.id}`"
        @click="viewStatement(String(item.statement_id || item.id))"
      >
        {{ item.statement_id || item.id }} / v{{ item.version }}
      </button>
      <button
        v-for="item in history"
        :key="`export-${item.statement_id || item.id}`"
        class="btn ghost history-export"
        type="button"
        :data-testid="`export-history-${item.statement_id || item.id}`"
        @click="exportHistory(String(item.statement_id || item.id))"
      >
        导出 {{ item.statement_id || item.id }}
      </button>
    </div>

    <article
      v-if="statement"
      class="recon-statement"
      data-testid="reconciliation-statement"
      :data-statement-id="statement.statement_id"
      :data-version="statement.version"
      :data-revision="statement.as_of_revision"
    >
      <header class="statement-head">
        <div>
          <h4>
            预算对账单
            <span class="version-badge" data-testid="reconciliation-version">v{{ statement.version }}</span>
            <span class="id-badge" data-testid="statement-id">{{ statement.statement_id }}</span>
          </h4>
          <p>
            只读历史快照 · 账本版本 r{{ statement.as_of_revision }} ·
            生成于 {{ formatTime(statement.created_at) }}
          </p>
        </div>
        <div class="statement-head-actions">
          <button class="btn" type="button" data-testid="statement-export" @click="exportCurrent">
            导出 CSV
          </button>
          <RouterLink
            class="btn ghost"
            :to="`/reconciliation/${statement.statement_id}`"
            data-testid="permalink-statement"
          >
            回查链接
          </RouterLink>
        </div>
      </header>

      <div class="statement-totals">
        <div><span>初始额度</span><strong>{{ fmt(statement.initial_quota) }}</strong></div>
        <div><span>已扣/回执</span><strong>{{ fmt(statement.receipt_total) }}</strong></div>
        <div><span>待退款</span><strong>{{ fmt(statement.pending_refund) }}</strong></div>
        <div><span>待确认占用</span><strong>{{ fmt(statement.pending_hold) }}</strong></div>
        <div><span>统一可用余额</span><strong>{{ fmt(statement.available_balance) }}</strong></div>
        <div><span>含待退参考/账本合计</span><strong>{{ fmt(statement.statement_balance) }}</strong></div>
        <div><span>退款完成后参考</span><strong>{{ fmt(statement.projected_balance) }}</strong></div>
        <div class="difference"><span>两页口径差额</span><strong>{{ fmt(statement.difference_amount) }}</strong></div>
      </div>

      <p class="sum-check" data-testid="sum-check" :data-balanced="String(statement.totals_match_details)">
        明细 {{ statement.line_count }} 笔；带符号加总
        <strong data-testid="detail-sum">{{ fmt(statement.all_details_sum) }}</strong>
        = 合计 <strong>{{ fmt(statement.summary?.all_details_sum) }}</strong>；
        可用口径加总 <strong>{{ fmt(statement.available_detail_total) }}</strong>
        = <strong>{{ fmt(statement.summary?.available_detail_total) }}</strong>。
        <span :class="statement.totals_match_details ? 'ok-text' : 'error-text'">
          {{ statement.totals_match_details ? '合计与明细相符' : '合计与明细不符' }}
        </span>
      </p>

      <div class="table-scroll">
        <table class="data-table recon-table" data-testid="reconciliation-details">
          <thead>
            <tr>
              <th>序号</th>
              <th>流水号</th>
              <th>执行回执</th>
              <th>类型</th>
              <th>状态</th>
              <th>金额(带符号)</th>
              <th>借方</th>
              <th>贷方</th>
              <th>可用余额影响</th>
              <th>说明/差额定位</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="line in statement.line_items"
              :key="line.id"
              :id="`recon-row-${line.id}`"
              :class="{
                'difference-row': line.is_difference,
                located: locatedId === line.id,
              }"
              :data-testid="line.is_difference ? 'difference-row' : `transaction-row-${line.id}`"
              :data-transaction-testid="line.is_difference ? 'difference-transaction' : `transaction-${line.id}`"
              :data-transaction-id="line.id"
              :data-difference="String(Boolean(line.is_difference))"
              :data-located="String(locatedId === line.id)"
            >
              <td>{{ line.seq }}</td>
              <td>
                <span :id="line.id" class="row-anchor" aria-hidden="true"></span>
                <code :data-testid="line.is_difference ? 'difference-transaction' : `transaction-code-${line.id}`">{{ line.id }}</code>
                <div class="flow-no">{{ line.flow_no }}</div>
              </td>
              <td>
                <div v-if="line.execution_id">{{ line.execution_id }}</div>
                <span v-else>—</span>
                <div v-if="line.receipt_id" class="muted">{{ line.receipt_id }}</div>
              </td>
              <td>{{ line.type_name }}</td>
              <td>
                <span class="status-pill" :data-status="line.status">{{ line.status_name }}</span>
              </td>
              <td :class="Number(line.amount) < 0 ? 'debit' : 'credit'">
                {{ signedFmt(line.amount) }}
              </td>
              <td>{{ Number(line.debit_amount) ? fmt(line.debit_amount) : '—' }}</td>
              <td>{{ Number(line.credit_amount) ? fmt(line.credit_amount) : '—' }}</td>
              <td>{{ signedFmt(line.balance_effect_amount) }}</td>
              <td>
                {{ line.description }}
                <span
                  v-if="line.is_difference"
                  class="difference-tag"
                  data-testid="difference-badge"
                >
                  差额流水 {{ fmt(line.difference_amount) }}
                </span>
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr data-testid="details-footer-sum">
              <td colspan="5">明细加总（含待退款贷方）</td>
              <td data-testid="footer-signed-sum">{{ fmt(statement.all_details_sum) }}</td>
              <td colspan="2"></td>
              <td>{{ fmt(statement.available_detail_total) }}</td>
              <td>与合计一致</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </article>

    <div v-else-if="!loading" class="recon-empty" data-testid="reconciliation-empty">
      尚未打开对账单。可先“发起对账”，或点击查询页的“打开预算对账单并定位差额”。
    </div>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  addHold,
  addRefund,
  completeExecution,
  createStatement,
  downloadStatement,
  getReconciliationState,
  getStatement,
  listStatements,
  setupScenario,
} from '@/api/reconciliation'

const props = withDefaults(defineProps<{
  moduleKey?: string
  statementId?: string | null
  focus?: string | null
  autoCreate?: boolean
}>(), {
  moduleKey: 'shared',
  statementId: null,
  focus: null,
  autoCreate: false,
})

const route = useRoute()
const state = ref<any>(null)
const statement = ref<any>(null)
const history = ref<any[]>([])
const loading = ref(false)
const busy = ref(false)
const errorMessage = ref('')
const notice = ref('')
const locatedId = ref<string | null>(null)
const simulateTimeout = ref(false)
const activeKey = ref<string>(
  window.localStorage.getItem('reconciliation:retryKey')
  || window.localStorage.getItem('reconciliation:activeKey')
  || `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
)
const oldKey = ref<string>(window.localStorage.getItem('reconciliation:oldKey') || '')
window.localStorage.setItem('reconciliation:activeKey', activeKey.value)

const form = reactive({
  initial_quota: '1.0',
  deducted: '0.3',
  pending_refund: '0.1',
  pending_hold: '0',
})
const refundAmount = ref('')
const holdAmount = ref('')

function fmt(value: unknown): string {
  const number = Number(value ?? 0)
  if (!Number.isFinite(number)) return '0.00'
  return number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}

function signedFmt(value: unknown): string {
  const number = Number(value ?? 0)
  return `${number > 0 ? '+' : ''}${fmt(number)}`
}

function formatTime(value?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function makeKey(prefix = 'web-recon'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function rememberKey(key: string) {
  activeKey.value = key
  window.localStorage.setItem('reconciliation:activeKey', key)
  window.localStorage.setItem('reconciliation:retryKey', key)
  window.localStorage.setItem(`reconciliation:key:${props.moduleKey}`, key)
}

async function refreshState() {
  loading.value = true
  try {
    state.value = await getReconciliationState()
    history.value = state.value.statements || []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '对账状态读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshHistory() {
  try {
    const payload = await listStatements()
    history.value = payload.items || []
  } catch {
    // 状态接口已包含历史；列表刷新失败不阻断主流程。
  }
}

async function applyScenario() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await setupScenario({ ...form, reset: true })
    statement.value = null
    locatedId.value = null
    oldKey.value = ''
    window.localStorage.removeItem('reconciliation:oldKey')
    const key = makeKey('scenario')
    rememberKey(key)
    window.localStorage.removeItem('reconciliation:statementId')
    await refreshState()
    notice.value = result.message || '题设场景已载入：初始 1.0、已扣 0.3、待退 0.1。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景初始化失败'
  } finally {
    busy.value = false
  }
}

async function startReconciliation() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  const key = makeKey('old')
  rememberKey(key)
  oldKey.value = key
  window.localStorage.setItem('reconciliation:oldKey', key)
  try {
    const payload: Record<string, unknown> = { idempotency_key: key }
    if (simulateTimeout.value) {
      payload.delay_ms = 1500
      await createStatement(payload, 800)
    } else {
      const result = await createStatement(payload)
      statement.value = result
      window.localStorage.setItem('reconciliation:statementId', result.statement_id)
      await afterStatementLoaded(props.focus || undefined)
    }
    await refreshHistory()
  } catch (error) {
    errorMessage.value = `第一次响应超时/失败：${error instanceof Error ? error.message : '未知错误'}。服务端快照可能已生成，请点“重试本次旧对账”。`
    notice.value = '已保留同一幂等键，重试会找回旧版本，不会生成新口径。'
  } finally {
    busy.value = false
  }
}

async function retryOldReconciliation() {
  busy.value = true
  errorMessage.value = ''
  try {
    const key = oldKey.value
      || window.localStorage.getItem('reconciliation:oldKey')
      || activeKey.value
      || window.localStorage.getItem('reconciliation:activeKey')
      || ''
    if (!key) throw new Error('没有可重试的对账键')
    const result = await createStatement({ idempotency_key: key })
    statement.value = result
    window.localStorage.setItem('reconciliation:statementId', result.statement_id)
    await afterStatementLoaded(props.focus || undefined)
    await refreshHistory()
    notice.value = `已找回幂等键对应的${result.statement_id} / v${result.version}，该旧版本不包含之后新完成的查询。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '旧对账版本找回失败'
  } finally {
    busy.value = false
  }
}

async function createNewReconciliation() {
  busy.value = true
  errorMessage.value = ''
  try {
    const key = makeKey('new')
    const result = await createStatement({ idempotency_key: key })
    rememberKey(key)
    statement.value = result
    window.localStorage.setItem('reconciliation:statementId', result.statement_id)
    await afterStatementLoaded(props.focus || undefined)
    await refreshHistory()
    notice.value = `已按当前账本发起新对账：${result.statement_id} / v${result.version}，包含此前已完成的 0.2 查询。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '新对账创建失败'
  } finally {
    busy.value = false
  }
}

async function completeScenarioQuery() {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await completeExecution({
      execution_id: 'EXEC-SCENARIO-020',
      idempotency_key: 'exec-scenario-020',
      query_id: 'DIFF-SCENARIO-020',
      query_no: 'DIFF-SCENARIO-020',
      amount: '0.2',
      epsilon: '0.2',
      status: 'completed',
      query: 'SELECT COUNT(*) FROM timeout_reconciliation_case',
      user: '对账超时场景',
    })
    await refreshState()
    notice.value = result.replayed
      ? '同一执行已确认过：刷新/重试没有重复计费。'
      : '0.2 查询已完成并取得服务端回执；当前打开的旧对账单保持不变，请发起新对账查看它。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '查询确认失败'
  } finally {
    busy.value = false
  }
}

async function registerRefund() {
  busy.value = true
  errorMessage.value = ''
  try {
    await addRefund({ amount: refundAmount.value || '0', pending: true })
    await refreshState()
    notice.value = '已登记待退款流水；它与可用余额、待确认占用分开呈现。'
    refundAmount.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '退款登记失败'
  } finally {
    busy.value = false
  }
}

async function registerHold() {
  busy.value = true
  errorMessage.value = ''
  try {
    await addHold({ amount: holdAmount.value || '0' })
    await refreshState()
    notice.value = '已登记待确认占用；该额度已冻结，但尚未变成已确认消耗。'
    holdAmount.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '占用登记失败'
  } finally {
    busy.value = false
  }
}

async function exportCurrent() {
  if (!statement.value) return
  await exportHistory(String(statement.value.statement_id))
}

async function exportHistory(statementId: string) {
  errorMessage.value = ''
  try {
    await downloadStatement(statementId)
    notice.value = `已导出 ${statementId}.csv；历史快照中的合计与明细保持一致。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function viewStatement(statementId: string) {
  busy.value = true
  errorMessage.value = ''
  try {
    statement.value = await getStatement(statementId)
    window.localStorage.setItem('reconciliation:statementId', statementId)
    await afterStatementLoaded(props.focus || undefined)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史对账单读取失败'
  } finally {
    busy.value = false
  }
}

async function locateDifference() {
  await afterStatementLoaded('difference')
}

async function afterStatementLoaded(focus?: string) {
  await nextTick()
  const queryTarget = [
    route.query.transaction_id,
    route.query.transaction,
    route.query.txn,
    route.query.entry,
  ].find((value): value is string => typeof value === 'string' && value.length > 0)
  let targetId: string | null = queryTarget || statement.value?.difference_transaction_id || null
  if (focus && focus !== 'difference') targetId = focus
  if (!focus && typeof route.query.focus === 'string' && route.query.focus !== 'difference') {
    targetId = route.query.focus
  }
  if (focus === 'difference' || route.query.focus === 'difference' || route.query.locate === 'difference') {
    targetId = statement.value?.difference_transaction_id || targetId
  }
  locatedId.value = targetId
  if (targetId) {
    document.getElementById(`recon-row-${targetId}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    })
  }
}

onMounted(async () => {
  await refreshState()
  const paramId = route.params.statementId
  const queryId = route.query.statement_id || route.query.statement || route.query.reconciliation
  const initialId = props.statementId
    || (typeof paramId === 'string' ? paramId : '')
    || (typeof queryId === 'string' && queryId !== 'open' && queryId !== 'latest' ? queryId : '')
    || window.localStorage.getItem('reconciliation:statementId')
  try {
    if (initialId && initialId !== 'latest') {
      statement.value = await getStatement(initialId)
      await afterStatementLoaded(props.focus || undefined)
    } else if (state.value?.latest_statement_id && (props.autoCreate || route.query.focus === 'difference')) {
      statement.value = await getStatement(String(state.value.latest_statement_id))
      await afterStatementLoaded(props.focus || undefined)
    } else if (props.autoCreate || route.query.focus === 'difference') {
      const result = await createStatement({ idempotency_key: makeKey('open') })
      statement.value = result
      await afterStatementLoaded('difference')
      await refreshHistory()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指定对账版本不存在'
  }
})
</script>

<style scoped>
.recon-panel { display: flex; flex-direction: column; gap: 12px; }
.recon-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.recon-head h3, .statement-head h4 { margin: 0 0 4px; }
.recon-head p, .statement-head p { margin: 0; color: var(--muted); font-size: 12px; }
.recon-balances { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.recon-balance { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.recon-balance span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
.recon-balance strong { font-size: 18px; }
.recon-balance.refund { background: #fff7ed; border-color: #fed7aa; }
.recon-balance.hold { background: #fefce8; border-color: #fde68a; }
.recon-balance.available { background: #eff6ff; border-color: #bfdbfe; }
.recon-balance.projected { background: #f0fdf4; border-color: #bbf7d0; }
.recon-setup { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; padding: 10px; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; }
.recon-setup label:not(.timeout-toggle) { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.recon-setup input:not([type='checkbox']) { width: 110px; padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; }
.timeout-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; flex-basis: 100%; }
.recon-extra { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; padding: 10px; border: 1px dashed var(--border); border-radius: 8px; }
.recon-extra label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.recon-extra input { width: 130px; padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; }
.recon-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.recon-key, .recon-notice { margin: 0; font-size: 12px; }
.recon-key code { background: #eef2ff; padding: 2px 5px; border-radius: 4px; }
.recon-notice { color: #166534; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 8px 10px; border-radius: 6px; }
.recon-history { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 13px; }
.history-link { margin-right: 6px; }
.recon-statement { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: #fff; }
.statement-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 12px; }
.statement-head-actions { display: flex; gap: 8px; }
.version-badge, .id-badge { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; margin-left: 6px; }
.version-badge { background: #dbeafe; color: #1d4ed8; }
.id-badge { background: #f1f5f9; color: #475569; }
.statement-totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 10px; }
.statement-totals div { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.statement-totals span { display: block; color: var(--muted); font-size: 12px; }
.statement-totals strong { font-size: 16px; }
.statement-totals .difference { border-color: #fb923c; background: #fff7ed; }
.sum-check { font-size: 13px; line-height: 1.7; background: #f8fafc; padding: 8px 10px; border-radius: 6px; }
.ok-text { color: #166534; font-weight: 600; }
.table-scroll { overflow-x: auto; }
.recon-table { min-width: 980px; }
.recon-table .debit { color: #b42318; }
.recon-table .credit { color: #166534; }
.muted, .flow-no { color: var(--muted); font-size: 12px; }
.row-anchor { display: block; position: relative; top: -90px; visibility: hidden; }
.status-pill { border-radius: 999px; padding: 2px 8px; background: #f1f5f9; font-size: 12px; white-space: nowrap; }
.difference-row { background: #fff7ed !important; box-shadow: inset 3px 0 #f97316; }
.difference-row.located, tr.located { outline: 3px solid #f97316; outline-offset: -3px; background: #ffedd5 !important; }
.difference-tag { display: inline-block; margin-left: 6px; padding: 2px 6px; border-radius: 4px; background: #f97316; color: #fff; font-size: 12px; white-space: nowrap; }
.recon-empty { border: 1px dashed var(--border); border-radius: 8px; padding: 18px; text-align: center; color: var(--muted); }
</style>
