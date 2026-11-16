<template>
  <section class="page" data-module="delta">
    <header class="page-head">
      <div>
        <h2>增量采集管理</h2>
        <p class="page-desc">
          确认水位与扫描位置分开保存：扫描只推进确认水位，回退只移动扫描位置；同一游标与去重窗口只生成一个批次。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记增量游标</button>
        <button class="btn" type="button" @click="exportRows">导出增量采集清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>游标编号</span>
        <input v-model="filters['游标编号']" placeholder="按游标编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters['状态']">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-if="isSelected(row)">
              <form class="scan-form" @submit.prevent="runScan">
                <input v-model="scanWindow.开始" placeholder="窗口开始" />
                <input v-model="scanWindow.结束" placeholder="窗口结束" />
                <button class="link" type="submit">扫描窗口</button>
                <button class="link" type="button" @click="runRewind(row)">回退扫描位置</button>
              </form>
            </template>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="selectRow(row)">
              {{ isSelected(row) ? '收起详情' : '查看批次' }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无增量采集数据，可先登记增量游标</td>
        </tr>
      </tbody>
    </table>

    <section v-if="selected" class="detail-panel">
      <header class="page-head">
        <div>
          <h3>{{ selected['游标编号'] }} · 批次与审计</h3>
          <p class="page-desc">
            确认水位 {{ selected['确认水位'] || '—' }} · 扫描位置 {{ selected['扫描位置'] || '—' }} ·
            去重窗口 {{ selected['去重窗口'] || '—' }}
          </p>
        </div>
        <button class="btn ghost" type="button" @click="reloadBatches">刷新批次</button>
      </header>

      <h4>增量批次数据集</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次号</th>
            <th>去重窗口</th>
            <th>扫描位置</th>
            <th>终态</th>
            <th>入账/重复/冲突</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches" :key="String(batch.id)">
            <td>{{ batch['批次号'] }}</td>
            <td>{{ batch['去重窗口'] }}</td>
            <td>{{ batch['扫描位置'] || '—' }}</td>
            <td>{{ batch['终态'] }}</td>
            <td>{{ batch['入账数'] }} / {{ batch['重复数'] }} / {{ batch['冲突数'] }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="showBatch(batch)">批次详情</button>
              <button
                v-if="batch['终态'] === '失败'"
                class="link"
                type="button"
                @click="retryBatch(batch)"
              >
                从失败窗口重试
              </button>
            </td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="6" class="empty-state">该游标还没有批次，先在游标行扫描一个窗口</td>
          </tr>
        </tbody>
      </table>

      <h4>增量审计待处理队列</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>失败窗口</th>
            <th>批次号</th>
            <th>扫描位置</th>
            <th>状态</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="audit in audits" :key="String(audit.id)">
            <td>{{ audit['类型'] }}</td>
            <td>{{ audit['失败窗口'] }}</td>
            <td>{{ audit['批次号'] }}</td>
            <td>{{ audit['扫描位置'] || '—' }}</td>
            <td>{{ audit['状态'] }}</td>
            <td>{{ audit['原因'] }}</td>
          </tr>
          <tr v-if="!audits.length">
            <td colspan="6" class="empty-state">暂无待处理的审计窗口</td>
          </tr>
        </tbody>
      </table>

      <section v-if="batchDetail" class="detail-panel nested">
        <h4>批次详情 · {{ batchDetail['批次号'] }}</h4>
        <p class="page-desc">
          扫描位置 {{ batchDetail['扫描位置'] || '—' }} · 去重窗口 {{ batchDetail['去重窗口'] }} ·
          终态 {{ batchDetail['终态'] }}
        </p>
        <table class="data-table">
          <thead>
            <tr><th>记录编号</th><th>记录时间</th><th>结果</th><th>判断</th></tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in batchDetail['处理结果'] || []" :key="`${item['记录编号']}-${item['结果']}-${index}`">
              <td>{{ item['记录编号'] }}</td>
              <td>{{ item['记录时间'] || '—' }}</td>
              <td>{{ item['结果'] }}</td>
              <td>{{ item['判断'] || '—' }}</td>
            </tr>
            <tr v-if="!(batchDetail['处理结果'] || []).length">
              <td colspan="4" class="empty-state">该批次没有记录明细</td>
            </tr>
          </tbody>
        </table>
      </section>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条增量采集记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="notice" class="notice-text">{{ notice }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/delta'
const columns = ["游标编号", "目标端点", "增量字段", "确认水位", "扫描位置", "回退步数", "批次上限", "去重窗口", "重跑开关"]
const actions = ["提交校验", "启动增量", "暂停增量"]
const statuses = ["草稿", "待校验", "运行中", "已暂停"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const filters = ref<Record<string, string>>({ 游标编号: '', 状态: '' })
const selected = ref<Row | null>(null)
const batches = ref<Row[]>([])
const audits = ref<Row[]>([])
const batchDetail = ref<Row | null>(null)
const scanWindow = ref({ 开始: '', 结束: '' })

const stats = ref([
  { label: '增量游标总数', value: 0 },
  { label: '运行中游标', value: 0 },
  { label: '待校验游标', value: 0 },
])

function isSelected(row: Row): boolean {
  return selected.value?.id === row.id
}

function resetFilters() {
  filters.value = { 游标编号: '', 状态: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '增量游标登记入口尚未接入审批流'
}

function selectRow(row: Row) {
  selected.value = isSelected(row) ? null : row
  batchDetail.value = null
  if (selected.value) {
    scanWindow.value = {
      开始: String(row['扫描位置'] || row['确认水位'] || ''),
      结束: '',
    }
    void reloadBatches()
  }
}

async function reloadBatches() {
  if (!selected.value) return
  const code = String(selected.value['游标编号'] || '')
  try {
    const query = new URLSearchParams({ cursor_code: code }).toString()
    const batchResponse = await request(`${ENDPOINT}/batches?${query}`)
    const auditResponse = await request(`${ENDPOINT}/audit?size=200`)
    if (!batchResponse.ok || !auditResponse.ok) {
      throw new Error('批次或审计队列读取失败')
    }
    const batchPayload = await batchResponse.json()
    const auditPayload = await auditResponse.json()
    batches.value = batchPayload.items ?? []
    audits.value = (auditPayload.items ?? []).filter((item: Row) => item['游标编号'] === code)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次读取失败'
  }
}

async function showBatch(batch: Row) {
  try {
    const response = await request(`${ENDPOINT}/batches/${batch.id}`)
    if (!response.ok) {
      throw new Error('批次详情读取失败')
    }
    batchDetail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次详情读取失败'
  }
}

async function runScan() {
  if (!selected.value) return
  errorMessage.value = ''
  notice.value = ''
  try {
    const payload = {
      values: {
        窗口开始: scanWindow.value.开始,
        窗口结束: scanWindow.value.结束,
      },
    }
    const response = await request(`${ENDPOINT}/${selected.value.id}/scan`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message || '增量窗口扫描失败')
    }
    notice.value = `${result.message}：批次 ${result.batch['批次号']} 终态 ${result.batch['终态']}`
    batchDetail.value = result.batch
    await reload()
    selected.value = rows.value.find((row) => row.id === result.entry.id) ?? result.entry
    await reloadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '增量窗口扫描失败'
  }
}

async function runRewind(row: Row) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/rewind`, { method: 'POST' })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message || '扫描位置回退失败')
    }
    notice.value = result.message
    await reload()
    selected.value = rows.value.find((item) => item.id === row.id) ?? result.entry
    scanWindow.value.开始 = String(result.entry['扫描位置'] || '')
    await reloadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '扫描位置回退失败'
  }
}

async function retryBatch(batch: Row) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/batches/${batch.id}/retry`, { method: 'POST' })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message || '失败窗口重试失败')
    }
    notice.value = `${result.message}：确认水位保持 ${result.entry['确认水位'] || '空'}`
    batchDetail.value = result.batch
    await reload()
    await reloadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '失败窗口重试失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message || '增量采集动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '增量采集操作失败'
  }
}

function updateStats() {
  stats.value = [
    { label: '增量游标总数', value: total.value },
    { label: '运行中游标', value: rows.value.filter((row) => row.status === '运行中').length },
    { label: '待校验游标', value: rows.value.filter((row) => row.status === '待校验').length },
  ]
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({
    keyword: filters.value['游标编号'] ?? '',
    status: filters.value['状态'] ?? '',
  }).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('增量游标列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '增量采集列表读取失败'
  }
}

onMounted(reload)
</script>
