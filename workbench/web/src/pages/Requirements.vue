<template>
  <div class="page">
    <div class="page-title">需求管理</div>

    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="全部项目" clearable style="width:200px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width:150px" @change="load">
          <el-option v-for="(v, k) in statusMap" :key="k" :value="k" :label="v.label" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索需求标题" style="width:220px" clearable
                  @keyup.enter="load" @clear="load" />
        <el-button type="primary" plain @click="load"><el-icon><Search /></el-icon>搜索</el-button>
        <div class="spacer" />
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>新建需求</el-button>
      </div>

      <el-table ref="tableRef" :data="reqs" v-loading="loading" stripe row-key="id" @expand-change="onExpand">
        <!-- 展开行：测试点列表（可直接增删） -->
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="tp-wrap">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span class="muted" style="font-size:12.5px">「{{ row.title }}」测试点（AI 拆解后在此跟踪覆盖状态）</span>
                <el-button size="small" type="primary" plain @click="openAddTp(row)"><el-icon><Plus /></el-icon>添加测试点</el-button>
              </div>
              <el-table :data="tps[row.id] || []" size="small" v-loading="tpLoading[row.id]">
                <el-table-column prop="title" label="测试点" min-width="200" />
                <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip />
                <el-table-column prop="priority" label="优先级" width="80" />
                <el-table-column label="覆盖状态" width="110">
                  <template #default="{ row: tp }">
                    <el-tag size="small" :type="tp.status === 'covered' ? 'success' : 'warning'">
                      {{ tp.status === 'covered' ? '已覆盖' : '待覆盖' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="110">
                  <template #default="{ row: tp }">
                    <el-button size="small" link type="primary" @click="openEditTp(row, tp)">编辑</el-button>
                    <el-popconfirm title="删除该测试点？" @confirm="removeTp(row, tp)">
                      <template #reference><el-button size="small" link type="danger">删除</el-button></template>
                    </el-popconfirm>
                  </template>
                </el-table-column>
                <template #empty><span class="muted">暂无测试点</span></template>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <!-- 点击标题展开/收起测试点 -->
        <el-table-column label="需求标题" min-width="200">
          <template #default="{ row }">
            <span class="req-title" @click="toggleExpand(row)">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="project_name" label="所属项目" width="130" show-overflow-tooltip />
        <!-- 状态：下拉直接修改 -->
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-select :model-value="row.status" size="small" style="width:105px" @change="v => changeStatus(row, v)">
              <el-option v-for="(v, k) in statusMap" :key="k" :value="k" :label="v.label" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="test_point_count" label="测试点" width="76" />
        <el-table-column prop="version" label="版本" width="70" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="删除需求将级联删除其测试点，确认？" @confirm="removeReq(row)">
              <template #reference><el-button size="small" link type="danger">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !reqs.length" description="还没有需求，点击右上角「新建需求」开始" :image-size="90" />
    </div>

    <!-- 新建/编辑需求 -->
    <el-dialog v-model="dlg" :title="form.id ? '编辑需求' : '新建需求'" width="560px">
      <el-form label-width="80px">
        <el-form-item label="所属项目" required>
          <el-select v-model="form.project_id" :disabled="!!form.id" placeholder="选择项目" style="width:100%">
            <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="form.title" placeholder="如 登录功能" /></el-form-item>
        <el-form-item label="正文">
          <el-input v-model="form.content" type="textarea" :rows="5" placeholder="粘贴 PRD 片段或手写需求描述" />
        </el-form-item>
        <el-form-item label="版本"><el-input v-model="form.version" style="width:120px" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 添加/编辑测试点 -->
    <el-dialog v-model="tpDlg" :title="`${tpForm.id ? '编辑' : '添加'}测试点（${tpReqTitle}）`" width="480px">
      <el-form label-width="70px">
        <el-form-item label="测试点" required><el-input v-model="tpForm.title" placeholder="如 验证码正确登录成功" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="tpForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="tpForm.priority" style="width:120px">
            <el-option v-for="v in ['P0', 'P1', 'P2', 'P3']" :key="v" :value="v" :label="v" />
          </el-select>
        </el-form-item>
        <el-form-item label="覆盖状态">
          <el-select v-model="tpForm.status" style="width:120px">
            <el-option label="待覆盖" value="pending" />
            <el-option label="已覆盖" value="covered" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="success" plain style="float:left; margin-left:0"
                   @click="openCreateCase"><el-icon><Plus /></el-icon>新建用例</el-button>
        <el-button @click="tpDlg = false">取消</el-button>
        <el-button type="primary" @click="saveTp">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试点新建用例：自动落到对应项目用例库并置测试点为已覆盖 -->
    <el-dialog v-model="caseDlg" :title="`新建用例（关联测试点：${caseForm.tpTitle}）`" width="620px">
      <el-form label-width="70px">
        <el-form-item label="所属项目" required>
          <el-select v-model="caseForm.project_id" style="width:100%" disabled>
            <el-option :value="caseForm.project_id" :label="caseForm.project_name" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="caseForm.title" /></el-form-item>
        <el-form-item label="用例类型" required>
          <el-radio-group v-model="caseForm.case_type">
            <el-radio-button value="functional">功能</el-radio-button>
            <el-radio-button value="api">接口</el-radio-button>
            <el-radio-button value="automation">自动化</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <!-- 接口用例：从接口管理选择接口并生成参数快照 -->
        <el-form-item v-if="caseForm.case_type === 'api'" label="接口">
          <div style="width: 100%">
            <el-button v-if="!pickedApi" @click="apiPickDlg = true"><el-icon><Plus /></el-icon>选择接口</el-button>
            <div v-else class="api-chip">
              <span class="m-tag" :class="'m-' + pickedApi.method.toLowerCase()">{{ pickedApi.method }}</span>
              <span class="api-chip-name">{{ pickedApi.name }}</span>
              <span class="muted chip-url">{{ pickedApi.url }}</span>
              <el-button link type="primary" size="small" @click="apiPickDlg = true">更换</el-button>
              <el-button link type="danger" size="small" @click="pickedApi = null">移除</el-button>
            </div>
            <div class="muted snap-tip">按所选接口当前参数生成快照归档到用例，不影响接口管理中的原接口</div>
          </div>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="caseForm.priority" style="width:120px">
            <el-option v-for="v in ['P0', 'P1', 'P2', 'P3']" :key="v" :value="v" :label="v" />
          </el-select>
        </el-form-item>
        <el-form-item label="前置"><el-input v-model="caseForm.precondition" placeholder="无前置可留空" /></el-form-item>
        <el-form-item label="步骤">
          <div style="width:100%">
            <div v-for="(s, i) in caseForm.steps" :key="i" style="display:flex; gap:6px; margin-bottom:6px;">
              <el-input v-model="s.action" placeholder="操作" style="flex:1" />
              <el-input v-model="s.expected" placeholder="预期" style="flex:1" />
              <el-button link type="danger" @click="caseForm.steps.splice(i, 1)"><el-icon><Delete /></el-icon></el-button>
            </div>
            <el-button size="small" @click="caseForm.steps.push({ action: '', expected: '' })"><el-icon><Plus /></el-icon>添加步骤</el-button>
          </div>
        </el-form-item>
        <el-form-item label="预期"><el-input v-model="caseForm.expected" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="caseDlg = false">取消</el-button>
        <el-button type="primary" @click="saveCase">保存到用例库</el-button>
      </template>
    </el-dialog>

    <!-- 接口选择弹窗 -->
    <api-pick-dialog v-model="apiPickDlg" :project-id="caseForm.project_id" @select="onApiPicked" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post, put, del } from '../api'
import ApiPickDialog from '../components/ApiPickDialog.vue'

const route = useRoute()
const projects = ref([])
const projectId = ref(null)
const status = ref(null)
const keyword = ref('')
const reqs = ref([])
const loading = ref(true)
const tps = reactive({})
const tpLoading = reactive({})

const statusMap = {
  pending: { label: '待分析', type: 'info' },
  split: { label: '已拆解', type: 'warning' },
  covered: { label: '已覆盖', type: 'primary' },
  passed: { label: '测试通过', type: 'success' }
}

const dlg = ref(false)
const emptyForm = { id: null, project_id: null, title: '', content: '', version: 'v1' }
const form = ref({ ...emptyForm })

const tpDlg = ref(false)
const tpReq = ref(null)
const tpReqTitle = ref('')
const tpForm = ref({ title: '', description: '', priority: 'P0' })

// 测试点新建用例
const caseDlg = ref(false)
const caseTp = ref(null)
const emptyCaseForm = { tpTitle: '', project_id: null, project_name: '', title: '', case_type: 'functional', priority: 'P0', precondition: '', steps: [{ action: '', expected: '' }], expected: '' }
const caseForm = ref({ ...emptyCaseForm })

// 接口用例选择（快照）
const apiPickDlg = ref(false)
const pickedApi = ref(null)

function onApiPicked(r) {
  pickedApi.value = r
  if (!caseForm.value.title) caseForm.value.title = r.name
  if (!caseForm.value.precondition) caseForm.value.precondition = `接口可访问：${r.url}`
  if (caseForm.value.steps.length === 1 && !caseForm.value.steps[0].action) {
    caseForm.value.steps = [{ action: `${r.method} ${r.url}`, expected: '响应状态码 2xx，返回符合预期' }]
  }
}

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (route.query.projectId) projectId.value = Number(route.query.projectId)
  await load()
  // 从项目详情「去需求管理修改」跳转：直接展开该需求并打开编辑
  if (route.query.editId) {
    const row = reqs.value.find(r => r.id === Number(route.query.editId))
    if (row) { openEdit(row) }
  }
})

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams()
    if (projectId.value) q.set('project_id', projectId.value)
    if (status.value) q.set('status', status.value)
    if (keyword.value) q.set('keyword', keyword.value)
    reqs.value = await get('/wb/requirements?' + q)
  } finally { loading.value = false }
}

const tableRef = ref()
function toggleExpand(row) {
  tableRef.value?.toggleRowExpansion(row)
}

async function onExpand(row, expandedRows) {
  if (expandedRows.includes(row)) await loadTps(row)
}

async function loadTps(row) {
  tpLoading[row.id] = true
  try { tps[row.id] = await get(`/wb/requirements/${row.id}/test-points`) }
  finally { tpLoading[row.id] = false }
}

function openCreate() {
  form.value = { ...emptyForm, project_id: projectId.value || projects.value[0]?.id }
  dlg.value = true
}

function openEdit(row) {
  form.value = { id: row.id, project_id: row.project_id, title: row.title, content: row.content || '', version: row.version || 'v1' }
  dlg.value = true
}

async function save() {
  if (!form.value.project_id) return ElMessage.warning('请选择所属项目')
  if (!form.value.title) return ElMessage.warning('请填写标题')
  if (form.value.id) {
    await put(`/wb/requirements/${form.value.id}`, {
      title: form.value.title, content: form.value.content, version: form.value.version
    })
    ElMessage.success('需求已更新')
  } else {
    await post('/wb/requirements', {
      project_id: form.value.project_id, title: form.value.title,
      content: form.value.content, version: form.value.version
    })
    ElMessage.success('需求已创建')
  }
  dlg.value = false
  load()
}

async function changeStatus(row, v) {
  await put(`/wb/requirements/${row.id}`, { status: v })
  row.status = v
  ElMessage.success('状态已更新：' + statusMap[v]?.label)
}

async function removeReq(row) {
  await del(`/wb/requirements/${row.id}`)
  ElMessage.success('已删除')
  load()
}

function openAddTp(row) {
  tpReq.value = row
  tpReqTitle.value = row.title
  tpForm.value = { id: null, title: '', description: '', priority: 'P0', status: 'pending' }
  tpDlg.value = true
}

function openEditTp(row, tp) {
  tpReq.value = row
  tpReqTitle.value = row.title
  tpForm.value = { id: tp.id, title: tp.title, description: tp.description || '', priority: tp.priority || 'P0', status: tp.status || 'pending' }
  tpDlg.value = true
}

async function saveTp() {
  if (!tpForm.value.title) return ElMessage.warning('请填写测试点内容')
  if (tpForm.value.id) {
    await put(`/wb/test-points/${tpForm.value.id}`, {
      title: tpForm.value.title, description: tpForm.value.description,
      priority: tpForm.value.priority, status: tpForm.value.status
    })
    ElMessage.success('测试点已更新')
  } else {
    await post(`/wb/requirements/${tpReq.value.id}/test-points`, {
      title: tpForm.value.title, description: tpForm.value.description,
      priority: tpForm.value.priority, status: tpForm.value.status
    })
    ElMessage.success('测试点已添加')
  }
  tpDlg.value = false
  loadTps(tpReq.value)
  load()
}

async function removeTp(row, tp) {
  await del(`/wb/test-points/${tp.id}`)
  loadTps(row)
  load()
}

// ---- 测试点新建用例：写入对应项目用例库，后端自动将测试点置为已覆盖 ----
function openCreateCase() {
  const tp = tpForm.value
  caseTp.value = tpReq.value  // 仅用于刷新测试点列表的需求行
  caseForm.value = {
    tpTitle: tp.title,
    project_id: tpReq.value.project_id,
    project_name: projects.value.find(p => p.id === tpReq.value.project_id)?.name || '',
    title: tp.title,
    case_type: 'functional',
    priority: tp.priority || 'P0',
    precondition: '',
    // 用测试点说明作为首条步骤操作，便于快速起步
    steps: [{ action: tp.description || tp.title, expected: '' }],
    expected: ''
  }
  pickedApi.value = null
  caseDlg.value = true
}

async function saveCase() {
  const f = caseForm.value
  if (!f.title) return ElMessage.warning('请填写用例标题')
  if (f.case_type === 'api' && !pickedApi.value) return ElMessage.warning('接口用例请先从接口管理选择一个接口')
  // 关联的测试点必须是真实落库的测试点 id：
  // - 编辑已有测试点：tpForm.id 即测试点 id
  // - 新增测试点（尚未保存）：先创建该测试点，再用它的 id 关联用例
  let tpId = tpForm.value.id
  if (!tpId) {
    const r = await post(`/wb/requirements/${tpReq.value.id}/test-points`, {
      title: tpForm.value.title, description: tpForm.value.description,
      priority: tpForm.value.priority, status: tpForm.value.status || 'pending'
    })
    tpId = r.id
    tpForm.value.id = tpId
  }
  const steps = f.steps.filter(s => s.action || s.expected).map((s, i) => ({ step: i + 1, action: s.action, expected: s.expected }))
  await post('/wb/cases', {
    project_id: f.project_id,
    title: f.title,
    case_type: f.case_type,
    priority: f.priority,
    precondition: f.precondition,
    steps,
    expected: f.expected,
    test_point_id: tpId,
    // 接口用例：以快照归档所选接口参数，与接口管理原接口解耦
    api_snapshot: f.case_type === 'api' && pickedApi.value ? {
      request_id: pickedApi.value.id, name: pickedApi.value.name,
      method: pickedApi.value.method || 'GET', url: pickedApi.value.url || '',
      headers: pickedApi.value.headers || [], params: pickedApi.value.params || [],
      body_type: pickedApi.value.body_type || 'none', body: pickedApi.value.body || '',
      auth: pickedApi.value.auth || null, description: pickedApi.value.description || '',
    } : null,
  })
  ElMessage.success('用例已创建并关联到用例库，测试点已置为已覆盖')
  caseDlg.value = false
  tpDlg.value = false
  pickedApi.value = null
  loadTps(tpReq.value)
  load()
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.toolbar .spacer { flex: 1; }
.req-title { cursor: pointer; color: var(--wb-accent); font-weight: 500; }
.req-title:hover { text-decoration: underline; }
.tp-wrap { padding: 4px 16px 8px 48px; }

/* 接口用例选择 */
.api-chip { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid var(--wb-border); border-radius: 8px; background: #f8fafc; }
.api-chip-name { font-weight: 600; font-size: 13px; }
.chip-url { font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.snap-tip { font-size: 12px; margin-top: 6px; }
.m-tag { width: 46px; text-align: center; flex-shrink: 0; font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 0; font-family: Consolas, Monaco, monospace; }
.m-get { color: #16a34a; background: #dcfce7; }
.m-post { color: #d97706; background: #fef3c7; }
.m-put { color: #2563eb; background: #dbeafe; }
.m-delete { color: #dc2626; background: #fee2e2; }
.m-patch { color: #7c3aed; background: #ede9fe; }
</style>
