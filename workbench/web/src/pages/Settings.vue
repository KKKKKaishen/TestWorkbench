<template>
  <div class="page">
    <div class="page-title">设置</div>

    <div class="card mb16">
      <div class="sec-title">AI 模型（DeepSeek / GLM / Qwen）</div>
      <div class="muted mb12">三家接口均预留，key 加密存储（界面打码显示）。填好后点「测试连通」验证。</div>
      <el-table :data="aiSettings" size="small">
        <el-table-column prop="setting_key" label="配置项" width="220" />
        <el-table-column label="值" min-width="200">
          <template #default="{ row }">
            <el-input v-if="editing === row.setting_key" v-model="editValue" size="small"
                      :placeholder="row.is_secret ? '粘贴 API key' : ''" show-password />
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
              <el-button v-if="row.has_value" size="small" link :loading="testing === row.setting_key"
                         @click="testConn(row)">测试连通</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card mb16">
      <div class="sec-title">AI 调用用量</div>
      <div class="muted">连接测试与 AI 生成的调用流水（场景 / 模型 / 耗时 / 成功与否）将在此汇总展示（P1 后续版本完善图表）。</div>
    </div>

    <div class="card">
      <div class="sec-title">数据库</div>
      <div class="muted">MySQL @ 8.133.254.215:3306 / Workbench · 连接配置在 data/.env（随 exe 分发时排除）。</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, put } from '../api'

const aiSettings = ref([])
const editing = ref('')
const editValue = ref('')
const testing = ref('')

onMounted(load)

async function load() {
  const items = await get('/wb/settings?group=ai_model')
  // 三家固定展示，未落库的补占位行
  const keys = ['ai.deepseek.api_key', 'ai.glm.api_key', 'ai.qwen.api_key']
  aiSettings.value = keys.map(k => {
    const found = items.find(i => i.setting_key === k)
    const provider = k.split('.')[1]
    return found || { setting_key: k, setting_group: 'ai_model', setting_value: '', is_secret: 1, has_value: false, description: `${provider} API key` }
  })
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
</style>
