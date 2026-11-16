<template>
  <div class="page">
    <div class="page-title">Bug 管理</div>
    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="全部项目" clearable style="width:220px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-select v-model="status" placeholder="全部状态" clearable style="width:150px" @change="load">
          <el-option v-for="v in statusList" :key="v[0]" :value="v[0]" :label="v[1]" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索标题/编号" style="width:220px" clearable
                  @keyup.enter="load" @clear="load" />
        <el-button type="primary" plain @click="load"><el-icon><Search /></el-icon>搜索</el-button>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>新建 Bug</el-button>
      </div>

      <el-table :data="bugs" v-loading="loading" stripe>
        <el-table-column prop="bug_no" label="编号" width="130" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column v-if="!projectId" prop="project_name" label="所属项目" width="120" show-overflow-tooltip />
        <el-table-column label="严重程度" width="90">
          <template #default="{ row }">{{ sevMap[row.severity] || row.severity }}</template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="70" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag[row.status]">{{ statusMap[row.status] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="90">
          <template #default="{ row }">{{ { manual: '手动', execution_fail: '功能执行', api_test: '接口测试' }[row.source] }}</template>
        </el-table-column>
        <el-table-column prop="assignee" label="处理人" width="90" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="size" :total="total"
                     layout="total, prev, pager, next" style="margin-top:12px; justify-content:flex-end" @current-change="load" />
    </div>

    <!-- 详情抽屉：含关联管理 + 图片上传 -->
    <el-drawer v-model="drawer" size="58%" :title="detail ? `${detail.bug_no} · ${detail.title}` : ''">
      <template v-if="detail">
        <div class="detail-grid">
          <div><span class="muted">状态：</span>
            <el-select v-model="detail.status" size="small" style="width:130px" @change="saveStatus">
              <el-option v-for="v in statusList" :key="v[0]" :value="v[0]" :label="v[1]" />
            </el-select>
          </div>
          <div><span class="muted">严重程度：</span>{{ sevMap[detail.severity] }}</div>
          <div><span class="muted">优先级：</span>{{ detail.priority }}</div>
          <div><span class="muted">处理人：</span>{{ detail.assignee || '—' }}</div>
          <div><span class="muted">发现时间：</span>{{ detail.found_at }}</div>
          <div><span class="muted">关闭时间：</span>{{ detail.closed_at || '—' }}</div>
        </div>
        <div class="desc-block">{{ detail.description }}</div>

        <div class="sec-title">四向关联</div>
        <el-table :data="detail.links" size="small" class="mb12">
          <el-table-column label="类型" width="100">
            <template #default="{ row }">{{ typeMap[row.link_type] }}</template>
          </el-table-column>
          <el-table-column prop="title" label="关联对象" min-width="200" show-overflow-tooltip />
          <el-table-column label="操作" width="80">
            <template #default="{ row }">
              <el-button size="small" link type="danger" @click="removeLink(row)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button size="small" type="primary" plain @click="openLinkDlg">
          <el-icon><Plus /></el-icon>添加关联
        </el-button>

        <div class="sec-title" style="margin-top:22px">截图与附件</div>
        <div class="paste-zone" @paste="onPaste" @drop.prevent="onDrop" @dragover.prevent tabindex="0">
          <el-icon><UploadFilled /></el-icon>
          <span>点击此处后 Ctrl+V 粘贴截图，或拖拽文件到此区域</span>
          <input type="file" multiple hidden ref="fileInput" @change="onPick" />
          <el-button size="small" @click="$refs.fileInput.click()">选择文件</el-button>
        </div>
        <div class="att-grid">
          <div v-for="a in detail.attachments" :key="a.id" class="att-item">
            <template v-if="(a.mime_type || '').startsWith('image/')">
              <el-image :src="`/wb/attachments/${a.id}/file`" fit="cover" class="att-img"
                        :preview-src-list="[`/wb/attachments/${a.id}/file`]" preview-teleported />
            </template>
            <template v-else>
              <a :href="`/wb/attachments/${a.id}/file`" target="_blank" class="muted">{{ a.file_name }}</a>
            </template>
            <div class="att-name">
              <span class="muted" style="font-size:12px">{{ a.file_name }}</span>
              <el-button size="small" link type="danger" @click="removeAtt(a)"><el-icon><Delete /></el-icon></el-button>
            </div>
          </div>
        </div>
      </template>
    </el-drawer>

    <!-- 添加关联弹窗：类型 → 项目 → 对象（需求/用例/计划/日志按项目筛选） -->
    <el-dialog v-model="linkDlg" title="添加关联" width="480px">
      <el-form label-width="80px">
        <el-form-item label="关联类型">
          <el-select v-model="linkForm.type" style="width:100%" @change="linkForm.targetId = null">
            <el-option v-for="v in Object.entries(typeMap)" :key="v[0]" :value="v[0]" :label="v[1]" />
          </el-select>
        </el-form-item>
        <template v-if="linkForm.type === 'requirement' || linkForm.type === 'case' || linkForm.type === 'plan' || linkForm.type === 'log'">
          <el-form-item label="项目">
            <el-select v-model="linkForm.projectId" style="width:100%" placeholder="选择项目" @change="linkForm.targetId = null; loadLinkTargets()">
              <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
          </el-form-item>
          <el-form-item :label="typeMap[linkForm.type]">
            <el-select v-model="linkForm.targetId" style="width:100%" filterable
                       :loading="linkLoading" placeholder="先选项目，再选择对象">
              <el-option v-for="t in linkTargets" :key="t.id" :value="t.id"
                         :label="t.label" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else label="回归执行" />
      </el-form>
      <template #footer>
        <el-button @click="linkDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!linkForm.targetId" @click="addLink">确认关联</el-button>
      </template>
    </el-dialog>

    <!-- 新建：带项目 + 需求选择 -->
    <el-dialog v-model="dlg" title="新建 Bug" width="560px">
      <el-form label-width="80px">
        <el-form-item label="项目" required>
          <el-select v-model="form.project_id" style="width:100%" placeholder="选择所属项目"
                     @change="form.requirement_id = null; loadFormReqs()">
            <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联需求">
          <el-select v-model="form.requirement_id" style="width:100%" clearable filterable
                     :disabled="!form.project_id" :loading="formReqLoading"
                     :placeholder="form.project_id ? '选择需求（可选）' : '先选择项目'">
            <el-option v-for="r in formReqs" :key="r.id" :value="r.id" :label="r.title" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="form.title" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="严重程度">
            <el-select v-model="form.severity">
              <el-option v-for="v in [['fatal','致命'],['serious','严重'],['normal','一般'],['minor','轻微']]" :key="v[0]" :value="v[0]" :label="v[1]" />
            </el-select>
          </el-form-item></el-col>
          <el-col :span="12"><el-form-item label="优先级">
            <el-select v-model="form.priority">
              <el-option v-for="v in ['P0','P1','P2','P3']" :key="v" :value="v" :label="v" />
            </el-select>
          </el-form-item></el-col>
        </el-row>
        <el-form-item label="详情"><el-input v-model="form.description" type="textarea" :rows="5"
          placeholder="复现步骤 / 实际结果 / 期望结果 / 影响范围" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :disabled="!form.project_id" @click="save">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, put, del, uploadAttachment } from '../api'

const projects = ref([])
const projectId = ref(null)
const bugs = ref([])
const loading = ref(false)
const status = ref('')
const keyword = ref('')
const page = ref(1)
const size = 50
const total = ref(0)
const drawer = ref(false)
const detail = ref(null)
const dlg = ref(false)
const fileInput = ref(null)
// 添加关联弹窗状态
const linkDlg = ref(false)
const linkForm = ref({ type: 'requirement', projectId: null, targetId: null })
const linkTargets = ref([])
const linkLoading = ref(false)
// 新建弹窗的需求选项
const formReqs = ref([])
const formReqLoading = ref(false)

const statusList = [['active', '新建'], ['processing', '处理中'], ['fixed', '已修复待回归'], ['regressed', '回归失败'], ['closed', '已关闭'], ['rejected', '已拒绝']]
const statusMap = Object.fromEntries(statusList)
const statusTag = { active: 'danger', processing: 'warning', fixed: 'primary', regressed: 'danger', closed: 'info', rejected: 'info' }
const sevMap = { fatal: '致命', serious: '严重', normal: '一般', minor: '轻微' }
const typeMap = { requirement: '需求', case: '用例', plan: '执行计划', log: '日志', regression_run: '回归执行' }

const emptyForm = { project_id: null, requirement_id: null, title: '', severity: 'normal', priority: 'P1', description: '' }
const form = ref({ ...emptyForm })

onMounted(async () => {
  projects.value = await get('/wb/projects')
  load()  // 默认不选项目：展示所有项目的 Bug
})

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams({ page: page.value, size })
    if (projectId.value) q.set('project_id', projectId.value)
    if (status.value) q.set('status', status.value)
    if (keyword.value) q.set('keyword', keyword.value)
    const d = await get('/wb/bugs?' + q)
    bugs.value = d.items
    total.value = d.total
  } finally { loading.value = false }
}

function openCreate() {
  form.value = { ...emptyForm, project_id: projectId.value }
  if (form.value.project_id) loadFormReqs()
  dlg.value = true
}

async function loadFormReqs() {
  if (!form.value.project_id) { formReqs.value = []; return }
  formReqLoading.value = true
  try { formReqs.value = await get(`/wb/requirements?project_id=${form.value.project_id}`) }
  finally { formReqLoading.value = false }
}

async function save() {
  if (!form.value.title) return ElMessage.warning('请填写标题')
  const links = []
  if (form.value.requirement_id) links.push({ link_type: 'requirement', link_id: form.value.requirement_id })
  await post('/wb/bugs', {
    project_id: form.value.project_id, title: form.value.title,
    severity: form.value.severity, priority: form.value.priority,
    description: form.value.description, links
  })
  ElMessage.success('Bug 已创建')
  dlg.value = false
  load()
}

async function openDetail(row) {
  detail.value = await get(`/wb/bugs/${row.id}`)
  drawer.value = true
}

async function saveStatus() {
  await put(`/wb/bugs/${detail.value.id}`, {
    project_id: detail.value.project_id, title: detail.value.title,
    severity: detail.value.severity, priority: detail.value.priority,
    description: detail.value.description, status: detail.value.status,
    assignee: detail.value.assignee
  })
  ElMessage.success('状态已更新')
  load()
}

// ---- 添加关联（弹窗式：项目 → 对象级联筛选） ----
function openLinkDlg() {
  linkForm.value = { type: 'requirement', projectId: detail.value.project_id, targetId: null }
  linkTargets.value = []
  loadLinkTargets()
  linkDlg.value = true
}

async function loadLinkTargets() {
  const { type, projectId: pid } = linkForm.value
  if (!pid) { linkTargets.value = []; return }
  linkLoading.value = true
  try {
    if (type === 'requirement') {
      linkTargets.value = (await get(`/wb/requirements?project_id=${pid}`)).map(r => ({ id: r.id, label: r.title }))
    } else if (type === 'case') {
      linkTargets.value = (await get(`/wb/cases?project_id=${pid}&size=200&status=active`)).items
        .map(c => ({ id: c.id, label: `${c.case_no} ${c.title}` }))
    } else if (type === 'plan') {
      linkTargets.value = (await get(`/wb/plans?project_id=${pid}`)).map(p => ({ id: p.id, label: p.name }))
    } else if (type === 'log') {
      linkTargets.value = (await get(`/wb/logs?project_id=${pid}&size=200`)).items.map(l => ({ id: l.id, label: l.title }))
    }
  } finally { linkLoading.value = false }
}

async function addLink() {
  await post(`/wb/bugs/${detail.value.id}/links`, { link_type: linkForm.value.type, link_id: linkForm.value.targetId })
  ElMessage.success('已关联')
  linkDlg.value = false
  detail.value = await get(`/wb/bugs/${detail.value.id}`)
}

async function removeLink(row) {
  await del(`/wb/bug-links/${row.id}`)
  detail.value = await get(`/wb/bugs/${detail.value.id}`)
}

async function uploadFiles(files) {
  for (const f of files) {
    await uploadAttachment('bug', detail.value.id, f)
  }
  ElMessage.success(`已上传 ${files.length} 个附件`)
  detail.value = await get(`/wb/bugs/${detail.value.id}`)
}

function onPaste(e) {
  const files = [...(e.clipboardData?.items || [])]
    .filter(i => i.kind === 'file').map(i => i.getAsFile()).filter(Boolean)
  if (files.length) uploadFiles(files)
}
function onDrop(e) {
  const files = [...(e.dataTransfer?.files || [])]
  if (files.length) uploadFiles(files)
}
function onPick(e) {
  const files = [...e.target.files]
  if (files.length) uploadFiles(files)
  e.target.value = ''
}

async function removeAtt(a) {
  await del(`/wb/attachments/${a.id}`)
  detail.value = await get(`/wb/bugs/${detail.value.id}`)
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-bottom: 14px; }
.desc-block {
  white-space: pre-wrap; background: #f8fafc; border: 1px solid var(--wb-border);
  border-radius: 8px; padding: 12px; font-size: 13px; margin-bottom: 18px;
}
.sec-title { font-weight: 600; margin-bottom: 10px; font-size: 14px; }
.paste-zone {
  display: flex; align-items: center; gap: 10px;
  border: 1.5px dashed #c0cfe8; border-radius: 10px; padding: 14px;
  color: var(--wb-text-sub); font-size: 13px; margin-bottom: 14px; outline: none;
  background: #f8fafc;
}
.paste-zone:focus { border-color: var(--wb-accent); }
.att-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.att-item { border: 1px solid var(--wb-border); border-radius: 8px; padding: 6px; }
.att-img { width: 100%; height: 110px; border-radius: 6px; }
.att-name { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
</style>
