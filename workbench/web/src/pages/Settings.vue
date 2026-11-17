<template>
  <div class="page">
    <div class="page-title">设置</div>

    <div class="card mb16">
      <div class="sync-head">
        <div class="sec-title">云同步（多电脑共享数据）</div>
        <el-tag size="small" effect="plain" :type="syncStatus.online ? 'success' : (syncStatus.enabled ? 'danger' : 'info')">
          {{ syncStatus.enabled ? (syncStatus.online ? '已连接' : (syncStatus.syncing ? '同步中…' : '离线（断网可继续用）')) : '未启用' }}
        </el-tag>
      </div>
      <div class="muted mb12">
        启用后本机数据与云服务器双向同步：断网时产生的数据存本地，恢复网络后自动补推；
        其他电脑录入的数据自动拉取。首次启用会将本机存量数据平移迁移（一次性，约几秒）。
      </div>
      <el-form label-width="110px" size="small" style="max-width: 640px">
        <el-form-item label="服务器地址">
          <el-input v-model="syncForm.cloud_url" placeholder="https://wb.ka1shen.cn"
                    :disabled="syncStatus.enabled" />
        </el-form-item>
        <el-form-item label="访问令牌">
          <el-input v-model="syncForm.token" type="password" show-password
                    placeholder="服务器 WB_SYNC_TOKEN" :disabled="syncStatus.enabled" />
        </el-form-item>
        <el-form-item label="本机名称">
          <el-input v-model="syncForm.client_name" placeholder="如：办公室电脑"
                    :disabled="syncStatus.enabled" />
        </el-form-item>
        <el-form-item>
          <template v-if="!syncStatus.enabled">
            <el-button type="primary" :loading="syncSaving" @click="enableSync">启用同步</el-button>
          </template>
          <template v-else>
            <el-button :loading="syncStatus.syncing" @click="syncNow">立即同步</el-button>
            <el-button type="danger" plain @click="disableSync">停用同步</el-button>
          </template>
        </el-form-item>
      </el-form>
      <div v-if="syncStatus.enabled" class="sync-info">
        <span>本机标识：{{ syncStatus.client_name || syncStatus.client_id }}</span>
        <span>待推送：{{ syncStatus.pending_push }} 条</span>
        <span v-if="syncStatus.last_sync_at">最后同步：{{ syncStatus.last_sync_at }}</span>
        <span v-if="syncStatus.last_error" class="sync-err">最近错误：{{ syncStatus.last_error }}</span>
      </div>
    </div>

    <div class="card mb16">
      <div class="sec-title">AI 模型（本地大模型 / DeepSeek / GLM / Qwen）</div>
      <div class="muted mb12">
        本地大模型：填 OpenAI 兼容端点即可离线使用（Ollama 默认 http://127.0.0.1:11434/v1，LM Studio 默认 http://127.0.0.1:1234/v1）；
        云厂商 key 加密存储（界面打码）。配置后点「测试连通」验证。
      </div>
      <el-table :data="aiSettings" size="small">
        <el-table-column label="配置项" width="260">
          <template #default="{ row }">
            <div>{{ row.setting_key }}</div>
            <div class="muted" style="font-size:12px">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="值" min-width="200">
          <template #default="{ row }">
            <el-input v-if="editing === row.setting_key" v-model="editValue" size="small"
                      :placeholder="row.is_secret ? '粘贴 API key' : (row.setting_key === 'ai.local.base_url' ? 'http://127.0.0.1:11434/v1' : '模型名')"
                      :show-password="!!row.is_secret" />
            <span v-else>{{ row.has_value ? (row.is_secret ? '******' : row.setting_value) : '未配置' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="{ row }">
            <template v-if="editing === row.setting_key">
              <el-button size="small" type="primary" @click="saveSetting(row)">保存</el-button>
              <el-button size="small" @click="editing = ''">取消</el-button>
            </template>
            <template v-else>
              <el-button size="small" link type="primary" @click="startEdit(row)">编辑</el-button>
              <el-button size="small" link :loading="testing === row.setting_key"
                         :disabled="row.setting_key === 'ai.local.api_key'"
                         @click="testConn(row)">测试连通</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card mb16">
      <div class="sec-title">AI 调用用量</div>
      <template v-if="usage">
        <div class="usage-cards">
          <div class="u-card"><div class="u-num">{{ usage.total }}</div><div class="u-label">调用总数</div></div>
          <div class="u-card"><div class="u-num">{{ usage.success_rate }}%</div><div class="u-label">成功率</div></div>
          <div class="u-card"><div class="u-num">{{ usage.prompt_tokens }}</div><div class="u-label">输入 Tokens</div></div>
          <div class="u-card"><div class="u-num">{{ usage.completion_tokens }}</div><div class="u-label">输出 Tokens</div></div>
          <div class="u-card"><div class="u-num">{{ usage.avg_ms }}ms</div><div class="u-label">平均耗时</div></div>
        </div>
        <el-table v-if="usage.by_model.length" :data="usage.by_model" size="small" style="margin-top: 12px">
          <el-table-column prop="model_name" label="模型" min-width="180" />
          <el-table-column prop="calls" label="调用次数" width="100" />
          <el-table-column prop="success" label="成功" width="80" />
          <el-table-column prop="prompt_tokens" label="输入 Tokens" width="110" />
          <el-table-column prop="completion_tokens" label="输出 Tokens" width="110" />
          <el-table-column prop="avg_ms" label="平均耗时(ms)" width="120" />
        </el-table>
        <el-table v-if="usage.recent.length" :data="usage.recent" size="small" style="margin-top: 12px">
          <el-table-column prop="created_at" label="时间" width="170" />
          <el-table-column prop="scene" label="场景" width="150" />
          <el-table-column prop="model_name" label="模型" min-width="160" />
          <el-table-column label="结果" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.success ? 'success' : 'danger'" effect="plain">{{ row.success ? '成功' : '失败' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="error_msg" label="错误信息" min-width="180" show-overflow-tooltip />
        </el-table>
        <div v-if="!usage.total" class="muted" style="margin-top: 8px">暂无调用记录：配置模型 key 并测试连通后，这里将展示调用流水。</div>
      </template>
      <div v-else class="muted">加载中…</div>
    </div>

    <div class="card">
      <div class="sec-title">数据库</div>
      <div class="muted">MySQL @ 8.133.254.215:3306 / Workbench · 连接配置在 data/.env（随 exe 分发时排除）。</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post, put } from '../api'

const aiSettings = ref([])
const editing = ref('')
const editValue = ref('')
const testing = ref('')
const usage = ref(null)

// 云同步
const syncStatus = ref({ enabled: false, online: false, pending_push: 0 })
const syncForm = ref({ cloud_url: 'https://wb.ka1shen.cn', token: '', client_name: '' })
const syncSaving = ref(false)
let syncTimer = null

onMounted(() => {
  load()
  loadSync()
  syncTimer = setInterval(loadSync, 5000)
})
onUnmounted(() => clearInterval(syncTimer))

async function loadSync() {
  try {
    syncStatus.value = await get('/wb/sync/status')
    if (!syncForm.value.cloud_url && syncStatus.value.cloud_url) {
      syncForm.value.cloud_url = syncStatus.value.cloud_url
    }
    if (!syncForm.value.client_name && syncStatus.value.client_name) {
      syncForm.value.client_name = syncStatus.value.client_name
    }
  } catch (e) { /* 云同步端点不可用（旧版本后端）不影响设置页 */ }
}

async function enableSync() {
  if (!syncForm.value.cloud_url || !syncForm.value.token) {
    ElMessage.warning('请填写服务器地址与访问令牌')
    return
  }
  syncSaving.value = true
  try {
    const r = await post('/wb/sync/config', { ...syncForm.value, enabled: true })
    ElMessage.success(`已启用（本机区间序号 ${r.ordinal}，存量数据已迁移）`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    syncSaving.value = false
    loadSync()
  }
}

async function disableSync() {
  await ElMessageBox.confirm(
    '停用后本机数据不再与服务器同步（本地数据保留），确定停用？',
    '停用云同步', { type: 'warning' })
  await post('/wb/sync/config', { enabled: false })
  ElMessage.success('已停用')
  loadSync()
}

async function syncNow() {
  await post('/wb/sync/now')
  ElMessage.info('已触发同步')
}

async function load() {
  const items = await get('/wb/settings?group=ai_model')
  // 固定展示行：本地大模型三项 + 三家云厂商 key；未落库的补占位行
  const defs = [
    { k: 'ai.local.base_url', secret: 0, desc: '本地模型服务端点（Ollama/LM Studio/任意 OpenAI 兼容服务）' },
    { k: 'ai.local.model', secret: 0, desc: '模型名（如 qwen2.5:7b，留空默认 qwen2.5:7b）' },
    { k: 'ai.local.api_key', secret: 1, desc: '本地模型通常无需 key，留空即可' },
    { k: 'ai.deepseek.api_key', secret: 1, desc: 'DeepSeek API key' },
    { k: 'ai.glm.api_key', secret: 1, desc: '智谱 GLM API key' },
    { k: 'ai.qwen.api_key', secret: 1, desc: '通义千问 API key' },
  ]
  aiSettings.value = defs.map(({ k, secret, desc }) => {
    const found = items.find(i => i.setting_key === k)
    return found || { setting_key: k, setting_group: 'ai_model', setting_value: '', is_secret: secret, has_value: false, description: desc }
  })
  try {
    usage.value = await get('/wb/ai-usage-stats')
  } catch (e) { /* 统计加载失败不阻塞设置页 */ }
}

function startEdit(row) {
  editing.value = row.setting_key
  editValue.value = row.is_secret ? '' : (row.setting_value || '')
}

async function saveSetting(row) {
  await put('/wb/settings', {
    setting_key: row.setting_key,
    setting_group: 'ai_model',
    setting_value: editValue.value,
    is_secret: row.is_secret,
    description: row.description
  })
  ElMessage.success('已保存（加密存储）')
  editing.value = ''
  load()
}

async function testConn(row) {
  const provider = row.setting_key.split('.')[1]
  testing.value = row.setting_key
  try {
    const r = await post(`/wb/settings/test-model?provider=${provider}`)
    ElMessage.success(`连通成功：${r.model}（${r.latency_ms}ms）`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    testing.value = ''
  }
}
</script>

<style scoped>
.sec-title { font-weight: 600; margin-bottom: 10px; font-size: 14px; }
.sync-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.sync-head .sec-title { margin-bottom: 0; }
.sync-info { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #6b7280; padding: 8px 12px; background: #f8fafc; border-radius: 8px; }
.sync-err { color: #dc2626; }
.usage-cards { display: flex; gap: 12px; flex-wrap: wrap; }
.u-card {
  flex: 1; min-width: 120px; padding: 14px 16px; border-radius: 10px;
  background: #f8fafc; border: 1px solid #eef1f6; text-align: center;
}
.u-num { font-size: 22px; font-weight: 700; color: var(--wb-accent, #2563eb); }
.u-label { font-size: 12px; color: #6b7280; margin-top: 4px; }
</style>
