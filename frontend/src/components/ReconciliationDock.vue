<template>
  <section class="recon-dock" data-testid="reconciliation-dock" :data-module="moduleKey">
    <div class="dock-summary">
      <div>
        <h3>差分查询 × 隐私预算统一对账</h3>
        <p>
          服务端按执行回执和预算流水计算余额；退款与待确认占用分别列示，同一执行刷新、重试或导出不重复计费。
        </p>
      </div>
      <div v-if="state?.balances" class="dock-balance" data-testid="dock-balance-values">
        <span>初始 <b>{{ fmt(state.balances.initial_quota) }}</b></span>
        <span>已扣 <b>{{ fmt(state.balances.receipt_total) }}</b></span>
        <span class="refund">待退 <b>{{ fmt(state.balances.pending_refund) }}</b></span>
        <span class="hold">待确认占用 <b>{{ fmt(state.balances.pending_hold) }}</b></span>
        <span class="available">统一可用余额 <b data-testid="dock-available">{{ fmt(state.balances.available_balance) }}</b></span>
      </div>
      <div class="dock-actions">
        <button
          class="btn primary"
          type="button"
          data-testid="reconcile"
          @click="openPanel(false, false)"
        >
          对账
        </button>
        <button
          class="btn primary"
          type="button"
          id="reconcile-button"
          data-testid="open-reconciliation"
          @click="openPanel(false, false)"
        >
          预算对账
        </button>
        <button
          class="btn"
          type="button"
          data-testid="open-statement"
          @click="openPanel(false, true)"
        >
          打开预算对账单
        </button>
        <button
          class="btn ghost"
          type="button"
          data-testid="open-locate-difference"
          @click="openPanel(true, true)"
        >
          打开并定位差额流水
        </button>
      </div>
    </div>

    <div
      v-if="open"
      class="modal-backdrop recon-backdrop"
      data-testid="reconciliation-modal"
      @click.self="close"
    >
      <div class="modal recon-modal" role="dialog" aria-modal="true" aria-label="预算对账单">
        <div class="modal-top">
          <span class="muted">来自{{ moduleKey === 'differential_sql' ? '差分查询页' : moduleKey === 'privacy_budget' ? '隐私预算页' : '对账页' }}的同一服务端版本</span>
          <button class="btn ghost" type="button" data-testid="close-reconciliation" @click="close">关闭</button>
        </div>
        <ReconciliationPanel
          :module-key="moduleKey"
          :focus="focus"
          :auto-create="autoCreate"
        />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import ReconciliationPanel from './ReconciliationPanel.vue'
import { getReconciliationState } from '@/api/reconciliation'

const props = withDefaults(defineProps<{ moduleKey?: string }>(), { moduleKey: 'shared' })
const route = useRoute()
const open = ref(false)
const focus = ref<string | null>(null)
const autoCreate = ref(false)
const state = ref<any>(null)

function fmt(value: unknown): string {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number.toFixed(2) : '0.00'
}

async function refresh() {
  try {
    state.value = await getReconciliationState()
  } catch {
    // 面板内部会展示详细错误；dock 保持可点击。
  }
}

function openPanel(locate = false, ensureStatement = true) {
  focus.value = locate ? 'difference' : null
  autoCreate.value = ensureStatement
  open.value = true
}

function close() {
  open.value = false
  void refresh()
}

defineExpose({ openPanel })
onMounted(async () => {
  await refresh()
  if (route.query.reconciliation !== undefined || route.query.reconcile !== undefined) {
    openPanel(route.query.focus === 'difference' || route.query.locate === 'difference')
  }
})
</script>

<style scoped>
.recon-dock { background: #fff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 14px 16px; margin: 12px 0; }
.dock-summary { display: flex; justify-content: space-between; gap: 16px; align-items: center; flex-wrap: wrap; }
.dock-summary h3 { margin: 0 0 4px; font-size: 16px; }
.dock-summary p { margin: 0; color: var(--muted); font-size: 13px; max-width: 680px; }
.dock-balance { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; align-items: center; }
.dock-balance b { font-size: 15px; }
.dock-balance .refund b { color: #c2410c; }
.dock-balance .hold b { color: #a16207; }
.dock-balance .available { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 999px; padding: 4px 10px; }
.dock-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.recon-modal { width: min(1180px, 96vw); }
.modal-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.muted { color: var(--muted); font-size: 12px; }
</style>
