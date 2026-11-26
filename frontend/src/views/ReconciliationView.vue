<template>
  <section class="reconciliation-page" data-testid="reconciliation-page">
    <header class="page-head">
      <div>
        <h2>预算对账单与版本回查</h2>
        <p class="page-desc">
          服务端按执行回执和预算流水生成不可变快照；旧对账重试返回旧版本，新对账才包含之后完成的查询。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/differential_sql">返回差分查询</RouterLink>
        <RouterLink class="btn" to="/privacy_budget">返回隐私预算</RouterLink>
      </div>
    </header>
    <div class="page-card">
      <ReconciliationPanel
        module-key="standalone"
        :statement-id="statementId"
        :focus="focus"
        :auto-create="true"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import ReconciliationPanel from '@/components/ReconciliationPanel.vue'

const route = useRoute()
const statementId = computed(() => {
  const value = route.params.statementId
  return typeof value === 'string' ? value : null
})
const focus = computed(() => (typeof route.query.focus === 'string' ? route.query.focus : null))
</script>

<style scoped>
.page-card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 18px; }
</style>
