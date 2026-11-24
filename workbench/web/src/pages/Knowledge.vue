<template>
  <div class="page">
    <div class="page-title">知识库</div>

    <el-alert
      v-if="loaded && !embeddingReady"
      type="info" :closable="false" show-icon style="margin-bottom: 12px"
      title="当前使用关键词检索模式（开箱即用）"
      description="在「设置」页配置通义千问（DashScope）API Key 或在 .env 配置 EMBEDDING_API_KEY 后，新上传的文档将自动启用语义检索（更智能的相关性排序）。"
    />

    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="选择项目" style="width:200px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-upload
          :show-file-list="false" accept=".pdf,.docx,.md,.txt" multiple
          :before-upload="beforeUpload" :http-request="doUpload"
        >
          <el-button type="primary" :disabled="!projectId" :loading="uploading">
            <el-icon><Upload /></el-icon>上传文档
          </el-button>
        </el-upload>
        <span class="muted" style="font-size:12px">支持 PDF / Word(docx) / Excel(xlsx) / PPT(pptx) / Markdown / TXT，≤50MB，上传后自动向量化</span>
        <div style="flex:1" />
        <span v-if="kbFiles.length" class="muted" style="font-size:12px">
          {{ kbFiles.length }} 个文档 · {{ totalChunks }} 个分片
        </span>
      </div>

      <el-table :data="kbFiles" v-loading="loading && !kbFiles.length" stripe>
        <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="file_type" label="类型" width="70">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ (row.file_type || '').toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分片" width="90">
          <template #default="{ row }">{{ row.status === 'completed' ? row.chunk_count : '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tooltip v-if="row.status === 'failed' && row.error" :content="row.error" placement="top">
              <el-tag size="small" type="danger" effect="dark" style="cursor:help">失败</el-tag>
            </el-tooltip>
            <el-tag v-else-if="row.status === 'completed'" size="small" type="success" effect="dark">已索引</el-tag>
            <el-tag v-else-if="row.status === 'processing'" size="small" type="primary" effect="dark">
              <el-icon class="is-loading" style="margin-right:4px"><Loading /></el-icon>处理中
            </el-tag>
            <el-tag v-else size="small" type="info" effect="plain">排队中</el-tag>
            <span v-if="row.skipped_chunks" class="muted" style="font-size:11px; margin-left:4px">跳{{ row.skipped_chunks }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.uploaded_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <div class="op-line">
              <el-button link type="primary" size="small" @click="previewFile(row)">预览</el-button>
              <el-button link type="primary" size="small" @click="downloadFile(row)">下载</el-button>
              <el-button v-if="row.status === 'failed' || row.status === 'pending'" link type="primary" size="small" @click="retryFile(row)">重试</el-button>
              <el-button v-if="row.status === 'completed'" link type="primary" size="small" @click="reindexFile(row)">重建索引</el-button>
              <el-button link type="danger" size="small" @click="removeFile(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="projectId ? '暂无文档，点击上方「上传文档」添加项目知识' : '先选择一个项目'" :image-size="80" />
        </template>
      </el-table>
    </div>

    <div class="card" style="margin-top: 14px">
      <div class="sec-title">语义检索</div>
      <div class="search-line">
        <el-input
          v-model="query" placeholder="输入问题或关键词，从项目知识中检索相关片段…"
          clearable @keyup.enter="doSearch"
        />
        <el-input-number v-model="topK" :min="1" :max="20" style="width:110px" />
        <el-button type="primary" :loading="searching" :disabled="!projectId" @click="doSearch">
          <el-icon><Search /></el-icon>检 索
        </el-button>
      </div>
      <div v-if="searched && !searchResults.length && !searchError" class="muted" style="padding: 16px 0">
        未检索到相关内容（该项目可能还没有已入库的文档）
      </div>
      <el-alert v-if="searchError" type="error" :closable="false" style="margin-top:10px" :title="searchError" />
      <div v-for="(r, i) in searchResults" :key="i" class="search-result">
        <div class="sr-head">
          <el-icon><Document /></el-icon>
          <span class="sr-source">{{ r.source }}</span>
          <el-tag v-if="r.page" size="small" effect="plain">第 {{ r.page }} 页</el-tag>
          <div style="flex:1" />
          <el-tooltip content="一键复制内容" placement="top">
            <el-button link size="small" class="sr-copy" @click="copyResult(r)">
              <el-icon><CopyDocument /></el-icon>
              <span style="margin-left:2px">复制</span>
            </el-button>
          </el-tooltip>
        </div>
        <div class="sr-content">{{ r.content }}</div>
      </div>
    </div>

    <!-- 全屏文件预览 -->
    <el-dialog
      v-model="previewVisible" :title="previewRow?.filename || '文件预览'"
      fullscreen destroy-on-close class="kb-preview-dialog"
    >
      <div class="preview-wrap">
        <div v-if="previewLoading" class="preview-tip">
          <el-icon class="is-loading" :size="26"><Loading /></el-icon>
          <span style="margin-left:8px">正在加载预览…</span>
        </div>
        <div v-else-if="previewError" class="preview-tip">
          <span style="color: var(--el-color-danger)">{{ previewError }}</span>
        </div>
        <div v-show="!previewLoading && !previewError" class="preview-inner">
          <div v-if="previewKind === 'xlsx' && sheetList.length" class="sheet-tabs">
            <el-tag
              v-for="(s, i) in sheetList" :key="s.name" size="small"
              :effect="i === activeSheet ? 'dark' : 'plain'" class="sheet-tag"
              @click="switchSheet(i)"
            >{{ s.name }}</el-tag>
          </div>
          <div ref="previewBox" class="preview-box" />
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Upload, Search, Document, Loading, CopyDocument } from '@element-plus/icons-vue'
import { get, post, del } from '../api'

const projects = ref([])
const projectId = ref(null)
const kbFiles = ref([])
const embeddingReady = ref(false)
const loading = ref(false)
const loaded = ref(false)
const uploading = ref(false)
const query = ref('')
const topK = ref(5)
const searching = ref(false)
const searchResults = ref([])
const searchError = ref('')
const searched = ref(false)

let pollTimer = null
const totalChunks = computed(() => kbFiles.value.reduce((s, f) => s + (f.chunk_count || 0), 0))

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (projects.value.length) {
    projectId.value = projects.value[0].id
    await load()
  }
  loaded.value = true
})

onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })

async function load() {
  if (!projectId.value) { kbFiles.value = []; return }
  loading.value = true
  try {
    const d = await get(`/wb/knowledge?project_id=${projectId.value}`)
    kbFiles.value = d.files
    embeddingReady.value = d.embedding_ready
    schedulePoll()
  } catch (e) {
    ElMessage.error('加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

function schedulePoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  const busy = kbFiles.value.some(f => f.status === 'pending' || f.status === 'processing')
  if (busy) pollTimer = setInterval(load, 3000)
}

function beforeUpload(file) {
  const ok = /\.(pdf|docx|md|txt)$/i.test(file.name)
  if (!ok) { ElMessage.warning('仅支持 PDF / Word(docx) / Markdown / TXT'); return false }
  if (file.size > 50 * 1024 * 1024) { ElMessage.warning('文件不能超过 50MB'); return false }
  return true
}

async function doUpload({ file }) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`/wb/knowledge/upload?project_id=${projectId.value}`, { method: 'POST', body: fd })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
      throw new Error(detail)
    }
    ElMessage.success(`《${file.name}》已上传，正在后台向量化`)
    await load()
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
}

async function retryFile(f) {
  try {
    await post(`/wb/knowledge/files/${f.id}/retry?project_id=${projectId.value}`)
    ElMessage.success('已重新入队处理')
    load()
  } catch (e) { ElMessage.error(e.message) }
}

async function reindexFile(f) {
  try {
    await post(`/wb/knowledge/files/${f.id}/reindex?project_id=${projectId.value}`)
    ElMessage.success('开始重建索引')
    load()
  } catch (e) { ElMessage.error(e.message) }
}

async function removeFile(f) {
  try {
    await ElMessageBox.confirm(`确定删除《${f.filename}》？其向量索引将一并清除。`, '删除文档', { type: 'warning' })
  } catch { return }
  try {
    await del(`/wb/knowledge/files/${f.id}?project_id=${projectId.value}`)
    ElMessage.success('已删除')
    load()
  } catch (e) { ElMessage.error(e.message) }
}

async function doSearch() {
  if (!query.value.trim()) return
  searching.value = true
  searchError.value = ''
  searched.value = true
  searchResults.value = []
  try {
    const d = await post('/wb/knowledge/search', { project_id: projectId.value, query: query.value.trim(), top_k: topK.value })
    searchResults.value = d.results
  } catch (e) {
    searchError.value = e.message
  } finally {
    searching.value = false
  }
}

async function copyResult(r) {
  const text = r.content || ''
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    // 非安全上下文等场景的兜底方案
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      ElMessage.success('已复制到剪贴板')
    } catch {
      ElMessage.error('复制失败，请手动选择复制')
    }
    ta.remove()
  }
}

// ---- 预览 / 下载 ----
const previewVisible = ref(false)
const previewRow = ref(null)
const previewLoading = ref(false)
const previewError = ref('')
const previewKind = ref('')
const previewBox = ref(null)
const sheetList = ref([])
const activeSheet = ref(0)

function previewKindOf(name) {
  const ext = String(name || '').split('.').pop().toLowerCase()
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx') return 'docx'
  if (ext === 'xlsx') return 'xlsx'
  if (ext === 'pptx') return 'pptx'
  if (ext === 'md' || ext === 'txt') return 'text'
  return ''
}

function fileUrl(row) {
  // 客户端壳进程直接下载时必须是绝对地址（相对路径 urllib 无法打开）
  return `${window.location.origin}/wb/knowledge/files/${row.id}/download?project_id=${projectId.value}`
}

async function fetchFileBuffer(row) {
  const res = await fetch(fileUrl(row))
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.arrayBuffer()
}

async function previewFile(row) {
  const kind = previewKindOf(row.filename)
  if (!kind) {
    ElMessage.warning('该文件类型暂不支持预览，可下载后本地查看')
    return
  }
  previewRow.value = row
  previewKind.value = kind
  previewError.value = ''
  previewLoading.value = true
  previewVisible.value = true
  await nextTick()
  try {
    const buf = await fetchFileBuffer(row)
    if (kind === 'pdf') await renderPdf(buf)
    else if (kind === 'docx') await renderDocx(buf)
    else if (kind === 'xlsx') await renderXlsx(buf)
    else if (kind === 'pptx') await renderPptx(buf)
    else renderText(buf)
  } catch (e) {
    console.error('预览失败', e)
    previewError.value = '预览失败：' + (e?.message || e)
  } finally {
    previewLoading.value = false
  }
}

async function renderPdf(buf) {
  const box = previewBox.value
  box.innerHTML = ''
  box.style.display = 'block'
  const pdfjs = await import('pdfjs-dist')
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    pdfjs.GlobalWorkerOptions.workerSrc = (await import('pdfjs-dist/build/pdf.worker.min.mjs?url')).default
  }
  const doc = await pdfjs.getDocument({ data: buf }).promise
  const width = Math.max(320, box.clientWidth - 24)
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i)
    const base = page.getViewport({ scale: 1 })
    const scale = Math.min(2, width / base.width)
    const vp = page.getViewport({ scale })
    const canvas = document.createElement('canvas')
    canvas.width = Math.floor(vp.width)
    canvas.height = Math.floor(vp.height)
    canvas.className = 'pdf-page'
    box.appendChild(canvas)
    await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise
  }
}

async function renderDocx(buf) {
  const box = previewBox.value
  box.innerHTML = ''
  const { renderAsync } = await import('docx-preview')
  await renderAsync(buf, box, null, {
    inWrapper: true,
    ignoreLastRenderedPageBreak: true,
    experimental: true,
  })
}

async function renderXlsx(buf) {
  const box = previewBox.value
  const XLSX = await import('xlsx')
  const wb = XLSX.read(buf, { type: 'array' })
  sheetList.value = wb.SheetNames.map(name => ({
    name,
    html: XLSX.utils.sheet_to_html(wb.Sheets[name]),
  }))
  activeSheet.value = 0
  await nextTick()
  box.innerHTML = sheetList.value[0]?.html || '<p>空表格</p>'
}

function switchSheet(i) {
  activeSheet.value = i
  previewBox.value.innerHTML = sheetList.value[i]?.html || ''
}

async function renderPptx(buf) {
  const box = previewBox.value
  box.innerHTML = ''
  const { init } = await import('pptx-preview')
  const previewer = init(box, {
    width: Math.max(320, box.clientWidth - 16),
    height: Math.max(240, box.clientHeight - 16),
  })
  await previewer.preview(buf)
}

function renderText(buf) {
  const box = previewBox.value
  box.innerHTML = ''
  const pre = document.createElement('pre')
  pre.className = 'text-view'
  pre.textContent = new TextDecoder('utf-8').decode(buf)
  box.appendChild(pre)
}

async function downloadFile(row) {
  const url = fileUrl(row)
  if (window.pywebview?.api?.download_file) {
    // 客户端模式：壳进程直接写入系统默认下载目录（无弹窗）
    try {
      const saved = await window.pywebview.api.download_file(url, row.filename)
      ElMessage.success('已保存到下载目录：' + saved)
    } catch (e) {
      ElMessage.error('下载失败：' + (e?.message || e))
    }
    return
  }
  try {
    // 浏览器模式：blob 锚点下载
    const res = await fetch(url)
    if (!res.ok) throw new Error('HTTP ' + res.status)
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = row.filename
    a.click()
    URL.revokeObjectURL(a.href)
    ElMessage.success('已开始下载')
  } catch (e) {
    ElMessage.error('下载失败：' + e.message)
  }
}

function fmtSize(n) {
  if (!n) return '—'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

function fmtTime(t) {
  if (!t) return '—'
  return String(t).replace('T', ' ').slice(0, 19)
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.op-line { display: flex; white-space: nowrap; }
.sec-title { font-weight: 600; margin-bottom: 10px; font-size: 14px; }
.search-line { display: flex; gap: 10px; }
.search-line .el-input { flex: 1; }
.search-result { border: 1px solid var(--wb-border, #e5e7eb); border-radius: 8px; padding: 10px 14px; margin-top: 10px; background: #f8fafc; }
.sr-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--wb-text-sub, #64748b); margin-bottom: 6px; }
.sr-copy { color: var(--wb-text-sub, #64748b); }
.sr-copy:hover { color: var(--el-color-primary); }
.sr-source { font-weight: 600; }
.sr-content { font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; max-height: 180px; overflow-y: auto; }
</style>

<!-- 预览弹窗样式：el-dialog teleport 到 body，需全局样式块 -->
<style>
.kb-preview-dialog .el-dialog__header {
  border-bottom: 1px solid var(--wb-border, #e5e7eb);
  margin-right: 0;
  padding: 14px 20px;
}
.kb-preview-dialog .el-dialog__title {
  font-size: 15px;
  font-weight: 600;
  max-width: calc(100% - 60px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
}
.kb-preview-dialog .el-dialog__body { padding: 0; height: calc(100vh - 55px); overflow: hidden; }
.kb-preview-dialog .preview-wrap { position: relative; height: 100%; display: flex; flex-direction: column; background: #f1f3f6; }
.kb-preview-dialog .preview-inner { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.kb-preview-dialog .preview-tip {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  gap: 4px; font-size: 14px; color: var(--wb-text-sub, #64748b);
}
.kb-preview-dialog .sheet-tabs {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 8px 16px;
  background: #fff;
  border-bottom: 1px solid var(--wb-border, #e5e7eb);
}
.kb-preview-dialog .sheet-tag { cursor: pointer; }
.kb-preview-dialog .preview-box {
  flex: 1; overflow: auto; padding: 16px;
  box-sizing: border-box;
}
.kb-preview-dialog .preview-box .pdf-page {
  display: block; margin: 0 auto 12px;
  background: #fff;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
}
.kb-preview-dialog .preview-box .text-view {
  margin: 0 auto; max-width: 860px;
  background: #fff; border-radius: 8px; padding: 24px;
  font-size: 13px; line-height: 1.8; white-space: pre-wrap; word-break: break-word;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
}
.kb-preview-dialog .preview-box table { border-collapse: collapse; background: #fff; }
.kb-preview-dialog .preview-box table td {
  border: 1px solid #d9dee5; padding: 3px 8px;
  font-size: 12px; color: #333;
  white-space: nowrap; max-width: 260px;
  overflow: hidden; text-overflow: ellipsis;
}
</style>
