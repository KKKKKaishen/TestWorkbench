<template>
  <div class="page">
    <div class="page-title">功能测试执行</div>

    <!-- 首页：测试计划列表（不再直接展示所有用例） -->
    <div v-if="!plan" class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="全部项目" clearable style="width:220px" @change="loadPlans">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索计划名称" style="width:220px" clearable
                  @keyup.enter="loadPlans" @clear="loadPlans" />
        <el-button type="primary" plain @click="loadPlans"><el-icon><Search /></el-icon>搜索</el-button>
        <el-button type="primary" @click="openCreatePlan"><el-icon><Plus /></el-icon>新建测试计划</el-button>
      </div>

      <el-table :data="plans" v-loading="plansLoading" stripe>
        <el-table-column prop="name" label="计划名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="project_name" label="项目" width="120" show-overflow-tooltip />
        <el-table-column prop="requirement_title" label="关联需求" width="140" show-overflow-tooltip />
        <el-table-column label="执行进度" width="170">
          <template #default="{ row }">
            <el-progress :percentage="progressPct(row)" :stroke-width="10"
                         :color="progressColor(row)" style="width:140px" />
          </template>
        </el-table-column>
        <el-table-column label="通过/失败" width="110">
          <template #default="{ row }">
            <span class="pass-num">{{ row.pass_count }}</span> / <span class="fail-num">{{ row.fail_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="{ running: 'primary', completed: 'success', aborted: 'info' }[row.status]">
              {{ { running: '执行中', completed: '已完成', aborted: '已中止' }[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openPlan(row.id)">进入执行</el-button>
            <el-popconfirm title="删除该计划及其执行记录？" @confirm="removePlan(row)">
              <template #reference><el-button size="small" link type="danger">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!plansLoading && !plans.length" description="还没有测试计划，点击右上角「新建测试计划」开始" :image-size="90" />
    </div>

    <!-- 计划内：左用例清单（多选/批量）+ 右详情（可编辑快照 + 点单执行） -->
    <div v-else class="exec-layout">
      <div class="card exec-list">
        <div class="exec-head">
          <div style="min-width:0">
            <div style="font-weight:600">{{ plan.name }}</div>
            <div class="muted" style="font-size:12px">
              {{ plan.project_name }}<template v-if="plan.requirement_title"> · {{ plan.requirement_title }}</template>
            </div>
            <div class="muted">通过 {{ plan.pass_count }} · 失败 {{ plan.fail_count }} · 阻塞 {{ plan.block_count }} · 跳过 {{ plan.skip_count }} / 共 {{ plan.total_count }}</div>
          </div>
          <el-button @click="plan = null; loadPlans()">返回列表</el-button>
        </div>

        <div class="batch-bar">
          <span class="muted" style="font-size:12px">已选 {{ checkedPlanCases.length }} 项</span>
          <el-button size="small" type="success" plain :disabled="!checkedPlanCases.length" @click="batchMark('pass')">批量通过</el-button>
          <el-button size="small" type="danger" plain :disabled="!checkedPlanCases.length" @click="batchMark('fail')">批量失败</el-button>
          <el-button size="small" type="warning" plain :disabled="!checkedPlanCases.length" @click="batchMark('block')">批量阻塞</el-button>
          <el-button size="small" plain :disabled="!checkedPlanCases.length" @click="batchMark('skip')">批量跳过</el-button>
          <el-button size="small" plain :disabled="!checkedPlanCases.length" @click="batchMark('pending')">批量未执行</el-button>
        </div>

        <div style="max-height: 420px; overflow-y:auto">
          <div v-for="c in plan.cases" :key="c.plan_case_id" class="exec-item"
               :class="{ active: current?.plan_case_id === c.plan_case_id }" @click="selectCase(c)">
            <el-checkbox :model-value="checkedPlanCases.includes(c.plan_case_id)"
                         @click.stop @change="v => toggleCheck(c.plan_case_id, v)" style="margin-right:2px" />
            <el-tag size="small" :type="resultTag(c.result)" class="res-tag">{{ resultLabel(c.result) }}</el-tag>
            <div class="exec-item-main">
              <div>
                {{ c.case_no }} {{ c.title }}
                <el-tooltip v-if="c.edited" content="该用例在本计划内被修改过（不影响用例库原用例）">
                  <el-icon style="color:#f59e0b; vertical-align:-2px"><EditPen /></el-icon>
                </el-tooltip>
              </div>
              <div class="muted">{{ c.module_path || '—' }} · {{ c.priority || '—' }}</div>
            </div>
            <el-button size="small" link type="danger" @click.stop="removeCase(c)" title="移出计划"><el-icon><Delete /></el-icon></el-button>
          </div>
        </div>
        <el-button style="width:100%; margin-top:10px" plain @click="openAddCases">
          <el-icon><Plus /></el-icon>添加用例
        </el-button>
      </div>

      <div class="card exec-panel" v-if="current">
        <div class="panel-title-row">
          <span style="font-weight:600; font-size:15px;">{{ current.case_no }}</span>
          <el-tag v-if="current.edited" size="small" type="warning" effect="plain">计划内已修改</el-tag>
          <el-button size="small" link type="primary" @click="editOpen = !editOpen">
            {{ editOpen ? '收起编辑' : '修改用例内容' }}
          </el-button>
        </div>

        <!-- 计划内内容编辑（第 9 项：只改本计划副本） -->
        <div v-if="editOpen" class="edit-block">
          <el-form label-width="70px" size="small">
            <el-form-item label="标题"><el-input v-model="editForm.title" /></el-form-item>
            <el-row :gutter="12">
              <el-col :span="8"><el-form-item label="优先级">
                <el-select v-model="editForm.priority" clearable>
                  <el-option v-for="v in ['P0','P1','P2','P3']" :key="v" :value="v" :label="v" />
                </el-select>
              </el-form-item></el-col>
            </el-row>
            <el-form-item label="前置"><el-input v-model="editForm.precondition" type="textarea" :rows="2" /></el-form-item>
            <el-form-item label="步骤">
              <div v-for="(s, i) in editForm.steps" :key="i" class="step-row">
                <span class="step-no">{{ i + 1 }}</span>
                <el-input v-model="s.action" placeholder="操作" style="flex:1" />
                <el-input v-model="s.expected" placeholder="预期" style="flex:1" />
                <el-button link type="danger" @click="editForm.steps.splice(i, 1)"><el-icon><Delete /></el-icon></el-button>
              </div>
              <el-button size="small" @click="editForm.steps.push({ action: '', expected: '' })"><el-icon><Plus /></el-icon>添加步骤</el-button>
            </el-form-item>
            <el-form-item label="预期"><el-input v-model="editForm.expected" /></el-form-item>
          </el-form>
          <el-button size="small" type="primary" @click="saveEdit(false)">保存修改（仅本计划）</el-button>
          <el-button size="small" type="warning" @click="saveEdit(true)">保存修改（同时修改原用例）</el-button>
        </div>

        <template v-else>
          <div style="font-weight:600">{{ current.title }}</div>
          <div class="muted mb12">前置：{{ current.precondition || '无' }}</div>
          <el-table :data="current.steps || []" size="small" class="mb12">
            <el-table-column prop="step" label="#" width="46" />
            <el-table-column prop="action" label="操作步骤" min-width="180" />
            <el-table-column prop="expected" label="预期" min-width="160" />
          </el-table>
          <div class="muted mb12">整体预期：{{ current.expected || '—' }}</div>
        </template>

        <el-form label-width="70px">
          <el-form-item label="实际结果"><el-input v-model="resultForm.actual_result" type="textarea" :rows="3" placeholder="填写实际观察到的结果" /></el-form-item>
          <el-form-item label="备注"><el-input v-model="resultForm.note" /></el-form-item>
        </el-form>
        <div class="result-btns">
          <el-button type="success" @click="submit('pass')">通过</el-button>
          <el-button type="danger" @click="submit('fail')">失败</el-button>
          <el-button type="warning" @click="submit('block')">阻塞</el-button>
          <el-button plain @click="submit('skip')">跳过</el-button>
          <el-button plain @click="submit('pending')">未执行</el-button>
          <el-button type="danger" plain @click="toBug">转 Bug</el-button>
        </div>
      </div>
      <div class="card exec-panel" v-else>
        <el-empty description="从左侧选择一条用例开始执行" :image-size="80" />
      </div>
    </div>

    <!-- 新建测试计划：项目 → 需求级联 -->
    <el-dialog v-model="createDlg" title="新建测试计划" width="520px">
      <el-form label-width="80px">
        <el-form-item label="项目" required>
          <el-select v-model="createForm.project_id" style="width:100%" placeholder="选择项目"
                     @change="createForm.requirement_id = null; loadCreateReqs()">
            <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联需求">
          <el-select v-model="createForm.requirement_id" style="width:100%" clearable filterable
                     :disabled="!createForm.project_id" :loading="createReqLoading"
                     :placeholder="createForm.project_id ? '选择需求（可选）' : '先选择项目'">
            <el-option v-for="r in createReqs" :key="r.id" :value="r.id" :label="r.title" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划名称" required>
          <el-input v-model="createForm.name" placeholder="如 登录模块第1轮" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!createForm.project_id || !createForm.name" @click="createPlan">创建并进入</el-button>
      </template>
    </el-dialog>

    <!-- 添加用例弹窗：本项目用例多选添加 -->
    <el-dialog v-model="addDlg" title="添加用例到计划" width="640px">
      <div class="toolbar">
        <el-select v-model="addProjectId" placeholder="筛选项目" style="width:200px" @change="loadAddCases">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-input v-model="addKeyword" placeholder="搜索标题/编号" style="width:220px" clearable @keyup.enter="loadAddCases" @clear="loadAddCases" />
        <el-button type="primary" plain size="small" @click="loadAddCases"><el-icon><Search /></el-icon>搜索</el-button>
      </div>
      <el-table :data="addCases" v-loading="addLoading" max-height="380"
                @selection-change="s => addSelected = s" ref="addTable">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="case_no" label="编号" width="130" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="module_path" label="模块" width="120" show-overflow-tooltip />
        <el-table-column prop="priority" label="优先级" width="70" />
      </el-table>
      <template #footer>
        <el-button @click="addDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!addSelected.length" @click="confirmAddCases">添加（{{ addSelected.length }}）</el-button>
      </template>
    </el-dialog>

    <!-- 批量标记备注弹窗 -->
    <el-dialog v-model="batchDlg" width="440px" :title="`批量标记为「${resultLabel(batchResult)}」（${batchIds.length} 条）`">
      <el-form label-width="70px">
        <el-form-item label="备注"><el-input v-model="batchNote" :placeholder="batchResult === 'fail' ? '建议填写失败原因' : '可空'" /></el-form-item>
        <el-form-item v-if="batchResult === 'fail'" label="实际结果"><el-input v-model="batchActual" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchDlg = false">取消</el-button>
        <el-button type="primary" @click="doBatchMark">确认标记</el-button>
      </template>
    </el-dialog>

    <!-- 转 Bug -->
    <el-dialog v-model="bugDlg" title="失败转 Bug（自动带入上下文）" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题" required><el-input v-model="bugForm.title" /></el-form-item>
        <el-form-item label="严重程度">
          <el-select v-model="bugForm.severity">
            <el-option v-for="v in [['fatal','致命'],['serious','严重'],['normal','一般'],['minor','轻微']]" :key="v[0]" :value="v[0]" :label="v[1]" />
          </el-select>
        </el-form-item>
        <el-form-item label="详情"><el-input v-model="bugForm.description" type="textarea" :rows="5" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bugDlg = false">取消</el-button>
        <el-button type="primary" @click="saveBug">创建 Bug</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '../api'

const route = useRoute()
const projects = ref([])
const projectId = ref(null)
const keyword = ref('')
const plans = ref([])
const plansLoading = ref(false)
const plan = ref(null)

// 新建计划
const createDlg = ref(false)
const createForm = ref({ project_id: null, requirement_id: null, name: '' })
const createReqs = ref([])
const createReqLoading = ref(false)

// 添加用例
const addDlg = ref(false)
const addCases = ref([])
const addKeyword = ref('')
const addProjectId = ref(null)  // 添加用例弹窗的项目筛选（默认当前计划项目）
const addLoading = ref(false)
const addSelected = ref([])

// 批量标记
const checkedPlanCases = ref([])
const batchDlg = ref(false)
const batchResult = ref('')
const batchIds = ref([])
const batchNote = ref('')
const batchActual = ref('')

// 执行与编辑
const current = ref(null)
const editOpen = ref(false)
const editForm = ref({ title: '', priority: '', precondition: '', steps: [], expected: '' })
const resultForm = ref({ actual_result: '', note: '', result: '' })
const bugDlg = ref(false)
const bugForm = ref({ title: '', severity: 'normal', description: '' })

onMounted(async () => {
  projects.value = await get('/wb/projects')
  // 从用例库「执行圈选」跳转：直接为该项目创建计划
  if (route.query.caseIds) {
    const pid = Number(route.query.projectId) || projects.value[0]?.id
    await createQuickPlan(pid, route.query.caseIds.split(',').map(Number))
    return
  }
  loadPlans()
})

function progressPct(row) {
  const done = row.pass_count + row.fail_count + row.block_count + row.skip_count
  return row.total_count ? Math.round(done / row.total_count * 100) : 0
}
function progressColor(row) {
  const total = row.total_count || 1
  return row.fail_count / total > 0.2 ? '#ef4444' : '#10b981'
}

async function loadPlans() {
  plansLoading.value = true
  try {
    const q = new URLSearchParams({ plan_type: 'functional' })
    if (projectId.value) q.set('project_id', projectId.value)
    if (keyword.value) q.set('keyword', keyword.value)
    plans.value = await get('/wb/plans?' + q)
  } finally { plansLoading.value = false }
}

// ---- 新建测试计划 ----
function openCreatePlan() {
  createForm.value = { project_id: projectId.value, requirement_id: null, name: '' }
  createReqs.value = []
  if (createForm.value.project_id) loadCreateReqs()
  createDlg.value = true
}

async function loadCreateReqs() {
  if (!createForm.value.project_id) { createReqs.value = []; return }
  createReqLoading.value = true
  try { createReqs.value = await get(`/wb/requirements?project_id=${createForm.value.project_id}`) }
  finally { createReqLoading.value = false }
}

async function createPlan() {
  const p = await post('/wb/plans', {
    project_id: createForm.value.project_id,
    name: createForm.value.name,
    plan_type: 'functional',
    requirement_id: createForm.value.requirement_id || null,
    case_ids: []
  })
  ElMessage.success('计划已创建，请在计划内添加用例')
  createDlg.value = false
  await openPlan(p.id)
}

// 用例库圈选跳转的快捷建计划
async function createQuickPlan(pid, caseIds) {
  const p = await post('/wb/plans', {
    project_id: pid, name: '用例库圈选 ' + new Date().toLocaleDateString('zh-CN'),
    plan_type: 'functional', case_ids: caseIds
  })
  ElMessage.success('计划已创建，开始执行')
  openPlan(p.id)
}

async function openPlan(id) {
  plan.value = await get(`/wb/plans/${id}`)
  checkedPlanCases.value = []
  current.value = plan.value.cases.find(c => c.result === 'pending') || plan.value.cases[0] || null
  editOpen.value = false
  if (current.value) fillEditForm(current.value)
}

async function removePlan(row) {
  await del(`/wb/plans/${row.id}`)
  ElMessage.success('计划已删除')
  loadPlans()
}

function selectCase(c) {
  current.value = c
  editOpen.value = false
  resultForm.value = { actual_result: '', note: '', result: '' }
  fillEditForm(c)
}

function fillEditForm(c) {
  editForm.value = {
    title: c.title || '', priority: c.priority || '',
    precondition: c.precondition || '',
    steps: (c.steps || []).map(s => ({ ...s })),
    expected: c.expected || ''
  }
}

// ---- 计划内编辑（默认只改快照；syncOriginal=true 时同步回用例库原用例） ----
async function saveEdit(syncOriginal = false) {
  const payload = {
    result: current.value.result,
    title: editForm.value.title,
    priority: editForm.value.priority,
    precondition: editForm.value.precondition,
    steps: editForm.value.steps.filter(s => s.action || s.expected),
    expected: editForm.value.expected
  }
  if (syncOriginal) {
    // 同步原用例：标题等关键字段变更时二次确认，避免误改用例库
    try {
      await ElMessageBox.confirm(
        '将把本次修改内容同步覆盖用例库中的原用例（版本号 +1），是否继续？',
        '同时修改原用例', { confirmButtonText: '确认修改', cancelButtonText: '取消', type: 'warning' }
      )
    } catch { return }
    await put(`/wb/cases/${current.value.case_id}`, {
      title: payload.title,
      priority: payload.priority,
      precondition: payload.precondition,
      steps: payload.steps,
      expected: payload.expected
    })
  }
  await put(`/wb/plan-cases/${current.value.plan_case_id}`, payload)
  ElMessage.success(syncOriginal ? '已保存到本计划，并已同步修改原用例' : '已保存到本计划（用例库原用例不受影响）')
  const pid = plan.value.id
  plan.value = await get(`/wb/plans/${pid}`)
  current.value = plan.value.cases.find(c => c.plan_case_id === current.value.plan_case_id) || null
  editOpen.value = false
}

// ---- 添加/移除用例 ----
function openAddCases() {
  addKeyword.value = ''
  addProjectId.value = plan.value.project_id  // 默认筛选当前计划项目
  addSelected.value = []
  loadAddCases()
  addDlg.value = true
}

async function loadAddCases() {
  addLoading.value = true
  try {
    const q = new URLSearchParams({ project_id: addProjectId.value || plan.value.project_id, size: 200, status: 'active' })
    if (addKeyword.value) q.set('keyword', addKeyword.value)
    const d = await get('/wb/cases?' + q)
    const inPlan = new Set(plan.value.cases.map(c => c.case_id))
    addCases.value = d.items.filter(c => !inPlan.has(c.id))
  } finally { addLoading.value = false }
}

async function confirmAddCases() {
  await post(`/wb/plans/${plan.value.id}/cases`, { case_ids: addSelected.value.map(c => c.id) })
  ElMessage.success(`已添加 ${addSelected.value.length} 条用例`)
  addDlg.value = false
  plan.value = await get(`/wb/plans/${plan.value.id}`)
}

async function removeCase(c) {
  await del(`/wb/plans/${plan.value.id}/cases?plan_case_ids=${c.plan_case_id}`)
  ElMessage.success('已移出计划')
  if (current.value?.plan_case_id === c.plan_case_id) current.value = null
  plan.value = await get(`/wb/plans/${plan.value.id}`)
}

// ---- 多选批量标记 ----
function toggleCheck(id, v) {
  if (v) checkedPlanCases.value = [...checkedPlanCases.value, id]
  else checkedPlanCases.value = checkedPlanCases.value.filter(x => x !== id)
}

function batchMark(result) {
  batchResult.value = result
  batchIds.value = [...checkedPlanCases.value]
  batchNote.value = ''
  batchActual.value = ''
  batchDlg.value = true
}

async function doBatchMark() {
  await post('/wb/plan-cases/batch-result', {
    plan_case_ids: batchIds.value, result: batchResult.value,
    note: batchNote.value, actual_result: batchActual.value, executor: 'local'
  })
  ElMessage.success(`已批量标记 ${batchIds.value.length} 条为 ${resultLabel(batchResult.value)}`)
  batchDlg.value = false
  checkedPlanCases.value = []
  plan.value = await get(`/wb/plans/${plan.value.id}`)
  if (current.value) {
    current.value = plan.value.cases.find(c => c.plan_case_id === current.value.plan_case_id) || null
  }
}

// ---- 点单执行 ----
function submit(result) {
  resultForm.value.result = result
  if (result === 'pending') return submitResult(result)  // 重置为未执行无需备注
  if (result !== 'pass' && !resultForm.value.note) {
    return ElMessage.warning(result === 'fail' ? '失败请填备注/实际结果' : '请填写说明')
  }
  submitResult(result)
}

async function submitResult(result) {
  await put(`/wb/plan-cases/${current.value.plan_case_id}`, {
    result,
    note: resultForm.value.note,
    actual_result: resultForm.value.actual_result,
    executor: 'local'
  })
  ElMessage.success('已记录：' + resultLabel(result))
  resultForm.value = { actual_result: '', note: '', result: '' }
  plan.value = await get(`/wb/plans/${plan.value.id}`)
  current.value = plan.value.cases.find(c => c.plan_case_id === current.value.plan_case_id) || null
  const next = plan.value.cases.find(c => c.result === 'pending')
  if (!next) ElMessage.success('本轮全部执行完成！')
}

function toBug() {
  const failed = current.value.result === 'fail'
  bugForm.value = {
    title: `[${current.value.case_no}] ${current.value.title} ${failed ? '执行失败' : '执行异常'}`,
    severity: 'normal',
    description: `复现步骤：\n${(current.value.steps || []).map(s => `${s.step}. ${s.action}`).join('\n')}\n\n预期：${current.value.expected || ''}\n实际：${resultForm.value.actual_result || ''}\n备注：${resultForm.value.note || ''}`
  }
  bugDlg.value = true
}

async function saveBug() {
  const b = await post('/wb/bugs', {
    project_id: plan.value.project_id,
    title: bugForm.value.title,
    severity: bugForm.value.severity,
    priority: 'P1',
    description: bugForm.value.description,
    plan_id: plan.value.id,
    source: 'execution_fail',
    links: [{ link_type: 'case', link_id: current.value.case_id }, { link_type: 'plan', link_id: plan.value.id }]
  })
  ElMessage.success(`Bug 已创建：${b.bug_no}`)
  bugDlg.value = false
}

function resultTag(r) {
  return { pending: 'info', pass: 'success', fail: 'danger', block: 'warning', skip: 'info' }[r] || 'info'
}
function resultLabel(r) {
  return { pending: '未执行', pass: '通过', fail: '失败', block: '阻塞', skip: '跳过' }[r] || r
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
.pass-num { color: #10b981; font-weight: 600; }
.fail-num { color: #ef4444; font-weight: 600; }
.exec-layout { display: flex; gap: 16px; align-items: flex-start; }
.exec-list { width: 460px; flex-shrink: 0; max-height: calc(100vh - 130px); overflow-y: auto; }
.exec-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
.batch-bar {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 10px; margin-bottom: 8px;
  background: #f8fafc; border: 1px solid var(--wb-border); border-radius: 8px;
  flex-wrap: wrap;
}
.exec-item {
  display: flex; gap: 8px; align-items: center;
  padding: 8px 10px; border-radius: 8px; cursor: pointer; border: 1px solid transparent;
}
.exec-item:hover { background: #f3f6fb; }
.exec-item.active { background: var(--wb-accent-soft); border-color: var(--wb-accent); }
.res-tag { flex-shrink: 0; width: 58px; justify-content: center; }
.exec-item-main { min-width: 0; font-size: 13px; flex: 1; }
.exec-item-main > div:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.exec-panel { flex: 1; min-width: 0; }
.panel-title-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.edit-block {
  border: 1px solid var(--wb-border); border-radius: 10px;
  padding: 12px 14px 14px; margin-bottom: 14px; background: #fffdf5;
}
.result-btns { display: flex; gap: 8px; margin-top: 6px; }
.step-row { display: flex; gap: 8px; align-items: center; width: 100%; margin-bottom: 6px; }
.step-no { width: 22px; height: 22px; border-radius: 50%; background: var(--wb-accent-soft); color: var(--wb-accent); display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
</style>
