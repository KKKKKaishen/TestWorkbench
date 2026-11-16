<template>
  <div class="page api-page">
    <!-- 顶部工具栏 -->
    <div class="toolbar card">
      <span class="muted">项目</span>
      <el-select v-model="projectId" style="width: 200px" @change="switchProject">
        <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
      </el-select>
      <el-dropdown trigger="click" @command="cmd => openCreate(cmd)">
        <el-button type="primary" size="small">
          <el-icon><Plus /></el-icon>新 建<el-icon style="margin-left: 2px"><CaretBottom /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="flow">新建测试流程</el-dropdown-item>
            <el-dropdown-item command="folder">新建目录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <span class="muted tip">目录用于归档管理；流程内步骤可从「用例库」或「接口管理」选择，按顺序执行</span>
    </div>

    <div class="card api-body">
      <!-- 左侧：场景目录树 -->
      <aside class="api-side">
        <div class="side-title muted">测试流程</div>
        <el-input v-model="keyword" placeholder="搜索流程 / 目录" size="small" clearable style="margin-bottom: 8px">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <template v-for="item in displayList" :key="item.node.id">
          <!-- 目录节点 -->
          <div
            v-if="item.node.is_folder" class="tree-row folder-row"
            :class="{ active: currentId === item.node.id }"
            :style="{ marginLeft: item.depth * 14 + 'px' }" role="button"
            @click="item.node._open = !item.node._open"
          >
            <el-icon class="col-arrow" :class="{ open: item.node._open }"><CaretRight /></el-icon>
            <el-icon color="#e6a23c"><FolderOpened /></el-icon>
            <span class="row-name">{{ item.node.name }}</span>
            <span class="col-count">({{ subtreeCount(item.node) }})</span>
            <el-dropdown trigger="click" @command="cmd => nodeCmd(cmd, item.node)">
              <el-icon class="row-more" @click.stop><MoreFilled /></el-icon>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="flow">新建流程</el-dropdown-item>
                  <el-dropdown-item command="folder">新建子目录</el-dropdown-item>
                  <el-dropdown-item command="rename" divided>重命名</el-dropdown-item>
                  <el-dropdown-item command="delete">删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <!-- 流程节点 -->
          <div
            v-else class="tree-row flow-row"
            :class="{ active: currentId === item.node.id }"
            :style="{ marginLeft: (item.depth + 1) * 14 + 'px' }" role="button"
            @click="selectNode(item.node); loadScenario(item.node.id)"
          >
            <el-icon><Tickets /></el-icon>
            <div class="scen-main">
              <div class="row-name">{{ item.node.name }}</div>
              <div class="muted scen-meta">{{ item.node.step_count }} 步骤 · {{ item.node.environment_name || '无环境' }}</div>
            </div>
            <el-icon class="req-del" @click.stop="removeNode(item.node)"><Delete /></el-icon>
          </div>
        </template>
        <el-empty v-if="!displayList.length" :description="keyword ? '无匹配' : '暂无内容，点击上方「新建」开始'" :image-size="60" />
      </aside>

      <!-- 中间：目录/空态占位（仅目录或未选中时显示） -->
      <section v-if="!currentId || !current || current.is_folder" class="api-mid">
        <div v-if="currentId && current && current.is_folder" class="api-mid-empty">
          <el-empty description="这是一个目录，左侧「···」可在其下新建流程或子目录" :image-size="80" style="margin: auto" />
        </div>
        <el-empty v-else description="从左侧选择测试流程，或点击上方「新建」" :image-size="90" style="margin: auto" />
      </section>

      <!-- 流程编辑器（Apifox 风格：左步骤列表 + 右步骤详情内联编辑） -->
      <section v-if="currentId && current && !current.is_folder" class="flow-editor">
        <!-- 顶部工具栏 -->
        <div class="editor-top">
          <span class="scen-title">{{ current.name }}</span>
          <el-select v-model="current.environment_id" size="small" style="width: 170px" clearable placeholder="无环境（直连 URL）">
            <el-option v-for="e in envs" :key="e.id" :value="e.id" :label="e.name" />
          </el-select>
          <span class="muted loop-label">循环</span>
          <el-input-number v-model="current.loop_count" size="small" :min="1" :max="20" style="width: 92px" controls-position="right" />
          <div style="flex: 1" />
          <el-button size="small" type="primary" :loading="running" @click="run">
            <el-icon><VideoPlay /></el-icon>运 行
          </el-button>
          <el-button size="small" plain @click="saveScenario">保存流程</el-button>
        </div>
        <el-input
          v-model="current.description" type="textarea" :rows="1" autosize
          placeholder="添加流程说明…" class="scen-desc"
        />

        <div class="editor-body">
          <!-- 左：步骤列表 -->
          <div class="editor-steps">
            <div class="steps-head muted">已选 {{ enabledStepCount }} 项</div>
            <div class="steps-scroll">
              <div
                v-for="(st, i) in current.steps" :key="st.id"
                class="step-row" :class="{ dim: !st.enabled, active: activeStepId === st.id }"
                role="button" @click="openStepEditor(st)"
              >
                <el-checkbox :model-value="!!st.enabled" @click.stop @change="v => toggleStep(st, v)" />
                <span class="step-no">{{ i + 1 }}</span>
                <span class="m-tag" :class="'m-' + st.method.toLowerCase()">{{ st.method }}</span>
                <div class="step-main">
                  <div class="step-name">{{ st.name }}</div>
                </div>
                <template v-if="st.result">
                  <el-tag size="small" :type="st.result.error || !st.result.status || st.result.status >= 400 ? 'danger' : 'success'" effect="dark">
                    {{ st.result.error ? 'ERR' : st.result.status }}
                  </el-tag>
                  <span class="muted step-ms">{{ st.result.duration_ms }}ms</span>
                </template>
                <el-icon class="req-del" @click.stop="removeStep(st)"><Delete /></el-icon>
              </div>
              <div v-if="lastRun" class="run-line">
                <el-tag size="small" :type="lastRun.failed ? 'danger' : 'success'" effect="dark">完成</el-tag>
                <span class="st-ok">通过 {{ lastRun.passed }}</span>
                <span class="st-bad" v-if="lastRun.failed">失败 {{ lastRun.failed }}</span>
                <span class="muted">{{ (lastRun.total_ms / 1000).toFixed(2) }}s</span>
              </div>
            </div>
            <div class="add-step" role="button" @click="openAddDlg"><el-icon><Plus /></el-icon>添加步骤</div>
          </div>

          <!-- 右：步骤详情（内联编辑，仅改本步骤副本） -->
          <div class="editor-detail">
            <template v-if="activeStep && stepForm">
              <div class="detail-head">
                <el-tag size="small" effect="plain" :type="activeStep.source === 'case' ? 'success' : 'primary'" class="src-tag">
                  {{ activeStep.source === 'case' ? '用例' : '接口' }}
                </el-tag>
                <el-input v-model="stepForm.name" class="detail-name" placeholder="步骤名称" />
                <el-button size="small" type="primary" plain :loading="stepSaving" @click="saveStepEdit">保 存</el-button>
              </div>
              <div class="detail-urlrow">
                <el-select v-model="stepForm.method" class="method-sel">
                  <el-option v-for="m in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']" :key="m" :value="m" :label="m" />
                </el-select>
                <el-input v-model="stepForm.url" placeholder="/path（配合环境 Base URL）或完整地址" class="url-input mono" />
                <el-button type="primary" :loading="sending" @click="sendStep">发 送</el-button>
              </div>

              <el-tabs v-model="detailTab" class="detail-tabs">
                <el-tab-pane name="params">
                  <template #label>Params<span v-if="paramCount" class="tab-badge">{{ paramCount }}</span></template>
                  <div class="kv-table">
                    <div class="kv-head muted"><span class="c-key">参数名</span><span class="c-val">参数值</span><span class="c-op"></span></div>
                    <div v-for="(p, pi) in stepForm.params" :key="pi" class="kv-row">
                      <el-checkbox :model-value="!!p.enabled" @change="v => p.enabled = v ? 1 : 0" />
                      <el-input v-model="p.key" placeholder="参数名" class="c-key" />
                      <el-input v-model="p.value" placeholder="参数值" class="c-val mono" />
                      <el-icon class="kv-del" @click="stepForm.params.splice(pi, 1)"><Delete /></el-icon>
                    </div>
                    <div class="kv-add" role="button" @click="stepForm.params.push({ key: '', value: '', enabled: 1 })">
                      <el-icon><Plus /></el-icon>添加参数
                    </div>
                  </div>
                </el-tab-pane>
                <el-tab-pane name="body">
                  <template #label>Body</template>
                  <el-radio-group v-model="stepForm.body_type" class="body-types">
                    <el-radio-button value="none">none</el-radio-button>
                    <el-radio-button value="json">json</el-radio-button>
                    <el-radio-button value="form">form</el-radio-button>
                    <el-radio-button value="raw">raw</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-if="stepForm.body_type !== 'none'" v-model="stepForm.body"
                    type="textarea" :rows="10" class="mono body-input"
                    :placeholder="stepForm.body_type === 'form' ? '[{&quot;key&quot;: &quot;名称&quot;, &quot;value&quot;: &quot;值&quot;}]' : '请求体内容'"
                  />
                </el-tab-pane>
                <el-tab-pane name="headers">
                  <template #label>Headers<span v-if="headerCount" class="tab-badge">{{ headerCount }}</span></template>
                  <div class="kv-table">
                    <div class="kv-head muted"><span class="c-key">参数名</span><span class="c-val">参数值</span><span class="c-op"></span></div>
                    <div v-for="(h, hi) in stepForm.headers" :key="hi" class="kv-row">
                      <el-checkbox :model-value="!!h.enabled" @change="v => h.enabled = v ? 1 : 0" />
                      <el-input v-model="h.key" placeholder="参数名" class="c-key" />
                      <el-input v-model="h.value" placeholder="参数值" class="c-val mono" />
                      <el-icon class="kv-del" @click="stepForm.headers.splice(hi, 1)"><Delete /></el-icon>
                    </div>
                    <div class="kv-add" role="button" @click="stepForm.headers.push({ key: '', value: '', enabled: 1 })">
                      <el-icon><Plus /></el-icon>添加 Header
                    </div>
                  </div>
                </el-tab-pane>
              </el-tabs>

              <!-- 返回响应 -->
              <div class="resp-section">
                <div class="resp-head">
                  <span class="resp-title">返回响应</span>
                  <template v-if="sendResp">
                    <el-tag size="small" :type="sendResp.error ? 'danger' : (!sendResp.status || sendResp.status >= 400 ? 'warning' : 'success')" effect="dark">
                      {{ sendResp.error ? 'ERR' : (sendResp.status || 'ERR') }}
                    </el-tag>
                    <span class="muted resp-meta">{{ sendResp.duration_ms }}ms · {{ fmtSize(sendResp.size) }}</span>
                  </template>
                </div>
                <pre v-if="sendResp" class="resp-body mono">{{ sendResp.error || pretty(sendResp.body) }}</pre>
                <div v-else class="resp-empty muted">点击「发送」按钮获取返回结果</div>
              </div>
              <el-alert type="info" :closable="false" class="detail-tip" title="保存仅修改本流程中此步骤的参数副本，不影响用例库原用例与接口管理原接口" />
            </template>
            <el-empty v-else description="点击左侧步骤查看并编辑详情（发送测试不影响已保存配置）" :image-size="90" style="margin: auto" />
          </div>
        </div>
      </section>
    </div>

    <!-- 添加步骤弹窗：目录折叠 + 勾选 + 确定加入 -->
    <el-dialog v-model="addDlg" title="添加步骤" width="620px">
      <el-radio-group v-model="addSource" style="margin-bottom: 10px">
        <el-radio-button value="api">从接口管理选择</el-radio-button>
        <el-radio-button value="case">从用例库选择（接口用例）</el-radio-button>
      </el-radio-group>
      <el-input v-model="addKeyword" placeholder="搜索名称" clearable style="margin-bottom: 10px">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <!-- 来源：接口管理（目录折叠展示 + 勾选） -->
      <div v-if="addSource === 'api'" class="pick-list">
        <template v-for="item in apiDisplayList" :key="item.col.id">
          <template v-if="colVisible(item.col)">
            <div
              class="pick-col pick-fold" role="button"
              :style="{ paddingLeft: 8 + item.depth * 14 + 'px' }"
              @click="toggleCol(item.col.id)"
            >
              <el-icon class="fold-arrow" :class="{ open: colExpanded(item.col.id) }"><CaretRight /></el-icon>
              <el-icon color="#e6a23c"><FolderOpened /></el-icon>
              <span class="col-title">{{ item.col.name }}</span>
              <span class="muted">({{ addFilter(item.col.requests).length }})</span>
            </div>
            <template v-if="colExpanded(item.col.id)">
              <div
                v-for="rq in addFilter(item.col.requests)" :key="rq.id"
                class="req-item pick-item" role="button"
                :class="{ disabled: isAdded('api', rq.id) }"
                :style="{ paddingLeft: 16 + item.depth * 14 + 'px' }"
                @click="toggleSel('api', rq.id, rq.name)"
              >
                <el-checkbox class="sel-check" :model-value="isChecked('api', rq.id)" :disabled="isAdded('api', rq.id)" />
                <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ rq.method }}</span>
                <span class="req-name">{{ rq.name }}</span>
                <span class="muted pick-url">{{ rq.url }}</span>
                <el-tag v-if="isAdded('api', rq.id)" size="small" type="info" effect="plain">已在流程</el-tag>
              </div>
            </template>
          </template>
        </template>
        <template v-if="addFilter(pickOrphans).length">
          <div class="pick-col pick-fold" role="button" @click="toggleCol('__orphans__')">
            <el-icon class="fold-arrow" :class="{ open: colExpanded('__orphans__') }"><CaretRight /></el-icon>
            <el-icon><Tickets /></el-icon>
            <span class="col-title">未分组</span>
            <span class="muted">({{ addFilter(pickOrphans).length }})</span>
          </div>
          <template v-if="colExpanded('__orphans__')">
            <div
              v-for="rq in addFilter(pickOrphans)" :key="rq.id"
              class="req-item pick-item" role="button"
              :class="{ disabled: isAdded('api', rq.id) }"
              @click="toggleSel('api', rq.id, rq.name)"
            >
              <el-checkbox class="sel-check" :model-value="isChecked('api', rq.id)" :disabled="isAdded('api', rq.id)" />
              <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ rq.method }}</span>
              <span class="req-name">{{ rq.name }}</span>
              <span class="muted pick-url">{{ rq.url }}</span>
              <el-tag v-if="isAdded('api', rq.id)" size="small" type="info" effect="plain">已在流程</el-tag>
            </div>
          </template>
        </template>
        <el-empty v-if="!apiDisplayList.length && !addFilter(pickOrphans).length" description="接口管理中还没有接口" :image-size="60" />
      </div>

      <!-- 来源：用例库（接口用例，勾选） -->
      <div v-else class="pick-list">
        <div
          v-for="c in filteredCases" :key="c.id"
          class="req-item pick-item" role="button"
          :class="{ disabled: isAdded('case', c.id) }"
          @click="toggleSel('case', c.id, c.title)"
        >
          <el-checkbox class="sel-check" :model-value="isChecked('case', c.id)" :disabled="isAdded('case', c.id)" />
          <el-tag size="small" type="success" effect="plain">用例</el-tag>
          <span class="case-no muted">{{ c.case_no }}</span>
          <span class="req-name">{{ c.title }}</span>
          <span class="muted pick-url">
            {{ c.api_snapshot ? (c.api_snapshot.method || 'GET') + ' ' + c.api_snapshot.url : '（缺少接口参数快照）' }}
          </span>
          <el-tag v-if="!c.api_snapshot?.url" size="small" type="danger" effect="plain">缺快照</el-tag>
          <el-tag v-else-if="isAdded('case', c.id)" size="small" type="info" effect="plain">已在流程</el-tag>
        </div>
        <el-empty v-if="!filteredCases.length" :description="addKeyword ? '无匹配接口用例' : '用例库中还没有接口用例，可在用例库或测试点处新建'" :image-size="60" />
      </div>

      <template #footer>
        <div class="add-footer">
          <span class="muted">已选 {{ selected.length }} 个步骤</span>
          <div style="flex: 1" />
          <el-button :disabled="!selected.length" @click="selected = []">清空</el-button>
          <el-button @click="addDlg = false">取消</el-button>
          <el-button type="primary" :loading="adding" :disabled="!selected.length" @click="confirmAdd">
            确定加入流程
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 新建流程/目录弹窗 -->
    <el-dialog v-model="createDlg" :title="createForm.is_folder ? '新建目录' : '新建测试流程'" width="440px">
      <el-form label-width="70px">
        <el-form-item label="上级">
          <el-input :model-value="createForm.parent_name || '（根目录）'" disabled />
        </el-form-item>
        <el-form-item label="名称" required><el-input v-model="createForm.name" :placeholder="createForm.is_folder ? '如 商城模块' : '如 宠物增删改查流程'" @keyup.enter="doCreate" /></el-form-item>
        <el-form-item v-if="!createForm.is_folder" label="说明"><el-input v-model="createForm.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg = false">取消</el-button>
        <el-button type="primary" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '../api'

const projects = ref([])
const projectId = ref(null)
const envs = ref([])
const scenarios = ref([])
const keyword = ref('')

const currentId = ref(null)
const current = ref(null)
const running = ref(false)
const lastRun = ref(null)

// ---------- 场景树 ----------
const tree = computed(() => {
  const byId = new Map()
  for (const s of scenarios.value) { s.children = []; byId.set(s.id, s) }
  const roots = []
  for (const s of scenarios.value) {
    const p = s.parent_id ? byId.get(s.parent_id) : null
    if (p && p !== s) p.children.push(s)
    else roots.push(s)
  }
  const sortRec = list => {
    list.sort((a, b) => (a.is_folder - b.is_folder) || a.id - b.id)
    list.forEach(n => sortRec(n.children))
  }
  sortRec(roots)
  return roots
})

function subtreeCount(node) {
  let n = node.is_folder ? 0 : 1
  for (const ch of node.children) n += subtreeCount(ch)
  return n
}

function hasTreeMatch(node) {
  if (node.name.toLowerCase().includes(keyword.value.toLowerCase())) return true
  return node.children.some(hasTreeMatch)
}

const displayList = computed(() => {
  const out = []
  const walk = (list, depth) => {
    for (const n of list) {
      if (keyword.value && !hasTreeMatch(n)) continue
      out.push({ node: n, depth })
      walk(n.children, depth + 1)
    }
  }
  walk(tree.value, 0)
  return out
})

// 当前选中节点（目录/流程均可），新建时作为父级定位依据
const selectedNode = ref(null)

function selectNode(node) {
  selectedNode.value = node
}

function resolveParentId() {
  const n = selectedNode.value
  if (!n) return { id: null, name: '' }
  if (n.is_folder) return { id: n.id, name: n.name }
  const parent = scenarios.value.find(s => s.id === n.parent_id)
  return { id: n.parent_id || null, name: parent?.name || '' }
}

function nodeCmd(cmd, node) {
  selectedNode.value = node
  if (cmd === 'flow' || cmd === 'folder') openCreate(cmd)
  else if (cmd === 'rename') renameNode(node)
  else if (cmd === 'delete') removeNode(node)
}

async function renameNode(node) {
  const { value } = await ElMessageBox.prompt('新名称', '重命名', { inputValue: node.name })
  if (value && value.trim()) {
    await put(`/wb/api-scenarios/${node.id}`, { project_id: projectId.value, name: value.trim() })
    await loadScenarios()
    if (currentId.value === node.id && current.value) current.value.name = value.trim()
  }
}

async function removeNode(node) {
  const tip = node.is_folder
    ? `删除目录「${node.name}」？其下流程与子目录将上移一级。`
    : `删除流程「${node.name}」及其全部步骤？`
  await ElMessageBox.confirm(tip, '删除', { type: 'warning' })
  await del(`/wb/api-scenarios/${node.id}`)
  if (currentId.value === node.id) { currentId.value = null; current.value = null; selectedNode.value = null }
  await loadScenarios()
}

// ---------- 数据加载 ----------
async function loadProjects() {
  projects.value = await get('/wb/projects')
  if (projects.value.length && !projectId.value) {
    projectId.value = projects.value[0].id
    await switchProject()
  }
}

async function switchProject() {
  currentId.value = null
  current.value = null
  lastRun.value = null
  selectedNode.value = null
  activeStepId.value = null
  stepForm.value = null
  sendResp.value = null
  await Promise.all([loadEnvs(), loadScenarios()])
}

async function loadEnvs() {
  envs.value = await get(`/wb/api-envs?project_id=${projectId.value}`)
}

async function loadScenarios() {
  scenarios.value = await get(`/wb/api-scenarios?project_id=${projectId.value}`)
}

async function loadScenario(id) {
  currentId.value = id
  current.value = await get(`/wb/api-scenarios/${id}`)
  lastRun.value = null
  activeStepId.value = null
  stepForm.value = null
  sendResp.value = null
  // 默认选中第一个步骤，直接进入详情编辑
  if (current.value.steps?.length) openStepEditor(current.value.steps[0])
}

// ---------- 新建流程/目录 ----------
const createDlg = ref(false)
const createForm = ref({ name: '', description: '', is_folder: 0, parent_id: null, parent_name: '' })

function openCreate(kind) {
  const parent = resolveParentId()
  createForm.value = {
    name: '', description: '',
    is_folder: kind === 'folder' ? 1 : 0,
    parent_id: parent.id, parent_name: parent.name,
  }
  createDlg.value = true
}

async function doCreate() {
  const f = createForm.value
  if (!f.name || !f.name.trim()) return ElMessage.warning('请填写名称')
  const d = await post('/wb/api-scenarios', {
    project_id: projectId.value,
    name: f.name.trim(),
    parent_id: f.parent_id,
    is_folder: f.is_folder,
    description: f.description || null,
    loop_count: 1,
  })
  createDlg.value = false
  await loadScenarios()
  selectedNode.value = scenarios.value.find(s => s.id === d.id) || selectedNode.value
  if (!f.is_folder) {
    await loadScenario(d.id)
    ElMessage.success('流程已创建，开始添加步骤')
  } else {
    ElMessage.success('目录已创建')
  }
}

// ---------- 场景保存 ----------
async function saveScenario() {
  await put(`/wb/api-scenarios/${currentId.value}`, {
    project_id: projectId.value,
    name: current.value.name,
    description: current.value.description || null,
    environment_id: current.value.environment_id || null,
    loop_count: current.value.loop_count || 1,
  })
  await loadScenarios()
  ElMessage.success('已保存')
}

// ---------- 添加步骤（勾选 + 确定加入） ----------
const addDlg = ref(false)
const addSource = ref('api')
const addKeyword = ref('')
const pickCollections = ref([])
const pickOrphans = ref([])
const apiCases = ref([])
const selected = ref([])        // [{ source, ref_id, name }]
const adding = ref(false)
const collapsedCols = ref({})   // 目录折叠状态（默认全部折叠）

function addFilter(list) {
  if (!addKeyword.value) return list || []
  const k = addKeyword.value.toLowerCase()
  return (list || []).filter(r => (r.name || r.title || '').toLowerCase().includes(k) || (r.url || '').toLowerCase().includes(k))
}

const filteredCases = computed(() => addFilter(apiCases.value))

function isAdded(source, refId) {
  return (current.value?.steps || []).some(s => s.source === source && s.ref_id === refId)
}

function isChecked(source, refId) {
  return selected.value.some(s => s.source === source && s.ref_id === refId)
}

function toggleSel(source, refId, name) {
  if (isAdded(source, refId)) return
  const i = selected.value.findIndex(s => s.source === source && s.ref_id === refId)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push({ source, ref_id: refId, name })
}

// 搜索时自动展开所有命中目录
function colExpanded(id) {
  if (addKeyword.value) return true
  return !collapsedCols.value[id]
}

function toggleCol(id) {
  collapsedCols.value[id] = !collapsedCols.value[id]
}

// 搜索时过滤掉无命中接口的目录（含子目录递归）
function colVisible(col) {
  if (!addKeyword.value) return true
  return addFilter(col.requests).length > 0 || col.children.some(colVisible)
}

async function openAddDlg() {
  try {
    addKeyword.value = ''
    selected.value = []
    collapsedCols.value = {}
    const [d, cases] = await Promise.all([
      get(`/wb/api-collections?project_id=${projectId.value}`),
      get(`/wb/cases?project_id=${projectId.value}&case_type=api&status=active&size=100`),
    ])
    const byId = new Map(d.collections.map(c => [c.id, c]))
    for (const c of d.collections) {
      let dep = 0, p = c.parent_id
      while (p && byId.has(p)) { dep++; p = byId.get(p).parent_id }
      c._depth = dep
    }
    pickCollections.value = d.collections
    pickOrphans.value = d.orphans
    apiCases.value = cases.items
    addDlg.value = true
  } catch (e) {
    ElMessage.error('步骤选择列表加载失败：' + e.message)
  }
}

// 接口管理树（平铺 + 层级）
const apiDisplayList = computed(() => {
  const out = []
  const walk = (list, depth) => {
    for (const c of list) {
      out.push({ col: c, depth })
      walk(c.children, depth + 1)
    }
  }
  const byId = new Map()
  for (const c of pickCollections.value) { c.children = []; byId.set(c.id, c) }
  const roots = []
  for (const c of pickCollections.value) {
    const p = c.parent_id ? byId.get(c.parent_id) : null
    if (p && p !== c) p.children.push(c)
    else roots.push(c)
  }
  walk(roots, 0)
  return out
})

async function confirmAdd() {
  if (!currentId.value) return ElMessage.warning('请先选择一个测试流程（目录不能添加步骤）')
  if (current.value?.is_folder) return ElMessage.warning('当前选中的是目录，请先选择一个测试流程再添加步骤')
  if (!selected.value.length) return ElMessage.warning('请先勾选要添加的步骤')
  adding.value = true
  try {
    const r = await post(`/wb/api-scenarios/${currentId.value}/steps/batch`, { items: selected.value })
    current.value = await get(`/wb/api-scenarios/${currentId.value}`)
    await loadScenarios()
    addDlg.value = false
    selected.value = []
    if (r.failed?.length) {
      const first = r.failed[0]
      ElMessage.error(`${r.failed.length} 个步骤添加失败，如「${first.name}」：${first.error}`)
    } else if (r.skipped?.length && !r.added.length) {
      ElMessage.info('所选步骤均已在流程中')
    } else {
      const skippedTip = r.skipped?.length ? `，${r.skipped.length} 个已存在被跳过` : ''
      ElMessage.success(`已添加 ${r.added.length} 个步骤${skippedTip}`)
    }
  } catch (e) {
    ElMessage.error('添加失败：' + e.message)
  } finally {
    adding.value = false
  }
}

// ---------- 步骤操作 ----------
async function toggleStep(st, v) {
  await put(`/wb/api-scenarios/${currentId.value}/steps/${st.id}`, { enabled: v ? 1 : 0 })
  st.enabled = v ? 1 : 0
}

// ---------- 步骤详情（内联编辑器，Apifox 风格；仅改本步骤副本，不影响原用例/原接口） ----------
const activeStepId = ref(null)
const stepForm = ref(null)      // 当前编辑副本 { name, method, url, headers, params, body_type, body }
const detailTab = ref('params')
const stepSaving = ref(false)
const sending = ref(false)
const sendResp = ref(null)      // 单步发送的响应（仅测试，不落库）

const activeStep = computed(() => (current.value?.steps || []).find(s => s.id === activeStepId.value) || null)
const paramCount = computed(() => (stepForm.value?.params || []).filter(p => p.enabled && p.key).length)
const headerCount = computed(() => (stepForm.value?.headers || []).filter(h => h.enabled && h.key).length)

function openStepEditor(st) {
  activeStepId.value = st.id
  detailTab.value = 'params'
  sendResp.value = null
  const s = st.snapshot || {}
  stepForm.value = {
    name: s.name || st.name,
    method: s.method || st.method || 'GET',
    url: s.url || st.url || '',
    headers: (s.headers || []).map(h => ({ key: h.key || '', value: h.value || '', enabled: h.enabled ?? 1 })),
    params: (s.params || []).map(p => ({ key: p.key || '', value: p.value || '', enabled: p.enabled ?? 1 })),
    body_type: s.body_type || 'none',
    body: s.body || '',
  }
}

async function saveStepEdit() {
  if (!activeStep.value || !stepForm.value) return
  if (!(stepForm.value.url || '').trim()) return ElMessage.warning('URL 不能为空')
  stepSaving.value = true
  try {
    await put(`/wb/api-scenarios/${currentId.value}/steps/${activeStep.value.id}`, { snapshot: stepForm.value })
    current.value = await get(`/wb/api-scenarios/${currentId.value}`)
    ElMessage.success('步骤已更新（不影响原用例/接口配置）')
  } catch (e) {
    ElMessage.error('保存失败：' + e.message)
  } finally {
    stepSaving.value = false
  }
}

// 单步发送：按当前编辑副本试发（含环境变量替换/Base URL 拼接），不写历史、不改配置
async function sendStep() {
  if (!stepForm.value) return
  sending.value = true
  try {
    sendResp.value = await post('/wb/api-send', {
      project_id: projectId.value,
      environment_id: current.value.environment_id || null,
      method: stepForm.value.method,
      url: (stepForm.value.url || '').trim(),
      headers: stepForm.value.headers,
      query_params: stepForm.value.params,
      body_type: stepForm.value.body_type,
      body: stepForm.value.body || null,
    })
  } catch (e) {
    sendResp.value = { error: e.message, status: null, duration_ms: 0, size: 0, body: '' }
  } finally {
    sending.value = false
  }
}

function fmtSize(n) {
  if (!n) return '0 B'
  if (n < 1024) return n + ' B'
  return (n / 1024).toFixed(1) + ' KB'
}

async function removeStep(st) {
  await del(`/wb/api-scenarios/${currentId.value}/steps/${st.id}`)
  current.value = await get(`/wb/api-scenarios/${currentId.value}`)
  await loadScenarios()
}

const enabledStepCount = computed(() => (current.value?.steps || []).filter(s => s.enabled).length)

// ---------- 运行 ----------
async function run() {
  if (!enabledStepCount.value) return ElMessage.warning('请先添加步骤')
  running.value = true
  try {
    await saveScenario()
    lastRun.value = await post(`/wb/api-scenarios/${currentId.value}/run`)
    // 把结果挂到步骤行上（循环取每步最后一次结果）
    const byStep = {}
    for (const r of lastRun.value.results) byStep[r.step_id] = r
    for (const st of current.value.steps) st.result = byStep[st.id] || null
    if (lastRun.value.failed) ElMessage.warning(`运行完成：${lastRun.value.passed} 通过 / ${lastRun.value.failed} 失败`)
    else ElMessage.success(`运行完成：${lastRun.value.total} 步全部通过`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    running.value = false
  }
}

function pretty(t) {
  if (!t) return ''
  try { return JSON.stringify(JSON.parse(t), null, 2) } catch { return t }
}

onMounted(loadProjects)
</script>

<style scoped>
.api-page { display: flex; flex-direction: column; height: calc(100vh - 48px); }
.toolbar { display: flex; gap: 10px; align-items: center; padding: 12px 16px; }
.tip { font-size: 12px; }
.api-body { display: flex; flex: 1; min-height: 0; padding: 0; overflow: hidden; overflow-x: auto; }

.api-side { width: 280px; flex-shrink: 0; border-right: 1px solid var(--wb-border); padding: 10px; overflow-y: auto; }
.side-title { font-weight: 600; font-size: 13px; margin-bottom: 8px; color: #6b7280; }
.tree-row { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border-radius: 8px; cursor: pointer; font-size: 13px; margin-bottom: 2px; }
.tree-row:hover { background: #f3f6fb; }
.tree-row.active { background: var(--wb-accent-soft, #eef2ff); }
.row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.folder-row { font-weight: 600; }
.col-arrow { cursor: pointer; transition: transform 0.15s; flex-shrink: 0; }
.col-arrow.open { transform: rotate(90deg); }
.col-count { color: #9ca3af; font-size: 12px; font-weight: 400; flex-shrink: 0; }
.row-more { margin-left: auto; color: #9ca3af; cursor: pointer; flex-shrink: 0; }
.scen-main { flex: 1; min-width: 0; }
.scen-meta { font-size: 11px; margin-top: 2px; }
.req-del { color: #d1d5db; flex-shrink: 0; }
.req-del:hover { color: #ef4444; }

.api-mid { flex: 1; min-width: 320px; display: flex; flex-direction: column; padding: 14px 18px; overflow-y: auto; }
.api-mid-empty { display: flex; flex: 1; }
.scen-title { font-weight: 700; font-size: 16px; }

/* ---------- 流程编辑器（Apifox 风格） ---------- */
.flow-editor { flex: 1; min-width: 720px; display: flex; flex-direction: column; padding: 12px 16px 0; overflow: hidden; }
.editor-top { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.loop-label { font-size: 12px; }
.scen-desc { margin-top: 6px; max-width: 560px; flex-shrink: 0; }
.scen-desc :deep(textarea) { border: none; padding-left: 0; box-shadow: none; font-size: 12px; }
.editor-body { display: flex; flex: 1; min-height: 0; margin-top: 14px; gap: 0; }

/* 左：步骤列表 */
.editor-steps { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; border-right: 1px solid var(--wb-border); padding-right: 14px; min-height: 0; }
.steps-head { font-size: 12px; padding-bottom: 8px; flex-shrink: 0; }
.steps-scroll { flex: 1; min-height: 0; overflow-y: auto; }
.step-row { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 8px; background: #f8fafc; border: 1px solid #eef1f6; margin-bottom: 6px; cursor: pointer; }
.step-row:hover { border-color: #c7d2fe; }
.step-row.active { background: var(--wb-accent-soft, #eef2ff); border-color: var(--wb-accent); }
.step-row.dim { opacity: 0.5; }
.step-no { width: 20px; height: 20px; border-radius: 50%; background: #eef2ff; color: var(--wb-accent); display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
.step-main { flex: 1; min-width: 0; }
.step-name { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-ms { font-size: 12px; flex-shrink: 0; }
.steps-scroll .run-line { display: flex; gap: 10px; align-items: center; font-size: 12px; padding: 8px 10px; margin-top: 4px; }
.st-ok { color: #16a34a; }
.st-bad { color: #ef4444; }
.add-step { flex-shrink: 0; text-align: center; padding: 12px; border: 1px dashed #cbd5e1; border-radius: 8px; color: var(--wb-accent); cursor: pointer; font-size: 13px; }
.add-step:hover { background: #f8faff; border-color: var(--wb-accent); }

/* 右：步骤详情 */
.editor-detail { flex: 1; min-width: 0; display: flex; flex-direction: column; padding-left: 14px; overflow-y: auto; }
.detail-head { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.src-tag { flex-shrink: 0; width: 44px; justify-content: center; }
.detail-name { flex: 1; }
.detail-name :deep(.el-input__inner) { font-weight: 600; }
.detail-urlrow { display: flex; align-items: center; gap: 8px; margin-top: 14px; flex-shrink: 0; }
.method-sel { width: 110px; flex-shrink: 0; }
.method-sel :deep(.el-select__wrapper) { font-family: Consolas, Monaco, monospace; font-weight: 700; }
.url-input { flex: 1; }
.detail-tabs { margin-top: 14px; flex-shrink: 0; }
.detail-tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
.detail-tabs :deep(.el-tabs__nav-wrap::after) { height: 1px; }
.tab-badge { display: inline-block; margin-left: 4px; font-size: 11px; background: var(--wb-accent-soft, #eef2ff); color: var(--wb-accent); border-radius: 8px; padding: 0 6px; line-height: 16px; }

/* 参数表格（Apifox 风格：表头 + 行内输入） */
.kv-table { border: 1px solid var(--wb-border); border-radius: 8px; overflow: hidden; }
.kv-head, .kv-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; }
.kv-head { background: #f8fafc; font-size: 12px; border-bottom: 1px solid var(--wb-border); padding: 8px 10px; }
.kv-row { border-bottom: 1px solid #f1f5f9; }
.kv-row:last-of-type { border-bottom: none; }
.kv-row .el-checkbox { margin: 0; }
.c-key { width: 240px; flex-shrink: 0; }
.c-val { flex: 1; min-width: 120px; }
.kv-head .c-key { width: 240px; padding-left: 26px; }
.kv-head .c-val { flex: 1; }
.kv-del { width: 26px; flex-shrink: 0; color: #d1d5db; cursor: pointer; }
.kv-del:hover { color: #ef4444; }
.kv-add { display: flex; align-items: center; gap: 4px; padding: 8px 12px; color: var(--wb-accent); font-size: 12px; cursor: pointer; border-top: 1px dashed #e2e8f0; user-select: none; }
.kv-add:hover { background: #f8faff; }
.body-types { margin-bottom: 14px; }
.body-input { width: 100%; }

/* 返回响应 */
.resp-section { margin-top: 10px; flex-shrink: 0; }
.resp-head { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-top: 1px solid var(--wb-border); }
.resp-title { font-weight: 700; font-size: 14px; }
.resp-meta { font-size: 12px; }
.resp-body { background: #f8fafc; border: 1px solid var(--wb-border); border-radius: 8px; padding: 12px; font-size: 12px; max-height: 42vh; overflow: auto; margin: 0; white-space: pre-wrap; word-break: break-all; }
.resp-empty { padding: 44px 0; text-align: center; font-size: 13px; }
.detail-tip { margin: 14px 0 20px; }

.m-tag { width: 46px; text-align: center; flex-shrink: 0; font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 0; font-family: Consolas, Monaco, monospace; }
.m-get { color: #16a34a; background: #dcfce7; }
.m-post { color: #d97706; background: #fef3c7; }
.m-put { color: #2563eb; background: #dbeafe; }
.m-delete { color: #dc2626; background: #fee2e2; }
.m-patch { color: #7c3aed; background: #ede9fe; }

.req-item { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.req-item:hover { background: #f3f6fb; }
.req-item.disabled { opacity: 0.55; cursor: not-allowed; }
.pick-list { max-height: 44vh; overflow-y: auto; }
.pick-col { font-size: 12px; font-weight: 600; padding: 8px 8px 4px; }
.pick-fold { display: flex; align-items: center; gap: 5px; cursor: pointer; border-radius: 6px; user-select: none; }
.pick-fold:hover { background: #f3f6fb; }
.fold-arrow { transition: transform 0.15s; font-size: 12px; color: #94a3b8; }
.fold-arrow.open { transform: rotate(90deg); }
.col-title { font-size: 12px; font-weight: 600; }
.sel-check { pointer-events: none; margin-right: 2px; }
.pick-url { font-size: 11px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.add-footer { display: flex; align-items: center; gap: 8px; }
.case-no { font-size: 11px; flex-shrink: 0; }
.mono { font-family: Consolas, Monaco, monospace; }
</style>
