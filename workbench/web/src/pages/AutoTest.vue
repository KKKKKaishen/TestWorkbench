<template>
  <div class="page">
    <div class="page-title">自动化测试</div>
    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="选择项目" style="width:220px" @change="loadAll">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-tabs v-model="tab" style="flex:1; margin-left:12px" @tab-change="loadTab">
          <el-tab-pane label="脚本管理" name="scripts" />
          <el-tab-pane label="执行历史" name="runs" />
        </el-tabs>
      </div>

      <!-- 脚本管理（EX-M1） -->
      <template v-if="tab === 'scripts'">
        <div class="toolbar">
          <el-button type="primary" :disabled="!projectId" @click="openGenerate">
            <el-icon><MagicStick /></el-icon>AI 生成脚本骨架
          </el-button>
          <el-button :disabled="!projectId" @click="openImport">
            <el-icon><Upload /></el-icon>手动导入脚本
          </el-button>
          <span v-if="genNote" class="muted" style="font-size:12px">{{ genNote }}</span>
        </div>
        <el-table :data="scripts" v-loading="loading" stripe>
          <el-table-column prop="case_no" label="用例编号" width="130" />
          <el-table-column prop="title" label="脚本标题" min-width="200" show-overflow-tooltip />
          <el-table-column label="来源" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="{ ai: 'success', template: 'warning', manual: 'info' }[row.gen_source]">
                {{ { ai: 'AI', template: '模板', manual: '手改' }[row.gen_source] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="script_size" label="代码量" width="80">
            <template #default="{ row }">{{ row.script_size }} 字</template>
          </el-table-column>
          <el-table-column label="最近执行" width="170">
            <template #default="{ row }">
              <template v-if="row.last_run">
                <el-tag size="small" :type="statusTag(row.last_run.status)">{{ statusMap[row.last_run.status] }}</el-tag>
                <div class="muted" style="font-size:12px; margin-top:2px">
                  {{ row.last_run.passed_count }}/{{ row.last_run.total_count }} 通过 · {{ fmt(row.last_run.finished_at) }}
                </div>
              </template>
              <span v-else class="muted">未执行</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" :disabled="row.status !== 'ready' && row.script_size < 30"
                         :loading="runningId === row.id" @click="runScript(row)">运行</el-button>
              <el-button size="small" link type="primary" @click="openEditor(row)">编辑</el-button>
              <el-button size="small" link type="danger" @click="removeScript(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- 执行历史（EX-M2） -->
      <template v-if="tab === 'runs'">
        <el-table :data="runs" v-loading="loading" stripe>
          <el-table-column prop="case_no" label="用例编号" width="130" />
          <el-table-column prop="script_title" label="脚本" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusMap[row.status] }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="130">
            <template #default="{ row }">
              {{ row.passed_count }}/{{ row.total_count }} 通过
              <span v-if="row.failed_count" class="fail-n">（{{ row.failed_count }} 失败）</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90">
            <template #default="{ row }">{{ row.duration_ms != null ? row.duration_ms + 'ms' : '—' }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="开始时间" width="160" />
          <el-table-column prop="finished_at" label="结束时间" width="160" />
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="openRunDetail(row)">详情</el-button>
              <el-button v-if="row.status !== 'passed'" size="small" link type="warning"
                         :loading="analyzingId === row.id" @click="analyzeRun(row)">AI 分析</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>

    <!-- AI 生成骨架：用例多选 -->
    <el-dialog v-model="genDlg" title="AI 生成脚本骨架" width="720px">
      <div class="muted" style="margin-bottom:8px; font-size:12px">
        从用例库勾选用例，AI 生成 pytest + Playwright 脚本骨架；AI key 未配置时使用内置模板。
      </div>
      <el-table ref="genTable" :data="genCases" size="small" max-height="340" @selection-change="onGenChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="case_no" label="编号" width="120" />
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="case_type" label="类型" width="90" />
        <el-table-column prop="priority" label="优先级" width="70" />
      </el-table>
      <template #footer>
        <el-button @click="genDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!genCaseIds.length" :loading="generating" @click="generate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 脚本编辑器（人工补全） -->
    <el-dialog v-model="editorDlg" :title="editing ? `编辑脚本 · ${editing.title}` : ''" width="860px" top="4vh">
      <el-input v-model="editCode" type="textarea" :rows="24" class="code-editor"
                placeholder="pytest + Playwright（sync API）脚本" spellcheck="false" />
      <div class="muted" style="margin-top:8px; font-size:12px">
        保存后状态变为「可执行」。选择器不确定处请补全 TODO；首次运行会启动 chromium。
      </div>
      <template #footer>
        <el-button @click="editorDlg = false">取消</el-button>
        <el-button type="primary" @click="saveScript">保存</el-button>
      </template>
    </el-dialog>

    <!-- 手动导入脚本 -->
    <el-dialog v-model="importDlg" title="手动导入脚本" width="860px" top="4vh">
      <div class="toolbar" style="margin-bottom:10px">
        <el-input v-model="importForm.title" placeholder="脚本标题（选文件后自动填入文件名）" style="flex:1" />
        <el-input v-model="importForm.case_no" placeholder="关联用例编号（可选）" style="width:180px" />
      </div>
      <el-input v-model="importForm.script" type="textarea" :rows="20" class="code-editor"
                placeholder="粘贴 pytest + Playwright（sync API）脚本，需包含 def test_xxx(page): 测试函数" spellcheck="false" />
      <div class="toolbar" style="margin-top:10px; margin-bottom:0">
        <button class="file-btn" type="button">选择本地 .py 文件（可多选，直接批量导入）
          <input type="file" accept=".py" multiple @change="onImportFiles" />
        </button>
        <span class="muted" style="font-size:12px">导入后状态为「可执行」，可直接点运行；关联用例编号为空则不挂接用例库。</span>
      </div>
      <template #footer>
        <el-button @click="importDlg = false">取消</el-button>
        <el-button type="primary" :disabled="!importForm.script.trim()" @click="saveImport">保存并创建</el-button>
      </template>
    </el-dialog>

    <!-- 执行详情 -->
    <el-drawer v-model="runDrawer" size="62%" :title="runDetail ? `执行详情 #${runDetail.id} · ${runDetail.script_title}` : ''">
      <template v-if="runDetail">
        <div class="detail-head">
          <el-tag :type="statusTag(runDetail.status)">{{ statusMap[runDetail.status] }}</el-tag>
          <span class="muted">{{ runDetail.passed_count }}/{{ runDetail.total_count }} 通过 · {{ runDetail.duration_ms }}ms</span>
          <span class="muted">{{ fmt(runDetail.created_at) }}</span>
        </div>

        <div class="sec-title">pytest 输出</div>
        <pre class="pytest-out">{{ runDetail.output }}</pre>

        <div class="sec-title">产物归档（截图 / Trace / 视频）</div>
        <div v-if="!runDetail.attachments.length" class="muted" style="font-size:12px">
          无产物（通过执行仅保留日志；失败执行自动留存截图与 Trace）。
        </div>
        <div class="att-grid">
          <div v-for="a in runDetail.attachments" :key="a.id" class="att-item">
            <template v-if="(a.mime_type || '').startsWith('image/')">
              <el-image :src="`/wb/attachments/${a.id}/file`" fit="cover" class="att-img"
                        :preview-src-list="[`/wb/attachments/${a.id}/file`]" preview-teleported />
            </template>
            <template v-else>
              <a :href="`/wb/attachments/${a.id}/file`" target="_blank" class="att-link">
                {{ a.mime_type === 'video/webm' ? '▶ 视频' : a.mime_type === 'application/zip' ? 'Trace 包' : '报告' }}
              </a>
            </template>
            <div class="muted" style="font-size:12px">{{ a.file_name }}</div>
          </div>
        </div>

        <div class="sec-title">AI 失败分析</div>
        <div v-if="runDetail.analysis" class="analysis-block">{{ runDetail.analysis }}</div>
        <div v-else class="muted" style="font-size:12px">
          {{ runDetail.status === 'passed' ? '执行通过，无需分析。' : '尚未分析，点击执行历史中的「AI 分析」生成失败原因与修复建议。' }}
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put, del } from '../api'
import { MagicStick, Upload } from '@element-plus/icons-vue'

const projects = ref([])
const projectId = ref(null)
const tab = ref('scripts')
const loading = ref(false)
const scripts = ref([])
const runs = ref([])

const statusMap = { running: '执行中', passed: '通过', failed: '失败', error: '错误' }
const statusTag = s => ({ running: 'warning', passed: 'success', failed: 'danger', error: 'danger' }[s] || 'info')

// 生成骨架
const genDlg = ref(false)
const genCases = ref([])
const genCaseIds = ref([])
const genTable = ref(null)
const generating = ref(false)
const genNote = ref('')

// 编辑器
const editorDlg = ref(false)
const editing = ref(null)
const editCode = ref('')

// 手动导入
const importDlg = ref(false)
const importForm = ref({ title: '', case_no: '', script: '' })

// 运行
const runningId = ref(null)
let pollTimer = null

// 详情
const runDrawer = ref(false)
const runDetail = ref(null)
const analyzingId = ref(null)

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (projects.value.length) {
    projectId.value = projects.value[0].id
    loadAll()
  }
})

function loadAll() {
  loadTab('scripts')
  loadTab('runs')
}

async function loadTab(t) {
  if (!projectId.value) return
  loading.value = true
  try {
    if (t === 'scripts') scripts.value = await get(`/wb/auto-scripts?project_id=${projectId.value}`)
    else if (t === 'runs') runs.value = await get(`/wb/auto-runs?project_id=${projectId.value}`)
  } finally { loading.value = false }
}

const fmt = s => (s ? String(s).replace('T', ' ').slice(0, 19) : '')

// ---- 生成骨架 ----
async function openGenerate() {
  const q = new URLSearchParams({ project_id: projectId.value, size: 200, status: 'active' })
  genCases.value = (await get('/wb/cases?' + q)).items
  genCaseIds.value = []
  genDlg.value = true
}

function onGenChange(rows) {
  genCaseIds.value = rows.map(r => r.id)
}

async function generate() {
  generating.value = true
  try {
    const r = await post('/wb/auto-scripts/generate', { project_id: projectId.value, case_ids: genCaseIds.value })
    genDlg.value = false
    genNote.value = r.note || ''
    ElMessage.success(`已生成 ${r.created.length} 个脚本骨架${r.ai_used ? '（AI）' : '（模板，待补全）'}`)
    loadTab('scripts')
  } catch (e) {
    ElMessage.error(e.message)
  } finally { generating.value = false }
}

// ---- 编辑器 ----
async function openEditor(row) {
  const d = await get(`/wb/auto-scripts/${row.id}`)
  editing.value = d
  editCode.value = d.script
  editorDlg.value = true
}

async function saveScript() {
  await put(`/wb/auto-scripts/${editing.value.id}`, { script: editCode.value, status: 'ready' })
  ElMessage.success('脚本已保存，可执行')
  editorDlg.value = false
  loadTab('scripts')
}

// ---- 手动导入 ----
function openImport() {
  importForm.value = { title: '', case_no: '', script: '' }
  importDlg.value = true
}

async function saveImport() {
  const f = importForm.value
  try {
    const r = await post('/wb/auto-scripts', { project_id: projectId.value, title: f.title, script: f.script, case_no: f.case_no || null })
    ElMessage.success(`脚本已导入（#${r.id}），可执行`)
    importDlg.value = false
    loadTab('scripts')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function onImportFiles(ev) {
  const files = Array.from(ev.target.files || [])
  ev.target.value = ''
  if (!files.length) return
  let ok = 0
  const errs = []
  for (const file of files) {
    try {
      const text = await file.text()
      if (!text.includes('def test_')) {
        errs.push(`${file.name}：无 test 函数`)
        continue
      }
      await post('/wb/auto-scripts', {
        project_id: projectId.value,
        title: file.name.replace(/\.py$/i, ''),
        script: text,
        case_no: null
      })
      ok += 1
    } catch (e) {
      errs.push(`${file.name}：${e.message}`)
    }
  }
  if (ok) {
    ElMessage.success(`已导入 ${ok} 个脚本，可执行`)
    importDlg.value = false
    loadTab('scripts')
  }
  if (errs.length) ElMessage.warning(errs.join('；'))
}

// ---- 运行（轮询状态） ----
async function runScript(row) {
  runningId.value = row.id
  try {
    const r = await post(`/wb/auto-scripts/${row.id}/run`)
    ElMessage.info('已提交执行，浏览器运行中…')
    pollRun(r.run_id)
  } catch (e) {
    runningId.value = null
    ElMessage.error(e.message)
  }
}

function pollRun(runId) {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(async () => {
    try {
      const d = await get(`/wb/auto-runs/${runId}`)
      if (d.status === 'running') { pollRun(runId); return }
      runningId.value = null
      loadTab('scripts'); loadTab('runs')
      if (d.status === 'passed') ElMessage.success(`执行通过（${d.passed_count}/${d.total_count}）`)
      else ElMessage.warning(`执行${statusMap[d.status]}，可在执行历史中查看详情与 AI 分析`)
    } catch { runningId.value = null }
  }, 3000)
}

// ---- 详情 / 分析 ----
async function openRunDetail(row) {
  runDetail.value = await get(`/wb/auto-runs/${row.id}`)
  runDrawer.value = true
}

async function analyzeRun(row) {
  analyzingId.value = row.id
  try {
    const r = await post(`/wb/auto-runs/${row.id}/analyze`)
    runDetail.value = await get(`/wb/auto-runs/${row.id}`)
    ElMessage.success('AI 分析完成')
  } catch (e) {
    ElMessage.error(e.message)
  } finally { analyzingId.value = null }
}

async function removeScript(row) {
  await ElMessageBox.confirm(`删除脚本「${row.title}」及其执行历史？`, '删除确认', { type: 'warning' })
  await del(`/wb/auto-scripts/${row.id}`)
  loadTab('scripts')
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.file-btn {
  position: relative; overflow: hidden; cursor: pointer;
  border: 1px dashed var(--wb-border, #dcdfe6); border-radius: 6px; background: #fff;
  padding: 7px 14px; font-size: 13px; color: var(--el-text-color-regular, #606266);
}
.file-btn:hover { border-color: var(--el-color-primary, #409eff); color: var(--el-color-primary, #409eff); }
.file-btn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.code-editor :deep(textarea) {
  font-family: Consolas, 'Courier New', monospace; font-size: 13px; line-height: 1.6;
  background: #f8fafc;
}
.detail-head { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.sec-title { font-weight: 600; margin: 18px 0 8px; font-size: 14px; }
.pytest-out {
  background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px;
  font-size: 12px; line-height: 1.6; font-family: Consolas, monospace;
  max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
.att-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.att-item { border: 1px solid var(--wb-border); border-radius: 8px; padding: 6px; }
.att-img { width: 100%; height: 110px; border-radius: 6px; }
.att-link { font-size: 13px; }
.analysis-block {
  white-space: pre-wrap; background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 8px; padding: 12px; font-size: 13px; line-height: 1.7;
}
.fail-n { color: #dc2626; }
</style>
