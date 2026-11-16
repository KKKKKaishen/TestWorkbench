<template>
  <div class="page">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
      <div class="page-title" style="margin:0;">项目管理</div>
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>新建项目</el-button>
    </div>

    <el-table :data="projects" v-loading="loading" stripe>
      <el-table-column prop="code" label="代号" width="110" />
      <el-table-column prop="name" label="项目名称" min-width="160" />
      <el-table-column label="用例规模" min-width="180">
        <template #default="{ row }">
          功能 {{ row.stats.cases.functional || 0 }} · 接口 {{ row.stats.cases.api || 0 }} · 自动化 {{ row.stats.cases.automation || 0 }}
        </template>
      </el-table-column>
      <el-table-column label="待处理 Bug" width="110">
        <template #default="{ row }">
          <el-badge :value="row.stats.bugs_open" :hidden="!row.stats.bugs_open" type="danger" class="bug-badge">
            <span>{{ row.stats.bugs_open }}</span>
          </el-badge>
        </template>
      </el-table-column>
      <el-table-column prop="owner_name" label="负责人" width="100" />
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
            {{ row.status === 'active' ? '进行中' : '已归档' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">详情</el-button>
          <el-button size="small" @click="openCreate(row)">编辑</el-button>
          <el-popconfirm title="删除项目将级联删除其全部数据，确认？" @confirm="removeProject(row)">
            <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑 -->
    <el-dialog v-model="dlg" :title="form.id ? '编辑项目' : '新建项目'" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="项目名称" required><el-input v-model="form.name" placeholder="如 商城小程序" /></el-form-item>
        <el-form-item label="项目代号" required>
          <el-input v-model="form.code" :disabled="!!form.id" placeholder="字母开头，如 SHOP（用作用例/Bug 编号前缀）" />
        </el-form-item>
        <el-form-item label="负责人"><el-input v-model="form.owner_name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉：需求 Tab + 统计 -->
    <el-drawer v-model="drawer" :title="detail ? `${detail.name} · 项目工作区` : ''" size="62%">
      <template v-if="detail">
        <el-tabs>
          <el-tab-pane label="需求 / 测试点">
            <div style="display:flex; justify-content:space-between; margin-bottom:12px;">
              <span class="muted">点击需求标题展开测试点；需求的状态与内容请到「需求管理」维护</span>
              <div>
                <el-button size="small" type="primary" @click="openLinkReq">添加需求</el-button>
                <el-button size="small" @click="openNewReq"><el-icon><Plus /></el-icon>新建需求</el-button>
              </div>
            </div>
            <el-table ref="reqTableRef" :data="requirements" size="small" @expand-change="onReqExpand">
              <!-- 点击标题展开测试点 -->
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="tp-block">
                    <div class="muted mb12">「{{ row.title }}」测试点</div>
                    <el-table :data="testPoints[row.id] || []" size="small">
                      <el-table-column prop="title" label="测试点" min-width="180" />
                      <el-table-column prop="priority" label="优先级" width="80" />
                      <el-table-column label="覆盖状态" width="100">
                        <template #default="{ row: tp }">
                          <el-tag size="small" :type="tp.status === 'covered' ? 'success' : 'warning'">
                            {{ tp.status === 'covered' ? '已覆盖' : '待覆盖' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="需求标题" min-width="150">
                <template #default="{ row }">
                  <span class="req-title" @click="reqTableRef?.toggleRowExpansion(row)">{{ row.title }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusMap[row.status]?.type || 'info'">{{ statusMap[row.status]?.label || row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="test_point_count" label="测试点" width="80" />
              <el-table-column prop="version" label="版本" width="70" />
              <el-table-column label="操作" width="150">
                <template #default="{ row }">
                  <el-button size="small" link type="primary"
                             @click="$router.push({ path: '/requirements', query: { projectId: detail.id, editId: row.id } })">去修改</el-button>
                  <el-popconfirm title="删除需求将级联删除其测试点，确认？" @confirm="removeReq(row)">
                    <template #reference><el-button size="small" link type="danger">删除</el-button></template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="最近执行">
            <el-table :data="detail.stats.recent_plans" size="small">
              <el-table-column prop="name" label="计划" min-width="150" />
              <el-table-column label="类型" width="100">
                <template #default="{ row }">{{ planTypeMap[row.plan_type] || row.plan_type }}</template>
              </el-table-column>
              <el-table-column label="进度" width="180">
                <template #default="{ row }">{{ row.pass + row.fail + row.block + row.skip }} / {{ row.total }}</template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="planStatusMap[row.status]?.type || 'info'">
                    {{ planStatusMap[row.status]?.label || row.status }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <!-- 添加需求：从需求管理列表拉取 -->
    <el-dialog v-model="reqDlg" title="添加需求（从需求库拉取）" width="560px">
      <el-form label-width="80px">
        <el-form-item label="筛选项目">
          <el-select v-model="linkProjectId" style="width:100%" @change="loadLinkCandidates">
            <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="选择需求">
          <el-select v-model="linkIds" multiple filterable placeholder="可搜索多选" style="width:100%">
            <el-option v-for="r in linkCandidates" :key="r.id" :value="r.id"
                       :label="`${r.title}（${r.project_name}）`" :disabled="r.project_id === detail.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <span class="muted" style="font-size:12px">同项目的需求默认已在下方列表展示；勾选其他项目的需求会将其拉取纳入本项目。没有想要的？点「新建需求」。</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reqDlg = false">取消</el-button>
        <el-button type="primary" @click="doLinkReq">确认拉取</el-button>
      </template>
    </el-dialog>

    <!-- 新建需求：直接建到需求管理（本项目） -->
    <el-dialog v-model="newReqDlg" title="新建需求" width="560px">
      <el-form label-width="80px">
        <el-form-item label="所属项目" required>
          <el-select v-model="newReqForm.project_id" style="width:100%">
            <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="newReqForm.title" placeholder="如 登录功能" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="newReqForm.content" type="textarea" :rows="5" placeholder="粘贴 PRD 片段或手写需求描述" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newReqDlg = false">取消</el-button>
        <el-button type="primary" @click="doNewReq">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post, put, del } from '../api'

const route = useRoute()
const projects = ref([])
const loading = ref(true)
const dlg = ref(false)
const drawer = ref(false)
const detail = ref(null)
const requirements = ref([])
const testPoints = ref({})
const reqTableRef = ref()

// 添加需求（从需求库拉取）
const reqDlg = ref(false)
const linkProjectId = ref(null)
const linkCandidates = ref([])
const linkIds = ref([])

// 新建需求
const newReqDlg = ref(false)
const newReqForm = ref({ project_id: null, title: '', content: '' })
const statusMap = {
  pending: { label: '待分析', type: 'info' },
  split: { label: '已拆解', type: 'warning' },
  covered: { label: '已覆盖', type: 'primary' },
  passed: { label: '测试通过', type: 'success' }
}
const planTypeMap = { functional: '功能测试', api: '接口测试', automation: '自动化测试' }
const planStatusMap = {
  running: { label: '执行中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  aborted: { label: '已中止', type: 'info' }
}
const emptyForm = { name: '', code: '', owner_name: '', description: '' }
const form = ref({ ...emptyForm })

async function load() {
  loading.value = true
  try { projects.value = await get('/wb/projects') } finally { loading.value = false }
}
onMounted(load)

function openCreate(row) {
  form.value = row?.id ? { id: row.id, name: row.name, code: row.code, owner_name: row.owner_name || '', description: row.description || '' } : { ...emptyForm }
  dlg.value = true
}

async function save() {
  try {
    if (form.value.id) await put(`/wb/projects/${form.value.id}`, form.value)
    else await post('/wb/projects', form.value)
    ElMessage.success('已保存')
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(e.message) }
}

async function removeProject(row) {
  await del(`/wb/projects/${row.id}`)
  ElMessage.success('已删除')
  load()
}

async function openDetail(row) {
  detail.value = await get(`/wb/projects/${row.id}`)
  drawer.value = true
  loadReqs()
}

async function loadReqs() {
  requirements.value = await get(`/wb/requirements?project_id=${detail.value.id}`)
}

async function onReqExpand(row, expandedRows) {
  if (expandedRows.includes(row) && !testPoints.value[row.id]) {
    testPoints.value = { ...testPoints.value, [row.id]: await get(`/wb/requirements/${row.id}/test-points`) }
  }
}

// ---- 添加需求：从需求库拉取 ----
async function openLinkReq() {
  linkProjectId.value = detail.value.id
  linkIds.value = []
  await loadLinkCandidates()
  reqDlg.value = true
}

async function loadLinkCandidates() {
  linkCandidates.value = await get(`/wb/requirements?project_id=${linkProjectId.value || ''}`)
}

async function doLinkReq() {
  if (!linkIds.value.length) return ElMessage.warning('请先勾选需求')
  const moved = [], skipped = []
  for (const rid of linkIds.value) {
    const r = linkCandidates.value.find(x => x.id === rid)
    if (!r) continue
    if (r.project_id === detail.value.id) { skipped.push(r.title); continue }
    await put(`/wb/requirements/${rid}`, { project_id: detail.value.id })
    moved.push(r.title)
  }
  reqDlg.value = false
  if (moved.length) ElMessage.success(`已拉取 ${moved.length} 条需求纳入本项目`)
  else if (skipped.length) ElMessage.info('所选需求已属于本项目，无需重复添加')
  loadReqs()
}

// ---- 新建需求：建到需求管理（默认本项目） ----
function openNewReq() {
  newReqForm.value = { project_id: detail.value.id, title: '', content: '' }
  newReqDlg.value = true
}

async function doNewReq() {
  if (!newReqForm.value.title) return ElMessage.warning('请填写标题')
  await post('/wb/requirements', newReqForm.value)
  ElMessage.success('需求已创建（可在需求管理中维护状态与测试点）')
  newReqDlg.value = false
  loadReqs()
}

async function removeReq(row) {
  await del(`/wb/requirements/${row.id}`)
  loadReqs()
}

watch(() => route.query.focus, (v) => {
  if (v) {
    const row = projects.value.find(p => p.id === Number(v))
    if (row) openDetail(row)
  }
})
</script>

<style scoped>
.bug-badge :deep(.el-badge__content) { position: static; transform: none; margin-left: 6px; }
.tp-block { padding: 4px 16px 8px 48px; }
.req-title { cursor: pointer; color: var(--wb-accent); font-weight: 500; }
.req-title:hover { text-decoration: underline; }
</style>
