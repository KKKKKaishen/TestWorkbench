<template>
  <div class="page">
    <div class="page-title">回归测试</div>
    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="选择项目" style="width:220px" @change="loadAll">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-tabs v-model="tab" style="flex:1; margin-left:12px" @tab-change="loadTab">
          <el-tab-pane label="回归集" name="sets" />
          <el-tab-pane label="定时计划" name="schedules" />
          <el-tab-pane label="执行记录" name="runs" />
        </el-tabs>
      </div>

      <!-- ========== 回归集（RG-01） ========== -->
      <template v-if="tab === 'sets'">
        <div class="toolbar">
          <el-button type="primary" :disabled="!projectId" @click="openSetDlg()">
            <el-icon><Plus /></el-icon>新建回归集
          </el-button>
          <el-button type="warning" plain :disabled="!projectId" @click="createP0">
            <el-icon><Star /></el-icon>P0 用例一键成集
          </el-button>
        </div>
        <el-table :data="sets" v-loading="loading" stripe>
          <el-table-column prop="name" label="回归集名称" min-width="180" show-overflow-tooltip />
          <el-table-column prop="case_count" label="用例数" width="80" />
          <el-table-column label="定时计划" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.schedule_count" size="small" type="success">{{ row.schedule_count }} 个</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="最近执行" width="170">
            <template #default="{ row }">
              <template v-if="row.last_run">
                <el-tag size="small" :type="runTag(row.last_run.status)">{{ runStatusMap[row.last_run.status] }}</el-tag>
                <div class="muted" style="font-size:12px; margin-top:2px">{{ fmt(row.last_run.finished_at) }}</div>
              </template>
              <span v-else class="muted">未执行</span>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip />
          <el-table-column label="操作" width="210" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" @click="openRunDlg(row)">执行</el-button>
              <el-button size="small" link type="primary" @click="openSetDlg(row)">编辑</el-button>
              <el-button size="small" link type="danger" @click="removeSet(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- ========== 定时计划（RG-02） ========== -->
      <template v-if="tab === 'schedules'">
        <div class="toolbar">
          <el-button type="primary" :disabled="!projectId" @click="openSchDlg()">
            <el-icon><Plus /></el-icon>新建定时计划
          </el-button>
          <span class="muted" style="font-size:12px">调度线程每 30 秒扫描一次到期计划并自动触发回归</span>
        </div>
        <el-table :data="schedules" v-loading="loading" stripe>
          <el-table-column prop="name" label="计划名称" min-width="160" show-overflow-tooltip />
          <el-table-column prop="set_name" label="回归集" min-width="140" show-overflow-tooltip />
          <el-table-column label="cron" width="130">
            <template #default="{ row }"><code class="cron-code">{{ row.cron }}</code></template>
          </el-table-column>
          <el-table-column prop="environment_name" label="环境" width="100" />
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-switch :model-value="!!row.enabled" @change="toggleSch(row)" />
            </template>
          </el-table-column>
          <el-table-column label="下次触发" width="150">
            <template #default="{ row }">{{ row.enabled ? fmt(row.next_run_at) : '—' }}</template>
          </el-table-column>
          <el-table-column label="上次触发" width="150">
            <template #default="{ row }">{{ fmt(row.last_run_at) || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" plain @click="runNow(row)">立即执行</el-button>
              <el-button size="small" link type="primary" @click="openSchDlg(row)">编辑</el-button>
              <el-button size="small" link type="danger" @click="removeSch(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- ========== 执行记录（RG-03） ========== -->
      <template v-if="tab === 'runs'">
        <el-table :data="runs" v-loading="loading" stripe>
          <el-table-column prop="set_name" label="回归集" min-width="150" show-overflow-tooltip />
          <el-table-column prop="plan_name" label="执行计划" min-width="170" show-overflow-tooltip />
          <el-table-column label="触发方式" width="90">
            <template #default="{ row }">
              {{ { manual: '手动', scheduled: '定时' }[row.trigger_type] || row.trigger_type }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="runTag(row.status)">{{ runStatusMap[row.status] }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="170">
            <template #default="{ row }">
              <span class="pass-n">{{ row.pass_count }}</span> 通过 /
              <span class="fail-n">{{ row.fail_count }}</span> 失败 /
              <span class="muted">{{ row.pending_count }}</span> 待执行
            </template>
          </el-table-column>
          <el-table-column label="通过率" width="90">
            <template #default="{ row }">
              {{ row.pass_rate != null ? row.pass_rate + '%' : '—' }}
            </template>
          </el-table-column>
          <el-table-column prop="started_at" label="开始时间" width="160" />
          <el-table-column prop="finished_at" label="结束时间" width="160" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openRunDetail(row)">用例明细</el-button>
              <el-button size="small" link type="danger" @click="removeRun(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>

    <!-- 新建/编辑回归集 -->
    <el-dialog v-model="setDlg" :title="setForm.id ? '编辑回归集' : '新建回归集'" width="720px">
      <el-form label-width="80px">
        <el-form-item label="名称" required><el-input v-model="setForm.name" placeholder="如 商城核心回归集" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="setForm.description" /></el-form-item>
        <el-form-item label="圈选用例">
          <div style="width:100%">
            <el-input v-model="caseKeyword" placeholder="搜索用例标题/编号" clearable style="width:240px; margin-bottom:8px"
                      @input="loadPickCases" />
            <el-table ref="pickTable" :data="pickCases" size="small" max-height="320" @selection-change="onPickChange">
              <el-table-column type="selection" width="40" />
              <el-table-column prop="case_no" label="编号" width="120" />
              <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
              <el-table-column prop="case_type" label="类型" width="80" />
              <el-table-column prop="priority" label="优先级" width="70" />
              <el-table-column prop="module_path" label="模块" min-width="120" show-overflow-tooltip />
            </el-table>
            <div class="muted" style="margin-top:6px">已选 {{ pickedCaseIds.length }} 条（接口用例可自动执行，功能用例执行时进入执行中心人工标记）</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="setDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!setForm.name" @click="saveSet">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行回归（选环境） -->
    <el-dialog v-model="runDlg" title="执行回归" width="440px">
      <div v-if="runSet" style="margin-bottom:12px">
        <b>{{ runSet.name }}</b>
        <span class="muted">（{{ runSet.case_count }} 条用例）</span>
      </div>
      <el-form label-width="80px">
        <el-form-item label="执行环境">
          <el-select v-model="runEnvId" style="width:100%" clearable placeholder="默认环境">
            <el-option v-for="e in envs" :key="e.id" :value="e.id" :label="e.name" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="muted" style="font-size:12px">接口用例将自动执行；失败自动重跑一次；功能用例进入执行中心人工标记。</div>
      <template #footer>
        <el-button @click="runDlg = false">取消</el-button>
        <el-button type="primary" :loading="runExecuting" @click="execRun">开始执行</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果 -->
    <el-dialog v-model="resultDlg" title="回归执行结果" width="720px">
      <div v-if="runResult">
        <div class="result-summary">
          <el-tag :type="runResult.summary.fail ? 'danger' : 'success'" size="large">
            {{ runResult.summary.pass }} 通过 / {{ runResult.summary.fail }} 失败
          </el-tag>
          <span v-if="runResult.pending_count" class="muted">
            另有 {{ runResult.pending_count }} 条功能用例待人工执行（执行中心 → 功能测试）
          </span>
        </div>
        <el-table v-if="runResult.auto_results.length" :data="runResult.auto_results" size="small" max-height="380">
          <el-table-column prop="case_no" label="编号" width="120" />
          <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
          <el-table-column label="结果" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.result === 'pass' ? 'success' : 'danger'">
                {{ row.result === 'pass' ? '通过' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="HTTP" width="70">
            <template #default="{ row }">{{ row.status ?? '—' }}</template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时" width="80">
            <template #default="{ row }">{{ row.duration_ms }}ms</template>
          </el-table-column>
          <el-table-column label="重跑" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.rerun" size="small" :type="row.result === 'pass' ? 'warning' : 'danger'">
                {{ row.result === 'pass' ? '重跑通过' : '重跑仍败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="error" label="错误" min-width="120" show-overflow-tooltip />
        </el-table>
        <div class="muted" style="margin-top:8px; font-size:12px">
          失败用例已自动生成「回归失败」测试日志（日志管理可查）。
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="resultDlg = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 用例明细（复用执行计划详情） -->
    <el-drawer v-model="detailDrawer" size="62%" :title="detailRun ? `回归明细 · ${detailRun.plan_name}` : ''">
      <template v-if="detailRun">
        <div class="detail-head">
          <el-tag size="small" :type="runTag(detailRun.status)">{{ runStatusMap[detailRun.status] }}</el-tag>
          <span class="muted">通过率 {{ detailRun.pass_rate != null ? detailRun.pass_rate + '%' : '—' }}</span>
          <span class="muted">{{ fmt(detailRun.started_at) }} ~ {{ fmt(detailRun.finished_at) || '进行中' }}</span>
        </div>
        <el-table :data="detailCases" v-loading="detailLoading" size="small" stripe>
          <el-table-column prop="case_no" label="编号" width="120" />
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="resultTag(row.result)">{{ resultMap[row.result] || row.result }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="note" label="备注" min-width="150" show-overflow-tooltip />
          <el-table-column prop="executed_at" label="执行时间" width="160" />
        </el-table>
        <div class="muted" style="margin-top:10px; font-size:12px">
          待执行用例可在「执行中心 → 功能测试」中找到对应回归计划进行人工标记。
        </div>
      </template>
    </el-drawer>
    <!-- 新建/编辑定时计划 -->
    <el-dialog v-model="schDlg" :title="schForm.id ? '编辑定时计划' : '新建定时计划'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="回归集" required>
          <el-select v-model="schForm.set_id" style="width:100%" placeholder="选择回归集">
            <el-option v-for="s in sets" :key="s.id" :value="s.id" :label="`${s.name}（${s.case_count} 条）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划名称"><el-input v-model="schForm.name" placeholder="留空自动生成" /></el-form-item>
        <el-form-item label="触发频率">
          <div style="width:100%">
            <el-select v-model="schForm.cron" style="width:100%">
              <el-option v-for="p in cronPresets" :key="p[0]" :value="p[0]" :label="p[1]" />
            </el-select>
            <el-input v-model="schForm.cron" style="margin-top:8px" placeholder="或直接输入 5 段 cron：分 时 日 月 周">
              <template #prepend>cron</template>
            </el-input>
            <div class="muted" style="font-size:12px; margin-top:4px">
              示例：0 9 * * 1-5 = 工作日 09:00；0 */2 * * * = 每 2 小时整点
            </div>
          </div>
        </el-form-item>
        <el-form-item label="执行环境">
          <el-select v-model="schForm.environment_id" style="width:100%" clearable placeholder="默认环境">
            <el-option v-for="e in envs" :key="e.id" :value="e.id" :label="e.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="schForm.enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="schDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!schForm.set_id || !schForm.cron" @click="saveSch">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '../api'

const projects = ref([])
const projectId = ref(null)
const tab = ref('sets')
const loading = ref(false)
const sets = ref([])
const schedules = ref([])
const runs = ref([])
const envs = ref([])

const runStatusMap = { running: '执行中', completed: '已完成', failed: '失败' }
const runTag = s => ({ running: 'warning', completed: 'success', failed: 'danger' }[s] || 'info')
const resultMap = { pending: '待执行', pass: '通过', fail: '失败', block: '阻塞', skip: '跳过' }
const resultTag = r => ({ pass: 'success', fail: 'danger', block: 'warning', pending: 'info', skip: 'info' }[r] || 'info')

// ---- 回归集 ----
const setDlg = ref(false)
const setForm = ref({ id: null, name: '', description: '' })
const pickCases = ref([])
const caseKeyword = ref('')
const pickedCaseIds = ref([])
const pickTable = ref(null)
const pickLoaded = ref(false)  // 编辑时首次加载后回显已选

// ---- 执行 ----
const runDlg = ref(false)
const runSet = ref(null)
const runEnvId = ref(null)
const runExecuting = ref(false)
const resultDlg = ref(false)
const runResult = ref(null)

// ---- 明细 ----
const detailDrawer = ref(false)
const detailRun = ref(null)
const detailCases = ref([])
const detailLoading = ref(false)

// ---- 定时计划 ----
const schDlg = ref(false)
const schForm = ref({ id: null, set_id: null, name: '', cron: '0 9 * * *', environment_id: null, enabled: 1 })
const cronPresets = [
  ['0 9 * * *', '每天 09:00'],
  ['0 9 * * 1', '每周一 09:00'],
  ['0 9 * * 1-5', '工作日 09:00'],
  ['0 9 1 * *', '每月 1 日 09:00'],
  ['*/30 * * * *', '每 30 分钟'],
]

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (projects.value.length) {
    projectId.value = projects.value[0].id
    loadAll()
  }
})

function loadAll() {
  loadTab('sets')
  loadTab('schedules')
  loadTab('runs')
}

async function loadTab(t) {
  if (!projectId.value) return
  loading.value = true
  try {
    if (t === 'sets') sets.value = await get(`/wb/regression-sets?project_id=${projectId.value}`)
    else if (t === 'schedules') schedules.value = await get(`/wb/regression-schedules?project_id=${projectId.value}`)
    else if (t === 'runs') runs.value = await get(`/wb/regression-runs?project_id=${projectId.value}`)
  } finally { loading.value = false }
}

const fmt = s => (s ? String(s).replace('T', ' ').slice(0, 19) : '')

// ---- 回归集弹窗 ----
async function openSetDlg(row) {
  setForm.value = row
    ? { id: row.id, name: row.name, description: row.description || '' }
    : { id: null, name: '', description: '' }
  caseKeyword.value = ''
  pickLoaded.value = false
  pickedCaseIds.value = []
  await loadPickCases()
  if (row) {
    const d = await get(`/wb/regression-sets/${row.id}`)
    // 回显已选：在候选表加载完成后勾选
    const idSet = new Set(d.cases.map(c => c.id))
    setForm.value.__selected = idSet
    pickLoaded.value = true
    togglePickRows()
  }
  setDlg.value = true
}

async function loadPickCases() {
  if (!projectId.value) return
  const q = new URLSearchParams({ project_id: projectId.value, size: 200, status: 'active' })
  if (caseKeyword.value) q.set('keyword', caseKeyword.value)
  pickCases.value = (await get('/wb/cases?' + q)).items
  togglePickRows()
}

function togglePickRows() {
  if (!setForm.value.__selected) return
  const sel = setForm.value.__selected
  nextTick(() => {
    pickCases.value.forEach(r => {
      pickTable.value?.toggleRowSelection(r, sel.has(r.id))
    })
  })
}

function onPickChange(rows) {
  pickedCaseIds.value = rows.map(r => r.id)
}

async function saveSet() {
  const f = setForm.value
  if (f.id) {
    await put(`/wb/regression-sets/${f.id}`, { project_id: projectId.value, name: f.name, description: f.description, case_ids: pickedCaseIds.value })
  } else {
    await post('/wb/regression-sets', { project_id: projectId.value, name: f.name, description: f.description, case_ids: pickedCaseIds.value })
  }
  ElMessage.success('已保存')
  setDlg.value = false
  loadTab('sets')
}

async function createP0() {
  try {
    const r = await post('/wb/regression-sets/p0', { project_id: projectId.value })
    ElMessage.success(`已创建 P0 回归集，圈选 ${r.added} 条用例`)
    loadTab('sets')
  } catch (e) {
    ElMessage.warning(e.message)
  }
}

async function removeSet(row) {
  await ElMessageBox.confirm(`删除回归集「${row.name}」？历史执行记录与定时计划将一并移除引用。`, '删除确认', { type: 'warning' })
  await del(`/wb/regression-sets/${row.id}`)
  ElMessage.success('已删除')
  loadTab('sets')
}

// ---- 执行 ----
async function openRunDlg(row) {
  runSet.value = row
  runEnvId.value = null
  if (!envs.value.length) envs.value = await get(`/wb/api-envs?project_id=${projectId.value}`)
  runDlg.value = true
}

async function execRun() {
  runExecuting.value = true
  try {
    const r = await post('/wb/regression-runs', {
      set_id: runSet.value.id, environment_id: runEnvId.value, trigger_type: 'manual'
    })
    runResult.value = r
    runDlg.value = false
    resultDlg.value = true
    loadTab('sets')
    loadTab('runs')
  } finally { runExecuting.value = false }
}

// ---- 明细 ----
async function openRunDetail(row) {
  detailRun.value = row
  detailDrawer.value = true
  detailLoading.value = true
  try {
    const p = await get(`/wb/plans/${row.plan_id}`)
    detailCases.value = p.cases
  } finally { detailLoading.value = false }
}

async function removeRun(row) {
  await ElMessageBox.confirm('删除该回归执行记录？（关联执行计划保留在执行中心）', '删除确认', { type: 'warning' })
  await del(`/wb/regression-runs/${row.id}`)
  loadTab('runs')
}

// ---- 定时计划 ----
async function openSchDlg(row) {
  if (!sets.value.length) await loadTab('sets')
  schForm.value = row
    ? { id: row.id, set_id: row.set_id, name: row.name, cron: row.cron, environment_id: row.environment_id, enabled: row.enabled ? 1 : 0 }
    : { id: null, set_id: sets.value[0]?.id || null, name: '', cron: '0 9 * * *', environment_id: null, enabled: 1 }
  if (!envs.value.length) envs.value = await get(`/wb/api-envs?project_id=${projectId.value}`)
  schDlg.value = true
}

async function saveSch() {
  const f = schForm.value
  if (!f.set_id) return ElMessage.warning('请选择回归集')
  const body = { set_id: f.set_id, name: f.name, cron: f.cron, environment_id: f.environment_id, enabled: f.enabled }
  if (f.id) {
    await put(`/wb/regression-schedules/${f.id}`, body)
  } else {
    const r = await post('/wb/regression-schedules', body)
    ElMessage.success(`已创建，下次触发：${String(r.next_run_at).replace('T', ' ')}`)
  }
  schDlg.value = false
  loadTab('schedules')
}

async function toggleSch(row) {
  await post(`/wb/regression-schedules/${row.id}/toggle`)
  loadTab('schedules')
}

async function runNow(row) {
  const r = await post(`/wb/regression-schedules/${row.id}/run-now`)
  runResult.value = r
  resultDlg.value = true
  loadTab('schedules')
  loadTab('runs')
}

async function removeSch(row) {
  await ElMessageBox.confirm(`删除定时计划「${row.name}」？`, '删除确认', { type: 'warning' })
  await del(`/wb/regression-schedules/${row.id}`)
  loadTab('schedules')
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.cron-code {
  background: #f1f5f9; border: 1px solid var(--wb-border); border-radius: 4px;
  padding: 1px 6px; font-size: 12px; font-family: Consolas, monospace;
}
.result-summary { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.detail-head { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.pass-n { color: var(--wb-pass, #16a34a); font-weight: 600; }
.fail-n { color: var(--wb-fail, #dc2626); font-weight: 600; }
</style>
