<template>
  <div class="page api-page">
    <!-- 顶部工具栏 -->
    <div class="toolbar card">
      <span class="muted">项目</span>
      <el-select v-model="projectId" style="width: 200px" @change="switchProject">
        <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
      </el-select>
      <el-input v-model="keyword" placeholder="搜索接口名称 / URL" style="width: 220px" clearable>
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <span class="muted">环境</span>
      <el-select v-model="envId" style="width: 170px" clearable placeholder="无环境">
        <el-option v-for="e in envs" :key="e.id" :value="e.id" :label="e.name + (e.is_default ? '（默认）' : '')" />
      </el-select>
      <el-button plain size="small" @click="envDlg = true"><el-icon><Setting /></el-icon>管理环境</el-button>
      <div style="flex: 1" />
      <el-button plain size="small" @click="openColDlg"><el-icon><FolderAdd /></el-icon>新建集合</el-button>
      <el-button type="primary" size="small" @click="newApi()"><el-icon><Plus /></el-icon>新建接口</el-button>
    </div>

    <div class="card api-body">
      <!-- 左侧树 -->
      <aside class="api-side">
        <template v-for="item in displayList" :key="item.node.id">
          <div class="col-group" :style="{ marginLeft: item.depth * 14 + 'px' }">
            <div class="col-head">
              <el-icon class="col-arrow" :class="{ open: item.node._open }" @click="item.node._open = !item.node._open"><CaretRight /></el-icon>
              <el-icon color="#e6a23c"><FolderOpened /></el-icon>
              <span class="col-name" @click="item.node._open = !item.node._open">{{ item.node.name }}</span>
              <span class="col-count">({{ colCount(item.node) }})</span>
              <el-dropdown trigger="click" @command="cmd => colCmd(cmd, item.node)">
                <el-icon class="col-more"><MoreFilled /></el-icon>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="addApi">新增接口</el-dropdown-item>
                    <el-dropdown-item command="addSub">新增子目录</el-dropdown-item>
                    <el-dropdown-item command="rename" divided>重命名</el-dropdown-item>
                    <el-dropdown-item command="delete">删除集合</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div v-show="item.node._open">
              <div
                v-for="rq in filterReqs(item.node.requests)" :key="rq.id"
                class="req-item" :class="{ active: currentId === rq.id }"
                @click="loadApi(rq.id)"
              >
                <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ shortMethod(rq.method) }}</span>
                <span class="req-name">{{ rq.name }}</span>
                <el-icon class="req-del" title="移动到其他目录" @click.stop="openMoveApi(rq)"><Position /></el-icon>
                <el-icon class="req-del" @click.stop="removeApi(rq)"><Delete /></el-icon>
              </div>
            </div>
          </div>
        </template>
        <div v-if="filteredOrphans.length" class="col-group">
          <div class="col-head"><el-icon><Tickets /></el-icon><span class="col-name">未分组</span><span class="col-count">({{ filteredOrphans.length }})</span></div>
          <div
            v-for="rq in filteredOrphans" :key="rq.id"
            class="req-item" :class="{ active: currentId === rq.id }"
            @click="loadApi(rq.id)"
          >
            <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ shortMethod(rq.method) }}</span>
            <span class="req-name">{{ rq.name }}</span>
            <el-icon class="req-del" title="移动到其他目录" @click.stop="openMoveApi(rq)"><Position /></el-icon>
            <el-icon class="req-del" @click.stop="removeApi(rq)"><Delete /></el-icon>
          </div>
        </div>
        <el-empty v-if="!displayList.length && !filteredOrphans.length" :description="keyword ? '无匹配接口' : '暂无接口，点击右上角新建'" :image-size="70" />
      </aside>

      <!-- 右侧详情 -->
      <section v-if="currentId" class="api-main">
        <div class="api-head">
          <span class="m-tag m-lg" :class="'m-' + form.method.toLowerCase()">{{ form.method }}</span>
          <span class="api-title">{{ currentName }}</span>
          <el-tag v-if="form.last_status_code" size="small" :type="form.last_status_code < 400 ? 'success' : 'danger'" effect="plain">上次 {{ form.last_status_code }}</el-tag>
          <div style="flex: 1" />
          <el-radio-group v-model="viewTab" size="small">
            <el-radio-button value="debug">调试</el-radio-button>
            <el-radio-button value="doc">文档</el-radio-button>
          </el-radio-group>
        </div>

        <!-- 调试视图 -->
        <template v-if="viewTab === 'debug'">
          <div class="send-row">
            <el-select v-model="form.method" style="width: 108px">
              <el-option v-for="m in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']" :key="m" :value="m" :label="m" />
            </el-select>
            <el-input v-model="form.url" placeholder="/pet/{petId}  支持 {{变量}} 与 Base URL 拼接" @keyup.enter="send" />
            <el-button type="primary" :loading="sending" @click="send">发 送</el-button>
            <el-button plain @click="saveApi">保 存</el-button>
          </div>
          <el-tabs v-model="reqTab" class="req-tabs">
            <el-tab-pane :label="`Params（${enabledCount(form.params)}）`" name="params">
              <param-table v-model="form.params" />
            </el-tab-pane>
            <el-tab-pane :label="`Headers（${enabledCount(form.headers)}）`" name="headers">
              <kv-table v-model="form.headers" />
            </el-tab-pane>
            <el-tab-pane :label="`Body（${form.body_type === 'none' ? '无' : form.body_type}）`" name="body">
              <el-radio-group v-model="form.body_type" size="small" style="margin-bottom: 8px">
                <el-radio-button value="none">none</el-radio-button>
                <el-radio-button value="json">json</el-radio-button>
                <el-radio-button value="form">form</el-radio-button>
                <el-radio-button value="raw">raw</el-radio-button>
              </el-radio-group>
              <kv-table v-if="form.body_type === 'form'" v-model="formRows" />
              <el-input
                v-else-if="form.body_type !== 'none'"
                v-model="form.body" type="textarea" :rows="6" class="mono"
                :placeholder="form.body_type === 'json' ? '{&quot;key&quot;: &quot;value&quot;}' : '原始文本'"
              />
              <div v-else class="muted" style="font-size: 12px">该接口不携带请求体</div>
            </el-tab-pane>
            <el-tab-pane label="Auth" name="auth">
              <el-radio-group v-model="authType" size="small" style="margin-bottom: 10px">
                <el-radio-button value="none">不认证</el-radio-button>
                <el-radio-button value="bearer">Bearer</el-radio-button>
                <el-radio-button value="basic">Basic</el-radio-button>
              </el-radio-group>
              <div v-if="authType === 'bearer'" style="max-width: 520px">
                <el-input v-model="auth.token" placeholder="Token，支持 {{变量}}"><template #prepend>Token</template></el-input>
              </div>
              <div v-else-if="authType === 'basic'" style="max-width: 520px; display: flex; flex-direction: column; gap: 8px">
                <el-input v-model="auth.username" placeholder="用户名"><template #prepend>用户名</template></el-input>
                <el-input v-model="auth.password" type="password" show-password placeholder="密码"><template #prepend>密码</template></el-input>
              </div>
            </el-tab-pane>
          </el-tabs>

          <div class="resp-area">
            <div class="resp-head">
              <template v-if="resp">
                <el-tag :type="respTag" size="large" effect="dark" class="resp-status">{{ resp.error ? 'ERR' : resp.status }}</el-tag>
                <span class="resp-meta"><b>{{ resp.duration_ms }}</b> ms</span>
                <span class="resp-meta"><b>{{ fmtSize(resp.size) }}</b></span>
                <el-divider direction="vertical" />
                <el-radio-group v-model="respView" size="small">
                  <el-radio-button value="pretty">Pretty</el-radio-button>
                  <el-radio-button value="raw">Raw</el-radio-button>
                </el-radio-group>
              </template>
              <span v-else class="muted">点击「发送」调试接口</span>
            </div>
            <pre v-if="resp" class="resp-body mono">{{ respView === 'pretty' ? prettyBody : (resp.body || resp.error || '') }}</pre>
            <div v-else class="resp-empty muted">
              <el-icon :size="42" color="#c0cfe8"><Promotion /></el-icon>
              <div>点击「发送」按钮获取返回结果</div>
            </div>
          </div>
        </template>

        <!-- 文档视图 -->
        <template v-else>
          <div class="doc-form">
            <div class="muted doc-label">接口说明</div>
            <el-input v-model="form.description" type="textarea" :rows="3" placeholder="描述接口用途、业务规则、注意事项…" style="max-width: 720px" />
            <div class="muted doc-label" style="margin-top: 18px">Query / Path 参数</div>
            <div class="doc-table">
              <div class="doc-row doc-head-row muted">
                <span style="width: 24px" />
                <span style="width: 22%">参数名</span>
                <span style="width: 20%">示例值</span>
                <span style="width: 14%">类型</span>
                <span style="flex: 1">说明</span>
                <span style="width: 24px" />
              </div>
              <div v-for="(pr, i) in docParams" :key="i" class="doc-row">
                <el-checkbox :model-value="pr.enabled !== 0" @change="v => setParam(i, 'enabled', v ? 1 : 0)" />
                <el-input :model-value="pr.key" placeholder="参数名" size="small" @update:model-value="v => setParam(i, 'key', v)" />
                <el-input :model-value="pr.value" placeholder="示例值" size="small" @update:model-value="v => setParam(i, 'value', v)" />
                <el-select :model-value="pr.type || 'string'" size="small" @update:model-value="v => setParam(i, 'type', v)">
                  <el-option v-for="t in ['string', 'integer', 'number', 'boolean']" :key="t" :value="t" :label="t" />
                </el-select>
                <el-input :model-value="pr.desc" placeholder="参数说明，如 宠物 ID" size="small" @update:model-value="v => setParam(i, 'desc', v)" />
                <el-icon class="req-del" @click="docParams.splice(i, 1)"><Delete /></el-icon>
              </div>
              <el-button link type="primary" size="small" style="margin-top: 6px" @click="docParams.push({ key: '', value: '', type: 'string', desc: '', enabled: 1 })">
                <el-icon><Plus /></el-icon>添加参数
              </el-button>
            </div>
            <div style="margin-top: 20px">
              <el-button type="primary" @click="saveApi">保存文档</el-button>
            </div>
          </div>
        </template>
      </section>

      <section v-else class="api-main api-empty">
        <el-empty description="从左侧选择一个接口，或新建接口开始" :image-size="90" />
      </section>
    </div>

    <!-- 新建接口弹窗 -->
    <el-dialog v-model="newDlg" title="新建接口" width="460px">
      <el-form label-width="70px">
        <el-form-item label="名称" required><el-input v-model="newForm.name" placeholder="如 查询宠物详情" /></el-form-item>
        <el-form-item label="方法">
          <el-select v-model="newForm.method" style="width: 120px">
            <el-option v-for="m in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']" :key="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="路径" required><el-input v-model="newForm.url" placeholder="/pet/{petId} 或完整 URL" /></el-form-item>
        <el-form-item label="集合">
          <el-select v-model="newForm.collection_id" clearable placeholder="不分组">
            <el-option v-for="c in colOptions" :key="c.id" :value="c.id" :label="c.label" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newDlg = false">取消</el-button>
        <el-button type="primary" @click="doNewApi">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新建集合弹窗 -->
    <el-dialog v-model="colDlg" title="新建集合" width="420px">
      <el-form label-width="70px">
        <el-form-item label="名称" required><el-input v-model="colName" @keyup.enter="doCreateCol" /></el-form-item>
        <el-form-item label="上级目录">
          <el-select v-model="colParent" clearable placeholder="无（顶层集合）" style="width: 100%">
            <el-option v-for="c in colOptions" :key="c.id" :value="c.id" :label="c.label" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="colDlg = false">取消</el-button>
        <el-button type="primary" @click="doCreateCol">创建</el-button>
      </template>
    </el-dialog>

    <!-- 移动接口到目录 -->
    <el-dialog v-model="moveApiDlg" :title="`移动「${moveApiReq?.name || ''}」到目录`" width="420px">
      <el-input v-model="moveKeyword" placeholder="搜索目录名称" clearable style="margin-bottom: 10px" />
      <div class="move-list">
        <div class="move-item" :class="{ active: moveApiTarget === 'root' }" role="button" @click="moveApiTarget = 'root'">
          <el-icon><Tickets /></el-icon>
          <span class="move-name">未分组</span>
          <el-icon v-if="moveApiTarget === 'root'" class="move-check"><Check /></el-icon>
        </div>
        <div
          v-for="c in moveOptions" :key="c.id" class="move-item" role="button"
          :class="{ active: moveApiTarget === c.id, disabled: moveApiReq && c.id === moveApiReq.collection_id }"
          @click="!(moveApiReq && c.id === moveApiReq.collection_id) && (moveApiTarget = c.id)"
        >
          <el-icon><FolderOpened /></el-icon>
          <span class="move-name">{{ c.label }}</span>
          <el-tag v-if="moveApiReq && c.id === moveApiReq.collection_id" size="small" type="info" effect="plain">当前目录</el-tag>
          <el-icon v-else-if="moveApiTarget === c.id" class="move-check"><Check /></el-icon>
        </div>
        <div v-if="!moveOptions.length" class="muted" style="padding: 16px; text-align: center; font-size: 13px">无匹配目录</div>
      </div>
      <template #footer>
        <el-button @click="moveApiDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!moveApiTarget" @click="confirmMoveApi">确认移动</el-button>
      </template>
    </el-dialog>

    <!-- 环境管理弹窗（与调试页共用逻辑） -->
    <el-dialog v-model="envDlg" title="环境变量管理" width="760px" top="6vh">
      <div style="display: flex; gap: 10px; margin-bottom: 10px; align-items: center">
        <el-select v-model="envEditId" style="width: 220px" placeholder="选择环境" @change="loadEnvEdit">
          <el-option v-for="e in envs" :key="e.id" :value="e.id" :label="e.name" />
        </el-select>
        <el-button size="small" type="primary" plain @click="newEnv"><el-icon><Plus /></el-icon>新建环境</el-button>
        <div style="flex: 1" />
        <el-button v-if="envEditId" size="small" type="danger" plain @click="removeEnv">删除环境</el-button>
      </div>
      <template v-if="envEditId">
        <el-form label-width="90px" style="max-width: 640px">
          <el-form-item label="环境名称"><el-input v-model="envForm.name" /></el-form-item>
          <el-form-item label="Base URL"><el-input v-model="envForm.base_url" placeholder="如 https://api.example.com/v1" /></el-form-item>
          <el-form-item label="设为默认"><el-switch v-model="envForm.is_default" :active-value="1" :inactive-value="0" /></el-form-item>
        </el-form>
        <div class="muted" style="margin: 4px 0 6px">变量列表（请求中以 <code v-pre>{{ '{{key}}' }}</code> 引用）</div>
        <kv-table v-model="envForm.variables" />
        <div style="margin-top: 14px; text-align: right">
          <el-button type="primary" @click="saveEnv">保存环境</el-button>
        </div>
      </template>
      <el-empty v-else description="选择或新建一个环境开始配置" :image-size="70" />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '../api'
import KvTable from '../components/KvTable.vue'
import ParamTable from '../components/ParamTable.vue'

const projects = ref([])
const projectId = ref(null)
const envs = ref([])
const envId = ref(null)
const collections = ref([])
const orphans = ref([])
const keyword = ref('')

const currentId = ref(null)
const currentName = ref('')
const viewTab = ref('debug')
const form = ref({ method: 'GET', url: '', body_type: 'none', body: '', params: [], headers: [], description: '', last_status_code: null })
const formRows = ref([{ key: '', value: '', enabled: 1 }])
const docParams = ref([])
const authType = ref('none')
const auth = ref({ token: '', username: '', password: '' })
const reqTab = ref('params')

const resp = ref(null)
const respView = ref('pretty')
const sending = ref(false)

const respTag = computed(() => {
  if (!resp.value || resp.value.error) return 'danger'
  const s = resp.value.status
  if (s >= 200 && s < 300) return 'success'
  if (s >= 300 && s < 400) return 'warning'
  return 'danger'
})
const prettyBody = computed(() => {
  const t = resp.value?.body
  if (!t) return resp.value?.error || ''
  try { return JSON.stringify(JSON.parse(t), null, 2) } catch { return t }
})

function shortMethod(m) {
  return { DELETE: 'DEL', OPTIONS: 'OPT', PATCH: 'PATCH' }[m] || m
}
function fmtSize(n) {
  if (n == null) return '-'
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(2) + ' MB'
}
function enabledCount(rows) {
  return (rows || []).filter(r => r.key && r.enabled).length
}
function filterReqs(list) {
  if (!keyword.value) return list
  const k = keyword.value.toLowerCase()
  return list.filter(r => r.name.toLowerCase().includes(k) || (r.url || '').toLowerCase().includes(k))
}

// ---------- 集合树 ----------
// 后端返回平铺列表（含 parent_id），这里组树 + 展开成带层级的渲染列表
const colTree = computed(() => {
  const byId = new Map()
  for (const c of collections.value) { c.children = []; byId.set(c.id, c) }
  const roots = []
  for (const c of collections.value) {
    const p = c.parent_id ? byId.get(c.parent_id) : null
    if (p && p !== c) p.children.push(c)
    else roots.push(c)
  }
  const sortRec = list => {
    list.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id)
    list.forEach(n => sortRec(n.children))
  }
  sortRec(roots)
  return roots
})

function colCount(node) {
  let n = filterReqs(node.requests).length
  for (const ch of node.children) n += colCount(ch)
  return n
}

function hasDeepMatch(node) {
  if (filterReqs(node.requests).length) return true
  return node.children.some(hasDeepMatch)
}

const displayList = computed(() => {
  const out = []
  const walk = (list, depth) => {
    for (const n of list) {
      if (keyword.value && !hasDeepMatch(n)) continue
      out.push({ node: n, depth })
      walk(n.children, depth + 1)
    }
  }
  walk(colTree.value, 0)
  return out
})

// 下拉用的层级缩进选项（新建接口/新建集合共用）
const colOptions = computed(() => {
  const out = []
  const walk = (list, depth) => {
    for (const n of list) {
      out.push({ id: n.id, label: (depth ? '　'.repeat(depth) + '└ ' : '') + n.name })
      walk(n.children, depth + 1)
    }
  }
  walk(colTree.value, 0)
  return out
})

const filteredOrphans = computed(() => filterReqs(orphans.value))

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
  resp.value = null
  await Promise.all([loadEnvs(), loadCollections()])
  const def = envs.value.find(e => e.is_default)
  envId.value = def ? def.id : null
}

async function loadEnvs() {
  envs.value = await get(`/wb/api-envs?project_id=${projectId.value}`)
}

async function loadCollections() {
  const d = await get(`/wb/api-collections?project_id=${projectId.value}`)
  for (const c of d.collections) if (c._open === undefined) c._open = true
  collections.value = d.collections
  orphans.value = d.orphans
}

// ---------- 接口 ----------
function resetEditor() {
  currentId.value = null
  currentName.value = ''
  form.value = { method: 'GET', url: '', body_type: 'none', body: '', params: [], headers: [], description: '', last_status_code: null }
  docParams.value = []
  formRows.value = [{ key: '', value: '', enabled: 1 }]
  authType.value = 'none'
  auth.value = { token: '', username: '', password: '' }
  resp.value = null
}

function newApi(collectionId = null) {
  // 防御：若误传事件对象（如 @click="newApi" 直绑），回退为 null，避免下拉显示 [object PointerEvent]
  if (typeof collectionId !== 'number') collectionId = null
  resetEditor()
  newForm.value = { name: '', method: 'GET', url: '', collection_id: collectionId }
  newDlg.value = true
}

const newDlg = ref(false)
const newForm = ref({ name: '', method: 'GET', url: '', collection_id: null })

async function doNewApi() {
  if (!newForm.value.name || !newForm.value.url) return ElMessage.warning('请填写接口名称与路径')
  const d = await post('/wb/api-requests', {
    project_id: projectId.value,
    collection_id: newForm.value.collection_id || null,
    name: newForm.value.name,
    method: newForm.value.method,
    url: newForm.value.url,
    headers: [], params: [], body_type: 'none'
  })
  newDlg.value = false
  await loadCollections()
  await loadApi(d.id)
  ElMessage.success('接口已创建')
}

async function loadApi(id) {
  const r = await get(`/wb/api-requests/${id}`)
  currentId.value = r.id
  currentName.value = r.name
  form.value = {
    method: r.method || 'GET', url: r.url || '',
    body_type: r.body_type || 'none', body: r.body || '',
    params: (r.params || []).map(p => ({ key: p.key || '', value: p.value || '', type: p.type || 'string', desc: p.desc || '', enabled: p.enabled ?? 1 })),
    headers: (r.headers || []).map(h => ({ key: h.key || '', value: h.value || '', enabled: h.enabled ?? 1 })),
    description: r.description || '',
    last_status_code: r.last_status_code
  }
  docParams.value = form.value.params.map(p => ({ ...p }))
  if (!form.value.headers.length) form.value.headers = [{ key: '', value: '', enabled: 1 }]
  formRows.value = [{ key: '', value: '', enabled: 1 }]
  const a = r.auth || {}
  authType.value = a.type || 'none'
  auth.value = { token: a.token || '', username: a.username || '', password: a.password || '' }
  resp.value = null
}

function syncDocParams() {
  // 文档视图的编辑同步回调试 params（保存时统一提交）
  form.value.params = docParams.value.map(p => ({ ...p }))
}

async function saveApi() {
  if (!currentId.value) return
  syncDocParams()
  await put(`/wb/api-requests/${currentId.value}`, {
    name: currentName.value,
    method: form.value.method,
    url: form.value.url,
    headers: form.value.headers.filter(h => h.key),
    params: form.value.params.filter(p => p.key),
    description: form.value.description,
    body_type: form.value.body_type,
    body: form.value.body_type === 'form' ? JSON.stringify(formRows.value.filter(r => r.key)) : (form.value.body || null),
    auth: authType.value === 'none' ? null : { type: authType.value, ...auth.value }
  })
  await loadCollections()
  ElMessage.success('接口已保存')
}

async function removeApi(rq) {
  await ElMessageBox.confirm(`删除接口「${rq.name}」？`, '删除', { type: 'warning' })
  await del(`/wb/api-requests/${rq.id}`)
  if (currentId.value === rq.id) resetEditor()
  await loadCollections()
  ElMessage.success('已删除')
}

// ---------- 发送调试 ----------
async function send() {
  if (!form.value.url) return ElMessage.warning('请填写请求 URL')
  sending.value = true
  try {
    const d = await post('/wb/api-send', {
      project_id: projectId.value,
      environment_id: envId.value || null,
      request_id: currentId.value,
      method: form.value.method,
      url: form.value.url,
      headers: form.value.headers,
      query_params: form.value.params,
      body_type: form.value.body_type,
      body: form.value.body_type === 'form' ? JSON.stringify(formRows.value.filter(r => r.key)) : (form.value.body || null),
      auth: authType.value === 'none' ? null : { type: authType.value, ...auth.value }
    })
    resp.value = d
    respView.value = 'pretty'
    form.value.last_status_code = d.status
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    sending.value = false
  }
}

// ---------- 文档参数 ----------
function setParam(i, k, v) {
  docParams.value[i] = { ...docParams.value[i], [k]: v }
}

// ---------- 集合 ----------
const colDlg = ref(false)
const colName = ref('')
const colParent = ref(null)

function openColDlg() {
  colName.value = ''
  colParent.value = null
  colDlg.value = true
}

async function doCreateCol() {
  if (!colName.value) return ElMessage.warning('请填写集合名称')
  await post('/wb/api-collections', { project_id: projectId.value, name: colName.value, parent_id: colParent.value || null })
  colDlg.value = false
  loadCollections()
}

async function colCmd(cmd, col) {
  if (cmd === 'addApi') {
    newApi(col.id)  // 目录内快捷新增：弹窗默认选中该目录
  } else if (cmd === 'addSub') {
    const { value } = await ElMessageBox.prompt('子目录名称', `在「${col.name}」下新增子目录`, { inputPlaceholder: '如 用户模块' })
    if (value && value.trim()) {
      await post('/wb/api-collections', { project_id: projectId.value, name: value.trim(), parent_id: col.id })
      loadCollections()
    }
  } else if (cmd === 'rename') {
    const { value } = await ElMessageBox.prompt('新名称', '重命名集合', { inputValue: col.name })
    if (value) {
      await put(`/wb/api-collections/${col.id}`, { project_id: projectId.value, name: value })
      loadCollections()
    }
  } else {
    await ElMessageBox.confirm(`删除集合「${col.name}」？集合内接口将变为未分组，其子目录将上移一级。`, '删除', { type: 'warning' })
    await del(`/wb/api-collections/${col.id}`)
    loadCollections()
  }
}

// ---------- 移动接口到目录 ----------
const moveApiDlg = ref(false)
const moveApiReq = ref(null)
const moveApiTarget = ref(null)
const moveKeyword = ref('')

const moveOptions = computed(() =>
  colOptions.value.filter(c => !moveKeyword.value || c.label.toLowerCase().includes(moveKeyword.value.toLowerCase()))
)

function openMoveApi(rq) {
  moveApiReq.value = rq
  moveApiTarget.value = null  // 默认不选；当前目录禁选，必须换一个目标
  moveKeyword.value = ''
  moveApiDlg.value = true
}

async function confirmMoveApi() {
  const rq = moveApiReq.value
  if (!rq || !moveApiTarget.value) return ElMessage.warning('请选择目标目录')
  const targetCol = moveApiTarget.value === 'root' ? null : Number(moveApiTarget.value)
  if (targetCol === rq.collection_id) return ElMessage.info('接口已在该目录下')
  // PUT 需携带必填的 name/url；其余参数保持不变（仅调整目录归属）
  await put(`/wb/api-requests/${rq.id}`, {
    project_id: projectId.value,
    collection_id: targetCol,
    name: rq.name,
    method: rq.method,
    url: rq.url,
  })
  moveApiDlg.value = false
  await loadCollections()
  ElMessage.success(`已移动到「${targetCol === null ? '未分组' : (collections.value.find(c => c.id === targetCol)?.name || '目录')}」`)
}

// ---------- 环境 ----------
const envDlg = ref(false)
const envEditId = ref(null)
const envForm = ref({ name: '', base_url: '', is_default: 0, variables: [{ key: '', value: '' }] })

function newEnv() {
  post('/wb/api-envs', { project_id: projectId.value, name: '新环境' }).then(async d => {
    envEditId.value = d.id
    await loadEnvs()
    envForm.value = { name: '新环境', base_url: '', is_default: 0, variables: [{ key: '', value: '' }] }
  })
}

async function loadEnvEdit() {
  const e = envs.value.find(x => x.id === envEditId.value)
  envForm.value = {
    name: e.name, base_url: e.base_url || '', is_default: e.is_default,
    variables: (e.variables || []).map(v => ({ key: v.key, value: v.value }))
  }
  if (!envForm.value.variables.length) envForm.value.variables = [{ key: '', value: '' }]
}

async function saveEnv() {
  if (!envForm.value.name) return ElMessage.warning('请填写环境名称')
  await put(`/wb/api-envs/${envEditId.value}`, {
    project_id: projectId.value,
    name: envForm.value.name,
    base_url: envForm.value.base_url,
    is_default: envForm.value.is_default,
    variables: envForm.value.variables.filter(v => v.key)
  })
  await loadEnvs()
  ElMessage.success('环境已保存')
}

async function removeEnv() {
  await ElMessageBox.confirm('删除该环境？', '删除', { type: 'warning' })
  await del(`/wb/api-envs/${envEditId.value}`)
  envEditId.value = null
  await loadEnvs()
}

onMounted(loadProjects)
</script>

<style scoped>
.api-page { display: flex; flex-direction: column; height: calc(100vh - 48px); }
.toolbar { display: flex; gap: 10px; align-items: center; padding: 12px 16px; }
.api-body { display: flex; flex: 1; min-height: 0; padding: 0; overflow: hidden; }
.api-side { width: 280px; flex-shrink: 0; border-right: 1px solid var(--wb-border); padding: 8px 10px; overflow-y: auto; }
.col-group { margin-bottom: 6px; }
.col-head { display: flex; align-items: center; gap: 6px; padding: 4px 2px; font-weight: 600; font-size: 13px; }
.col-arrow { cursor: pointer; transition: transform 0.15s; }
.col-arrow.open { transform: rotate(90deg); }
.col-name { cursor: pointer; }
.col-count { color: #9ca3af; font-size: 12px; font-weight: 400; }
.col-more { margin-left: auto; color: #9ca3af; cursor: pointer; }
.req-item { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.req-item:hover { background: #f3f6fb; }
.req-item.active { background: var(--wb-accent-soft, #eef2ff); }
.req-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.req-del { color: #d1d5db; flex-shrink: 0; }
.req-del:hover { color: #ef4444; }

/* 移动接口弹窗 */
.move-list { max-height: 300px; overflow-y: auto; border: 1px solid var(--wb-border); border-radius: 8px; }
.move-item {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 14px; cursor: pointer; font-size: 13.5px; color: #374151;
  border-bottom: 1px solid #f1f5f9;
}
.move-item:last-child { border-bottom: none; }
.move-item:hover { background: #f3f6fb; }
.move-item.active { background: var(--wb-accent-soft, #eef2ff); color: var(--wb-accent); font-weight: 600; }
.move-item.disabled { color: #9ca3af; cursor: not-allowed; }
.move-name { flex: 1; }
.move-check { color: var(--wb-accent); }

/* 方法着色（Apifox 风格） */
.m-tag {
  width: 46px; text-align: center; flex-shrink: 0;
  font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 0;
  font-family: Consolas, Monaco, monospace;
}
.m-lg { width: 64px; font-size: 13px; padding: 3px 0; }
.m-get { color: #16a34a; background: #dcfce7; }
.m-post { color: #d97706; background: #fef3c7; }
.m-put { color: #2563eb; background: #dbeafe; }
.m-delete { color: #dc2626; background: #fee2e2; }
.m-patch { color: #7c3aed; background: #ede9fe; }
.m-head, .m-options { color: #6b7280; background: #f3f4f6; }

.api-main { flex: 1; min-width: 0; display: flex; flex-direction: column; padding: 12px 16px; overflow-y: auto; }
.api-empty { align-items: center; justify-content: center; }
.api-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.api-title { font-weight: 600; font-size: 15px; }
.send-row { display: flex; gap: 8px; }
.req-tabs { margin-top: 10px; }
.resp-area { border-top: 1px solid var(--wb-border); margin-top: 4px; padding-top: 10px; }
.resp-head { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.resp-status { font-weight: 700; min-width: 56px; text-align: center; }
.resp-meta { font-size: 13px; color: #6b7280; }
.resp-meta b { color: #111827; }
.resp-body { background: #f8fafc; border: 1px solid var(--wb-border); border-radius: 8px; padding: 12px; font-size: 12px; max-height: 38vh; overflow: auto; margin: 0; white-space: pre-wrap; word-break: break-all; }
.resp-empty { padding: 30px 0; text-align: center; font-size: 13px; color: #9ca3af; display: flex; flex-direction: column; gap: 8px; align-items: center; }

/* 文档视图 */
.doc-form { max-width: 860px; }
.doc-label { font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #6b7280; }
.doc-table { max-width: 760px; }
.doc-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.doc-row .el-input, .doc-row .el-select { flex-shrink: 0; }
.doc-row > .el-input:nth-child(2) { width: 22%; }
.doc-row > .el-input:nth-child(3) { width: 20%; }
.doc-row > .el-select { width: 14%; }
.doc-row > .el-input:nth-child(5) { width: auto; flex: 1; }
.mono { font-family: Consolas, Monaco, monospace; }
</style>
