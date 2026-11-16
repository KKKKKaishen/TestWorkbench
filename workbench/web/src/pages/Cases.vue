<template>
  <div class="page">
    <div class="page-title">用例库</div>
    <div class="case-layout">
      <!-- 左：项目菜单树 -->
      <aside class="case-side card">
        <div class="side-title">按项目分类</div>
        <div v-for="p in projects" :key="p.id" class="proj-group">
          <div class="proj-item" :class="{ active: p.id === projectId }" @click="selectProject(p)">
            <el-icon class="proj-caret" :class="{ open: expanded[p.id] }" @click.stop="toggleExpand(p)"><ArrowRight /></el-icon>
            <el-icon><Folder /></el-icon>
            <span class="proj-name">{{ p.name }}</span>
            <el-tag size="small" effect="plain">{{ p.stats.cases_total }}</el-tag>
          </div>
          <div v-show="expanded[p.id]" class="type-list">
            <div class="type-item" :class="{ active: !filterType }" @click="setType('')">
              <span class="t-dot" style="background:#64748b"></span>
              <span>全部用例</span>
              <span class="muted" style="margin-left:auto">{{ p.stats.cases_total }}</span>
            </div>
            <div v-for="t in typeList" :key="t.key" class="type-item"
                 :class="{ active: filterType === t.key }" @click="setType(t.key)">
              <span class="t-dot" :style="{ background: t.color }"></span>
              <span>{{ t.label }}</span>
              <span class="muted" style="margin-left:auto">{{ p.stats.cases[t.key] || 0 }}</span>
            </div>
          </div>
        </div>
      </aside>

      <!-- 右：用例表格 -->
      <div class="case-main">
        <div class="card">
          <div class="toolbar">
            <el-select v-model="projectId" placeholder="选择项目" style="width:200px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
            <el-input v-model="keyword" placeholder="搜索标题/编号" style="width:220px" clearable @keyup.enter="load" @clear="load" />
            <el-select v-model="filterStatus" style="width:110px" @change="page = 1; load()">
              <el-option value="active" label="启用中" />
              <el-option value="deprecated" label="已废弃" />
              <el-option value="" label="全部状态" />
            </el-select>
            <el-button type="primary" :disabled="!projectId" @click="openCreate">
              <el-icon><Plus /></el-icon>新建用例
            </el-button>
            <el-button type="success" plain :disabled="!selected.length" @click="goExec">
              执行圈选（{{ selected.length }}）
            </el-button>
            <el-button type="warning" plain :disabled="!selected.length" @click="openMove(selected.map(c => c.id))">
              <el-icon><Position /></el-icon>移动到项目（{{ selected.length }}）
            </el-button>
          </div>

          <el-table :data="cases" v-loading="loading" stripe @selection-change="s => selected = s">
            <el-table-column type="selection" width="42" />
            <el-table-column prop="case_no" label="编号" width="130" />
            <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="case_type" label="类型" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="{ functional: 'primary', api: 'success', automation: 'warning' }[row.case_type]">
                  {{ { functional: '功能', api: '接口', automation: '自动化' }[row.case_type] }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="module_path" label="模块" width="120" show-overflow-tooltip />
            <el-table-column prop="priority" label="优先级" width="80" />
            <el-table-column prop="gen_source" label="来源" width="80">
              <template #default="{ row }">{{ { manual: '人工', ai: 'AI', rule_engine: '规则' }[row.gen_source] }}</template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right" class-name="op-col">
              <template #default="{ row }">
                <div class="op-line">
                  <el-button size="small" link @click="openCreate(row)">编辑</el-button>
                  <el-button size="small" link type="warning" @click="openMove([row.id])">移动</el-button>
                  <el-popconfirm title="废弃该用例？" @confirm="deprecate(row)">
                    <template #reference><el-button size="small" link type="danger">废弃</el-button></template>
                  </el-popconfirm>
                </div>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-model:current-page="page" :page-size="size" :total="total"
                         layout="total, prev, pager, next" style="margin-top:12px; justify-content:flex-end"
                         @current-change="load" />
        </div>
      </div>
    </div>

    <!-- 用例编辑 -->
    <el-dialog v-model="dlg" :title="form.id ? `编辑用例 ${form.case_no}` : '新建用例'" width="680px" top="4vh">
      <el-form :model="form" label-width="80px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="标题" required><el-input v-model="form.title" /></el-form-item></el-col>
          <el-col :span="6"><el-form-item label="类型">
            <el-select v-model="form.case_type">
              <el-option value="functional" label="功能" />
              <el-option value="api" label="接口" />
              <el-option value="automation" label="自动化" />
            </el-select>
          </el-form-item></el-col>
          <el-col :span="6"><el-form-item label="优先级">
            <el-select v-model="form.priority" clearable>
              <el-option v-for="v in ['P0','P1','P2','P3']" :key="v" :value="v" :label="v" />
            </el-select>
          </el-form-item></el-col>
        </el-row>
        <el-form-item label="模块路径"><el-input v-model="form.module_path" placeholder="如 商城/购物车（用 / 分层）" /></el-form-item>
        <el-form-item label="前置条件"><el-input v-model="form.precondition" type="textarea" :rows="2" /></el-form-item>

        <!-- 接口用例：参数快照区 -->
        <template v-if="form.case_type === 'api'">
          <el-divider content-position="left">接口参数（快照）</el-divider>
          <!-- 新建：从接口管理选择接口 -->
          <div v-if="!form.id" class="snap-block">
            <el-button v-if="!pickedApi" @click="apiPickDlg = true"><el-icon><Plus /></el-icon>选择接口</el-button>
            <div v-else class="api-chip">
              <span class="m-tag" :class="'m-' + pickedApi.method.toLowerCase()">{{ pickedApi.method }}</span>
              <span class="api-chip-name">{{ pickedApi.name }}</span>
              <span class="muted chip-url">{{ pickedApi.url }}</span>
              <el-button link type="primary" size="small" @click="apiPickDlg = true">更换</el-button>
              <el-button link type="danger" size="small" @click="pickedApi = null">移除</el-button>
            </div>
            <div class="muted snap-tip">将按所选接口当前参数生成快照归档到用例，保存用例不影响接口管理中的原接口</div>
          </div>
          <!-- 编辑：直接维护快照参数 -->
          <div v-else-if="snap" class="snap-block">
            <div style="display: flex; gap: 8px; margin-bottom: 8px">
              <el-select v-model="snap.method" style="width: 110px">
                <el-option v-for="m in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']" :key="m" :value="m" />
              </el-select>
              <el-input v-model="snap.url" placeholder="/path 或完整 URL" style="flex: 1" />
            </div>
            <div class="muted snap-label">Query / Path 参数</div>
            <param-table v-model="snap.params" />
            <div class="muted snap-label">Headers</div>
            <kv-table v-model="snap.headers" />
            <div style="margin: 8px 0">
              <el-radio-group v-model="snap.body_type" size="small">
                <el-radio-button value="none">none</el-radio-button>
                <el-radio-button value="json">json</el-radio-button>
                <el-radio-button value="form">form</el-radio-button>
                <el-radio-button value="raw">raw</el-radio-button>
              </el-radio-group>
            </div>
            <el-input v-if="snap.body_type && snap.body_type !== 'none'" v-model="snap.body" type="textarea" :rows="4" class="mono" />
            <el-checkbox v-if="snap.request_id" v-model="syncOriginal" style="margin-top: 8px">
              保存时同时更新接口管理原接口
            </el-checkbox>
          </div>
        </template>

        <el-form-item label="测试步骤">
          <div v-for="(s, i) in form.steps" :key="i" class="step-row">
            <span class="step-no">{{ i + 1 }}</span>
            <el-input v-model="s.action" placeholder="操作" style="flex:1" />
            <el-input v-model="s.expected" placeholder="预期" style="flex:1" />
            <el-button link type="danger" @click="form.steps.splice(i, 1)"><el-icon><Delete /></el-icon></el-button>
          </div>
          <el-button size="small" @click="form.steps.push({ action: '', expected: '' })">
            <el-icon><Plus /></el-icon>添加步骤
          </el-button>
        </el-form-item>
        <el-form-item label="预期结果"><el-input v-model="form.expected" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="form.id" type="warning" plain style="float:left; margin-left:0"
                   @click="openMove([form.id])"><el-icon><Position /></el-icon>移动到项目</el-button>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">{{ form.case_type === 'api' ? '保存快照' : '保存' }}</el-button>
      </template>
    </el-dialog>

    <!-- 接口选择弹窗（新建接口用例时从接口管理选取） -->
    <api-pick-dialog v-model="apiPickDlg" :project-id="projectId" @select="onApiPicked" />

    <!-- 移动用例到项目：搜索项目名称，默认展示全部项目 -->
    <el-dialog v-model="moveDlg" :title="`移动用例到项目（已选 ${moveIds.length} 条）`" width="420px">
      <el-input v-model="moveKeyword" placeholder="搜索项目名称" clearable style="margin-bottom:10px" />
      <div class="move-list">
        <div v-for="p in moveProjects" :key="p.id" class="move-item"
             :class="{ active: moveTarget === p.id, disabled: p.id === projectId }"
             @click="p.id !== projectId && (moveTarget = p.id)">
          <el-icon><Folder /></el-icon>
          <span class="move-name">{{ p.name }}</span>
          <el-tag v-if="p.id === projectId" size="small" type="info" effect="plain">当前项目</el-tag>
          <el-icon v-else-if="moveTarget === p.id" class="move-check"><Check /></el-icon>
        </div>
        <div v-if="!moveProjects.length" class="muted" style="padding:16px; text-align:center; font-size:13px">无匹配项目</div>
      </div>
      <template #footer>
        <el-button @click="moveDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!moveTarget" @click="confirmMove">确认移动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post, put } from '../api'
import ApiPickDialog from '../components/ApiPickDialog.vue'
import KvTable from '../components/KvTable.vue'
import ParamTable from '../components/ParamTable.vue'

const router = useRouter()
const projects = ref([])
const projectId = ref(null)
// 项目目录展开状态（手风琴式：同时只展开一个，方便多项目筛选）
const expanded = reactive({})
const cases = ref([])
const loading = ref(false)
const keyword = ref('')
const filterType = ref('')
const filterStatus = ref('active')
const page = ref(1)
const size = 50
const total = ref(0)
const selected = ref([])
const dlg = ref(false)
const typeList = [
  { key: 'functional', label: '功能用例', color: '#3a7afe' },
  { key: 'api', label: '接口用例', color: '#10b981' },
  { key: 'automation', label: '自动化用例', color: '#f59e0b' }
]
const emptyForm = { title: '', case_type: 'functional', module_path: '', priority: 'P1', precondition: '', steps: [{ action: '', expected: '' }], expected: '' }
const form = ref({ ...emptyForm, steps: [{ action: '', expected: '' }] })

// ---------- 接口用例（快照机制） ----------
const apiPickDlg = ref(false)
const pickedApi = ref(null)   // 新建时选中的接口（完整配置）
const snap = ref(null)        // 编辑时的快照副本
const syncOriginal = ref(false)

// 编辑态把类型切成"接口"时，初始化空快照以便直接录入参数
watch(() => form.value.case_type, t => {
  if (t === 'api' && form.value.id && !snap.value) {
    snap.value = {
      request_id: null, name: form.value.title, method: 'GET', url: '',
      headers: [{ key: '', value: '', enabled: 1 }],
      params: [{ key: '', value: '', type: 'string', desc: '', enabled: 1 }],
      body_type: 'none', body: '', auth: null,
    }
  }
})

function snapshotFromRequest(r) {
  return {
    request_id: r.id, name: r.name, method: r.method || 'GET', url: r.url || '',
    headers: (r.headers || []).map(h => ({ key: h.key || '', value: h.value || '', enabled: h.enabled ?? 1 })),
    params: (r.params || []).map(p => ({ key: p.key || '', value: p.value || '', type: p.type || 'string', desc: p.desc || '', enabled: p.enabled ?? 1 })),
    body_type: r.body_type || 'none', body: r.body || '', auth: r.auth || null,
    description: r.description || '',
  }
}

function onApiPicked(r) {
  pickedApi.value = r
  // 自动带出标题与前置条件，减少录入
  if (!form.value.title) form.value.title = r.name
  if (!form.value.precondition) form.value.precondition = `接口可访问：${r.url}`
  if (form.value.steps.length === 1 && !form.value.steps[0].action) {
    form.value.steps = [{ action: `${r.method} ${r.url}`, expected: '响应状态码 2xx，返回符合预期' }]
  }
}

async function save() {
  try {
    if (form.value.id) {
      const { id, case_no, ...body } = form.value
      if (form.value.case_type === 'api' && snap.value) {
        body.api_snapshot = {
          ...snap.value,
          headers: (snap.value.headers || []).filter(h => h.key),
          params: (snap.value.params || []).filter(p => p.key),
        }
        // 显式勾选时才回写接口管理原接口（默认仅保存用例快照）
        if (syncOriginal.value && snap.value.request_id) {
          await put(`/wb/api-requests/${snap.value.request_id}`, {
            project_id: projectId.value,
            name: snap.value.name || form.value.title,
            method: snap.value.method, url: snap.value.url,
            headers: (snap.value.headers || []).filter(h => h.key),
            params: (snap.value.params || []).filter(p => p.key),
            body_type: snap.value.body_type, body: snap.value.body,
            auth: snap.value.auth,
          })
        }
      }
      await put(`/wb/cases/${id}`, { ...body, project_id: projectId.value })
    } else {
      const body = { ...form.value, project_id: projectId.value }
      if (form.value.case_type === 'api') {
        if (!pickedApi.value) return ElMessage.warning('接口用例请先从接口管理选择一个接口')
        body.api_snapshot = snapshotFromRequest(pickedApi.value)
      }
      await post('/wb/cases', body)
    }
    ElMessage.success('已保存')
    dlg.value = false
    load()
    loadProjects()
  } catch (e) { ElMessage.error(e.message) }
}

function selectProject(p) {
  projectId.value = p.id
  filterType.value = ''
  page.value = 1
  expandOnly(p.id)
  load()
}

// 手风琴式展开：展开某个项目时收起其余
function expandOnly(id) {
  Object.keys(expanded).forEach(k => { expanded[k] = false })
  expanded[id] = true
}

function toggleExpand(p) {
  if (expanded[p.id]) {
    expanded[p.id] = false
  } else {
    expandOnly(p.id)
  }
}

// 顶部下拉切换项目：同步左侧目录展开
function onProjectChange() {
  filterType.value = ''
  page.value = 1
  if (projectId.value) expandOnly(projectId.value)
  load()
}

async function loadProjects() {
  projects.value = await get('/wb/projects')
  if (!projectId.value && projects.value.length) {
    projectId.value = projects.value[0].id
    load()
  }
  // 默认展开当前选中项目
  if (projectId.value) expanded[projectId.value] = true
}

function setType(key) {
  // 点同一个类型再点一次取消筛选；每次切换都重置页码并重新加载
  filterType.value = filterType.value === key ? '' : key
  page.value = 1
  load()
}

async function load() {
  if (!projectId.value) return
  loading.value = true
  try {
    const q = new URLSearchParams({ project_id: projectId.value, page: page.value, size })
    if (keyword.value) q.set('keyword', keyword.value)
    if (filterType.value) q.set('case_type', filterType.value)
    if (filterStatus.value) q.set('status', filterStatus.value)
    const d = await get('/wb/cases?' + q)
    cases.value = d.items
    total.value = d.total
  } finally { loading.value = false }
}

function openCreate(row) {
  syncOriginal.value = false
  pickedApi.value = null
  if (row?.id) {
    form.value = { ...row, steps: row.steps?.length ? row.steps : [{ action: '', expected: '' }] }
    // 编辑接口用例：载入快照副本（含空行便于续填）
    const s = row.api_snapshot || {}
    snap.value = {
      request_id: s.request_id || null, name: s.name || row.title,
      method: s.method || 'GET', url: s.url || '',
      headers: (s.headers || []).map(h => ({ key: h.key || '', value: h.value || '', enabled: h.enabled ?? 1 })),
      params: (s.params || []).map(p => ({ key: p.key || '', value: p.value || '', type: p.type || 'string', desc: p.desc || '', enabled: p.enabled ?? 1 })),
      body_type: s.body_type || 'none', body: s.body || '', auth: s.auth || null,
    }
    if (!snap.value.headers.length) snap.value.headers = [{ key: '', value: '', enabled: 1 }]
    if (!snap.value.params.length) snap.value.params = [{ key: '', value: '', type: 'string', desc: '', enabled: 1 }]
  } else {
    form.value = { ...emptyForm, steps: [{ action: '', expected: '' }] }
    snap.value = null
  }
  dlg.value = true
}

async function deprecate(row) {
  await post(`/wb/cases/${row.id}/deprecate`)
  ElMessage.success('已废弃')
  load()
}

function goExec() {
  router.push({
    path: '/exec/functional',
    query: { caseIds: selected.value.map(c => c.id).join(','), projectId: projectId.value }
  })
}

// ---- 移动用例到项目（单个/批量，类型不变，编号按目标项目重新生成） ----
const moveDlg = ref(false)
const moveIds = ref([])
const moveKeyword = ref('')
const moveTarget = ref(null)
const moveProjects = computed(() =>
  projects.value.filter(p => !moveKeyword.value || p.name.toLowerCase().includes(moveKeyword.value.toLowerCase()))
)

function openMove(ids) {
  if (!ids?.length) return
  moveIds.value = ids
  moveKeyword.value = ''
  moveTarget.value = null
  dlg.value = false  // 从详情弹窗发起时关闭编辑弹窗
  moveDlg.value = true
}

async function confirmMove() {
  if (!moveTarget.value) return ElMessage.warning('请选择目标项目')
  try {
    const r = await post('/wb/cases/batch-move', { case_ids: moveIds.value, project_id: moveTarget.value })
    const skipTip = r.skipped?.length ? `，${r.skipped.length} 条已在目标项目跳过` : ''
    ElMessage.success(`已移动 ${r.moved} 条用例到「${projects.value.find(p => p.id === moveTarget.value)?.name || ''}」${skipTip}`)
    moveDlg.value = false
    selected.value = []
    load()
    loadProjects()
  } catch (e) { ElMessage.error(e.message) }
}

onMounted(loadProjects)
</script>

<style scoped>
.op-line { display: flex; align-items: center; white-space: nowrap; }
.op-line :deep(.el-button + .el-button) { margin-left: 10px; }
.op-line :deep(.el-button) { margin-left: 0; }
.case-layout { display: flex; gap: 16px; align-items: flex-start; }
.case-side { width: 240px; flex-shrink: 0; }
.side-title { font-weight: 600; margin-bottom: 10px; font-size: 13px; }
.proj-group { margin-bottom: 4px; }
.proj-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 13px;
}
.proj-caret {
  font-size: 12px; color: #9ca3af; flex-shrink: 0;
  transition: transform 0.18s ease;
}
.proj-caret:hover { color: var(--wb-accent); }
.proj-caret.open { transform: rotate(90deg); }
.proj-item:hover { background: #f3f6fb; }
.proj-item.active { background: var(--wb-accent-soft); color: var(--wb-accent); font-weight: 600; }
.proj-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.type-list { padding: 2px 0 6px 26px; }
.type-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 12.5px; color: #4b5563;
}
.type-item:hover { background: #f3f6fb; }
.type-item.active { background: var(--wb-accent-soft); color: var(--wb-accent); }
.t-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.case-main { flex: 1; min-width: 0; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.step-row { display: flex; gap: 8px; align-items: center; width: 100%; margin-bottom: 6px; }
.step-no { width: 22px; height: 22px; border-radius: 50%; background: var(--wb-accent-soft); color: var(--wb-accent); display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
.move-list { max-height: 280px; overflow-y: auto; border: 1px solid var(--wb-border); border-radius: 8px; }
.move-item {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; cursor: pointer; font-size: 13.5px; color: #374151;
  border-bottom: 1px solid #f1f5f9;
}
.move-item:last-child { border-bottom: none; }
.move-item:hover { background: #f3f6fb; }
.move-item.active { background: var(--wb-accent-soft); color: var(--wb-accent); font-weight: 600; }
.move-item.disabled { color: #9ca3af; cursor: not-allowed; }
.move-name { flex: 1; }
.move-check { color: var(--wb-accent); }

/* 接口用例快照区 */
.snap-block { width: 100%; }
.snap-tip { font-size: 12px; margin-top: 6px; }
.snap-label { font-size: 12px; margin: 8px 0 4px; }
.api-chip { display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 10px; border: 1px solid var(--wb-border); border-radius: 8px; background: #f8fafc; }
.api-chip-name { font-weight: 600; font-size: 13px; }
.chip-url { font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.m-tag { width: 46px; text-align: center; flex-shrink: 0; font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 0; font-family: Consolas, Monaco, monospace; }
.m-get { color: #16a34a; background: #dcfce7; }
.m-post { color: #d97706; background: #fef3c7; }
.m-put { color: #2563eb; background: #dbeafe; }
.m-delete { color: #dc2626; background: #fee2e2; }
.m-patch { color: #7c3aed; background: #ede9fe; }
.mono { font-family: Consolas, Monaco, monospace; }
</style>
