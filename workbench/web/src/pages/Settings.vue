<template>
  <div class="page">
    <div class="page-title">设置</div>

    <div class="card mb16">
      <div class="sync-head">
        <div class="sec-title">云同步（账户登录 · 多电脑共享数据）</div>
        <el-tag size="small" effect="plain"
                :type="syncStatus.logged_in ? (syncStatus.online ? 'success' : (syncStatus.syncing ? 'primary' : 'warning')) : 'info'">
          {{ syncStatus.logged_in
            ? (syncStatus.online ? '已连接' : (syncStatus.syncing ? '同步中…' : '离线（断网可继续用）'))
            : '未登录' }}
        </el-tag>
      </div>

      <!-- 未登录：账户密码登录表单 -->
      <template v-if="!syncStatus.logged_in">
        <div class="muted mb12">
          登录账户后自动与云端双向同步：本机数据与云上数据保持一致，断网时产生的数据存本地、
          恢复网络后自动补推。登录前产生的本地临时数据可选择归纳到账户，或保留在本地（登录期间隐藏）。
        </div>
        <el-form label-width="110px" size="small" style="max-width: 640px">
          <el-form-item label="服务器地址">
            <el-input v-model="loginForm.cloud_url" placeholder="https://wb.ka1shen.cn" />
          </el-form-item>
          <el-form-item label="用户名">
            <el-input v-model="loginForm.username" placeholder="账户名" @keyup.enter="doLogin" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="loginForm.password" type="password" show-password
                      placeholder="账户密码" @keyup.enter="doLogin" />
          </el-form-item>
          <el-form-item label="本机名称">
            <el-input v-model="loginForm.client_name" placeholder="如：办公室电脑" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loginSaving" @click="doLogin">登 录</el-button>
          </el-form-item>
        </el-form>
        <div v-if="tempInfo.has_backup" class="sync-info">
          <span>本地存在已隐藏的临时数据（退出登录状态下自动恢复展示）</span>
        </div>
      </template>

      <!-- 已登录：账户信息 + 手动同步 -->
      <template v-else>
        <div class="muted mb12">
          已登录账户 <b>{{ syncStatus.account }}</b>，本机数据与云端自动双向同步：
          新增/修改/删除都会同步到云端；离线期间的操作会在恢复网络后自动补推。
        </div>
        <el-form label-width="110px" size="small" style="max-width: 640px">
          <el-form-item label="服务器地址">
            <el-input :model-value="syncStatus.cloud_url" disabled />
          </el-form-item>
          <el-form-item label="本机名称">
            <el-input :model-value="syncStatus.client_name" disabled />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="syncStatus.syncing" @click="syncNow">立即同步</el-button>
            <el-button type="danger" plain :loading="logoutSaving" @click="doLogout">退出登录</el-button>
          </el-form-item>
        </el-form>
      </template>

      <div v-if="syncStatus.logged_in" class="sync-info">
        <span>本机标识：{{ syncStatus.client_name || syncStatus.client_id }}</span>
        <span>待推送：{{ syncStatus.pending_push }} 条</span>
        <span v-if="syncStatus.last_sync_at">最后同步：{{ syncStatus.last_sync_at }}</span>
        <span v-if="syncStatus.last_error" class="sync-err">最近错误：{{ syncStatus.last_error }}</span>
      </div>
    </div>

    <div class="card mb16">
      <div class="sync-head">
        <div class="sec-title">AI 模型（本地大模型 / DeepSeek / GLM / Qwen）</div>
        <el-button type="primary" size="small" @click="openProviderDialog">新增模型提供商</el-button>
      </div>
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
              <el-button size="small" link type="danger" @click="removeSetting(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <!-- 已配置的模型提供商 -->
      <template v-if="providers.length">
        <div class="provider-sub-title">模型提供商（{{ providers.length }}）</div>
        <el-table :data="providers" size="small">
          <el-table-column label="提供商" min-width="180">
            <template #default="{ row }">
              <div>{{ row.display_name }}</div>
              <div class="muted" style="font-size:12px">{{ row.provider_id }} · {{ row.kind === 'builtin' ? '内置' : '自定义' }}</div>
            </template>
          </el-table-column>
          <el-table-column label="协议" width="190">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.protocol_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="base_url" label="API 地址" min-width="220" show-overflow-tooltip />
          <el-table-column label="模型目录" min-width="200">
            <template #default="{ row }">
              <el-tag v-for="m in row.models.slice(0, 3)" :key="modelId(m)" size="small" style="margin-right:4px" effect="plain">{{ modelId(m) }}</el-tag>
              <span v-if="row.models.length > 3" class="muted">+{{ row.models.length - 3 }}</span>
              <span v-if="!row.models.length" class="muted">未配置（目录外 ID 可直接发送）</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160">
            <template #default="{ row }">
              <el-button size="small" link type="primary" :loading="providerTesting === row.id"
                         @click="testProvider(row)">测试连通</el-button>
              <el-button size="small" link type="danger" @click="removeProvider(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>

    <!-- 新增模型提供商弹窗 -->
    <el-dialog v-model="providerDialog" width="680px" destroy-on-close class="provider-dialog">
      <template #header>
        <div class="pd-title">新增模型提供商</div>
      </template>

      <!-- 分段式胶囊 Tab -->
      <div class="seg-tabs">
        <button :class="['seg-btn', providerTab === 'builtin' ? 'active' : '']" type="button"
                @click="providerTab = 'builtin'">第三方模型提供商</button>
        <button :class="['seg-btn', providerTab === 'custom' ? 'active' : '']" type="button"
                @click="providerTab = 'custom'">自定义模型 API</button>
      </div>

      <!-- Tab 1：第三方模型提供商 -->
      <template v-if="providerTab === 'builtin'">
        <div class="pd-desc">
          从内置目录中选择 OpenAI、Anthropic、Kimi 等提供商，填入其 API 密钥即可使用。
        </div>
        <el-form label-position="top" size="small" class="pd-form">
          <el-form-item label="提供商">
            <el-select v-model="builtinForm.catalog_slug" style="width: 360px" @change="onCatalogChange">
              <el-option v-for="c in catalog" :key="c.slug" :value="c.slug" :label="c.name" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <template #label>
              <span>API 密钥</span>
              <el-tooltip placement="top" content="密钥加密存储，同步到其他设备后仍可解密使用">
                <span class="q-mark">?</span>
              </el-tooltip>
            </template>
            <el-input v-model="builtinForm.api_key" type="password" show-password
                      :placeholder="curCatalog.env_key ? `留空使用环境认证（${curCatalog.env_key}）` : '输入 API 密钥'" />
            <div v-if="curCatalog.env_key && !curCatalog.env_key_detected" class="env-hint">
              未检测到环境变量。请填写 API 密钥，或设置环境变量 {{ curCatalog.env_key }}。
            </div>
            <div v-else-if="curCatalog.env_key && curCatalog.env_key_detected" class="env-hint ok">
              已检测到环境变量 {{ curCatalog.env_key }}，可留空。
            </div>
          </el-form-item>

          <el-collapse v-model="builtinAdvanced" class="adv-collapse">
            <el-collapse-item title="自定义设置" name="adv">
              <el-form-item label="API 地址">
                <el-input v-model="builtinForm.base_url" :placeholder="curCatalog.base_url || '由提供方配置地址'" />
              </el-form-item>
              <div v-if="curCatalog.addr_hint" class="pd-field-hint">
                提供方地址配置：{{ curCatalog.addr_hint }}
              </div>
              <div class="adv-divider"></div>
              <model-catalog-editor v-model="builtinForm.models"
                                    :base-url="builtinForm.base_url || curCatalog.base_url"
                                    :protocol="curCatalog.protocol" :api-key="builtinForm.api_key"
                                    :default-models="curCatalog.models || []" />
            </el-collapse-item>
          </el-collapse>
        </el-form>
      </template>

      <!-- Tab 2：自定义模型 API -->
      <template v-else>
        <div class="pd-desc">
          连接中转站、自部署服务或其他兼容 OpenAI / Anthropic 协议的接口，需填写 API 地址、协议和模型。
        </div>
        <el-form label-position="top" size="small" class="pd-form">
          <el-form-item label="Provider ID">
            <el-input v-model="customForm.provider_id" placeholder="acme-gateway" />
            <div class="pd-field-hint">以小写字母开头的标识，在请求中唯一标识该提供商，并用于派生凭据名。</div>
          </el-form-item>
          <el-form-item label="显示名称">
            <el-input v-model="customForm.display_name" placeholder="显示名称" />
          </el-form-item>
          <el-form-item label="API 地址">
            <el-input v-model="customForm.base_url" placeholder="https://gateway.example/v1" />
          </el-form-item>
          <el-form-item label="API 协议">
            <el-select v-model="customForm.protocol" style="width: 360px">
              <el-option value="openai_chat" label="OpenAI Chat Completions" />
              <el-option value="openai_responses" label="OpenAI Responses" />
              <el-option value="anthropic" label="Anthropic Messages" />
            </el-select>
          </el-form-item>
          <el-form-item label="API 密钥">
            <el-input v-model="customForm.api_key" type="password" show-password placeholder="输入 API 密钥" />
          </el-form-item>

          <el-collapse v-model="customAdvanced" class="adv-collapse">
            <el-collapse-item title="自定义设置" name="adv">
              <model-catalog-editor v-model="customForm.models" :base-url="customForm.base_url"
                                    :protocol="customForm.protocol" :api-key="customForm.api_key" />
            </el-collapse-item>
          </el-collapse>
        </el-form>
      </template>

      <template #footer>
        <div class="pd-footer">
          <el-button class="btn-ghost" @click="providerDialog = false">取消</el-button>
          <el-button v-if="providerTab === 'builtin'" class="btn-dark" :loading="providerSaving"
                     @click="saveBuiltinProvider">保存</el-button>
          <el-button v-else class="btn-dark" :loading="providerSaving"
                     @click="saveCustomProvider">创建提供商</el-button>
        </div>
      </template>
    </el-dialog>

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
import { defineComponent, h, onMounted, onUnmounted, ref } from 'vue'
import { ElCheckbox, ElInput, ElIcon, ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowUp, Delete } from '@element-plus/icons-vue'
import { get, post, put, del } from '../api'

// ---------- 模型目录编辑器（卡片式：ID/显示名称/上下文窗口/最大输出/输入类型） ----------
const normEntry = (m) => (typeof m === 'string'
  ? { id: m, name: '', ctx: '', out: '', input: ['text'] }
  : { id: m?.id || '', name: m?.name || '', ctx: m?.ctx || '', out: m?.out || '', input: Array.isArray(m?.input) ? m.input : ['text'] })
const modelId = (m) => (typeof m === 'string' ? m : (m && m.id) || '')

const ModelCatalogEditor = defineComponent({
  name: 'ModelCatalogEditor',
  props: {
    modelValue: { type: Array, default: () => [] },
    baseUrl: { type: String, default: '' },
    protocol: { type: String, default: 'openai_chat' },
    apiKey: { type: String, default: '' },
    defaultModels: { type: Array, default: () => [] },   // 内置目录默认模型（"恢复默认模型"用）
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const probing = ref(false)
    const expanded = ref(-1)   // 当前展开的卡片下标（手风琴式）

    const entries = () => props.modelValue.map(normEntry)
    const setEntry = (i, patch) => {
      const next = entries()
      next[i] = { ...next[i], ...patch }
      emit('update:modelValue', next)
    }
    const removeEntry = (i) => {
      const next = entries()
      next.splice(i, 1)
      if (expanded.value === i) expanded.value = -1
      else if (expanded.value > i) expanded.value -= 1
      emit('update:modelValue', next)
    }
    const toggleInput = (i, kind, on) => {
      const cur = new Set(entries()[i].input)
      if (on) cur.add(kind); else cur.delete(kind)
      setEntry(i, { input: [...cur] })
    }
    const addModel = () => {
      const next = entries()
      next.push({ id: '', name: '', ctx: '', out: '', input: ['text'] })
      expanded.value = next.length - 1
      emit('update:modelValue', next)
    }
    const restoreDefaults = () => {
      expanded.value = -1
      emit('update:modelValue', (props.defaultModels || []).map(normEntry))
    }
    const fetchModels = async () => {
      probing.value = true
      try {
        const r = await post('/wb/ai-providers/probe-models',
          { base_url: props.baseUrl, protocol: props.protocol, api_key: props.apiKey })
        expanded.value = -1
        emit('update:modelValue', (r.models || []).map(id => ({ id, name: '', ctx: '', out: '', input: ['text'] })))
        ElMessage.success(`获取到 ${(r.models || []).length} 个可用模型`)
      } catch (e) {
        ElMessage.error(e.message)
      } finally {
        probing.value = false
      }
    }

    const renderCard = (e, i) => h('div', { class: 'mc-card' }, [
      h('div', { class: 'mc-card-row' }, [
        h(ElInput, { modelValue: e.id, placeholder: '模型 ID', size: 'default',
                     'onUpdate:modelValue': v => setEntry(i, { id: v }), class: 'mc-inp-id' }),
        h(ElInput, { modelValue: e.name, placeholder: '显示名称', size: 'default',
                     'onUpdate:modelValue': v => setEntry(i, { name: v }), class: 'mc-inp-name' }),
        h('button', { class: 'mc-icon-btn push', type: 'button', title: expanded.value === i ? '收起' : '展开',
                      onClick: () => { expanded.value = expanded.value === i ? -1 : i } },
          [h(ElIcon, null, () => h(expanded.value === i ? ArrowUp : ArrowDown))]),
        h('button', { class: 'mc-icon-btn danger', type: 'button', title: '删除',
                      onClick: () => removeEntry(i) },
          [h(ElIcon, null, () => h(Delete))]),
      ]),
      expanded.value === i ? h('div', { class: 'mc-card-more' }, [
        h('div', { class: 'mc-grid' }, [
          h('div', { class: 'mc-field' }, [
            h('div', { class: 'mc-field-label' }, '上下文窗口'),
            h(ElInput, { modelValue: e.ctx, placeholder: '256K', size: 'default',
                         'onUpdate:modelValue': v => setEntry(i, { ctx: v }) }),
          ]),
          h('div', { class: 'mc-field' }, [
            h('div', { class: 'mc-field-label' }, '最大输出 token 数'),
            h(ElInput, { modelValue: e.out, placeholder: '32K', size: 'default',
                         'onUpdate:modelValue': v => setEntry(i, { out: v }) }),
          ]),
        ]),
        h('div', { class: 'mc-field mc-field-checks' }, [
          h('div', { class: 'mc-field-label' }, '输入类型'),
          h('div', { class: 'mc-checks' }, [
            h(ElCheckbox, { modelValue: (e.input || []).includes('text'), label: '文本',
                            'onUpdate:modelValue': v => toggleInput(i, 'text', v) }),
            h(ElCheckbox, { modelValue: (e.input || []).includes('image'), label: '图片',
                            'onUpdate:modelValue': v => toggleInput(i, 'image', v) }),
          ]),
        ]),
      ]) : null,
    ])

    return () => {
      const list = entries()
      const badIdx = list.findIndex(e => !String(e.id || '').trim())
      return h('div', { class: 'model-catalog' }, [
        h('div', { class: 'mc-head' }, [
        h('div', [h('div', { class: 'mc-title' }, '模型目录'),
          h('div', { class: 'mc-title-sub' },
            list.length ? '已自定义模型目录' : '正在使用适配器默认模型')]),
          h('div', { class: 'mc-actions' }, [
            (props.defaultModels || []).length
              ? h('a', { class: 'mc-fetch', onClick: restoreDefaults }, '恢复默认模型')
              : null,
            h('a', { class: 'mc-fetch', onClick: fetchModels },
              probing.value ? '获取中…' : '获取可用模型'),
          ]),
        ]),
        list.length
          ? h('div', { class: 'mc-cards' }, list.map(renderCard))
          : h('div', { class: 'mc-empty' }, '模型选择器中将不显示任何模型；目录外 ID 仍可直接发送。'),
        h('button', { class: 'mc-add', type: 'button', onClick: addModel }, '+ 添加模型'),
        badIdx >= 0 ? h('div', { class: 'mc-err' }, `模型 ${badIdx + 1}: 模型 ID 不能为空。`) : null,
      ])
    }
  },
})

// ---------- 云同步（账户登录） ----------
const syncStatus = ref({ enabled: false, online: false, logged_in: false, account: '', pending_push: 0 })
const loginForm = ref({ cloud_url: 'https://wb.ka1shen.cn', username: '', password: '', client_name: '' })
const loginSaving = ref(false)
const logoutSaving = ref(false)
const tempInfo = ref({ rows: 0, has_backup: false })
let syncTimer = null

onMounted(() => {
  load()
  loadSync()
  loadProviders()
  syncTimer = setInterval(loadSync, 5000)
})
onUnmounted(() => clearInterval(syncTimer))

async function loadSync() {
  try {
    syncStatus.value = await get('/wb/sync/status')
    if (!loginForm.value.cloud_url && syncStatus.value.cloud_url) {
      loginForm.value.cloud_url = syncStatus.value.cloud_url
    }
    if (!loginForm.value.client_name && syncStatus.value.client_name) {
      loginForm.value.client_name = syncStatus.value.client_name
    }
  } catch (e) { /* 云同步端点不可用（旧版本后端）不影响设置页 */ }
}

async function doLogin() {
  if (!loginForm.value.cloud_url || !loginForm.value.username || !loginForm.value.password) {
    ElMessage.warning('请填写服务器地址、用户名与密码')
    return
  }
  // 登录前检查本地临时数据，询问是否归纳到账户
  let merge = true
  try {
    tempInfo.value = await get('/wb/sync/temp-info')
  } catch (e) { /* 旧后端无此端点时默认归纳 */ }
  if (tempInfo.value.rows > 0) {
    try {
      await ElMessageBox.confirm(
        `检测到本地有 ${tempInfo.value.rows} 条未登录期间产生的临时数据。是否将其归纳到登录账号下？` +
        '（归纳后会同步到云端；不归纳则登录期间隐藏本地数据，仅展示云端数据，退出登录后恢复展示）',
        '本地临时数据处理', {
          distinguishCancelAndClose: true,
          confirmButtonText: '归纳到账号',
          cancelButtonText: '不归纳（隐藏本地数据）',
          type: 'info',
        })
      merge = true
    } catch (action) {
      if (action === 'cancel') merge = false
      else return // 关闭 = 取消登录
    }
  }
  loginSaving.value = true
  try {
    const r = await post('/wb/sync/login', { ...loginForm.value, merge_local: merge })
    ElMessage.success(`已登录 ${r.username}（云端数据同步中）`)
    loginForm.value.password = ''
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loginSaving.value = false
    loadSync()
  }
}

async function doLogout() {
  try {
    await ElMessageBox.confirm(
      '退出登录后将隐藏该账户的云端数据（数据保留在云端，重新登录即恢复）；' +
      '若本地存在未登录期间产生的临时数据会恢复展示。确定退出？',
      '退出登录', { type: 'warning' })
  } catch (e) { return }
  logoutSaving.value = true
  try {
    await post('/wb/sync/logout')
    ElMessage.success('已退出登录')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    logoutSaving.value = false
    loadSync()
  }
}

async function syncNow() {
  await post('/wb/sync/now')
  ElMessage.info('已触发同步')
}

// ---------- AI 设置（既有固定行） ----------
const aiSettings = ref([])
const editing = ref('')
const editValue = ref('')
const testing = ref('')
const usage = ref(null)

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

async function removeSetting(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除配置「${row.setting_key}」吗？删除后该配置将回落为未配置状态，可重新添加。`,
      '删除配置', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch (e) { return }
  await del(`/wb/settings?setting_key=${encodeURIComponent(row.setting_key)}`)
  ElMessage.success('已删除')
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

// ---------- 模型提供商 ----------
const providers = ref([])
const providerDialog = ref(false)
const providerTab = ref('builtin')
const catalog = ref([])
const builtinAdvanced = ref('adv')
const customAdvanced = ref('adv')
const providerSaving = ref(false)
const providerTesting = ref(0)
const curCatalog = ref({})
const builtinForm = ref({ catalog_slug: 'openai', api_key: '', base_url: '', models: [] })
const customForm = ref({ provider_id: '', display_name: '', base_url: '', protocol: 'openai_chat', api_key: '', models: [] })

async function loadProviders() {
  try {
    providers.value = await get('/wb/ai-providers')
  } catch (e) { /* 旧后端无此端点 */ }
}

async function openProviderDialog() {
  providerDialog.value = true
  providerTab.value = 'builtin'
  if (!catalog.value.length) {
    try {
      catalog.value = await get('/wb/ai-providers/catalog')
      onCatalogChange(builtinForm.value.catalog_slug)
    } catch (e) { /* 旧后端 */ }
  }
}

function onCatalogChange(slug) {
  curCatalog.value = catalog.value.find(c => c.slug === slug) || {}
  builtinForm.value.base_url = curCatalog.value.base_url || ''
  // 切换提供商时不预填默认模型，目录保持为空（可通过"恢复默认模型"一键取回）
  builtinForm.value.models = []
}

async function saveBuiltinProvider() {
  const c = curCatalog.value
  if (!c.slug) { ElMessage.warning('请选择提供商'); return }
  if (builtinForm.value.models.some(m => !modelId(m).trim())) {
    ElMessage.error('模型目录中存在未填写模型 ID 的条目，请补全或删除'); return
  }
  providerSaving.value = true
  try {
    await post('/wb/ai-providers', {
      provider_id: c.slug,
      display_name: c.name,
      kind: 'builtin',
      catalog_slug: c.slug,
      protocol: c.protocol,
      base_url: builtinForm.value.base_url || c.base_url || '',
      api_key: builtinForm.value.api_key || '',
      models: builtinForm.value.models,
    })
    ElMessage.success(`已添加提供商：${c.name}`)
    providerDialog.value = false
    loadProviders()
    load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    providerSaving.value = false
  }
}

async function saveCustomProvider() {
  const f = customForm.value
  if (!f.provider_id || !f.base_url) {
    ElMessage.warning('请填写 Provider ID 与 API 地址')
    return
  }
  if (f.models.some(m => !modelId(m).trim())) {
    ElMessage.error('模型目录中存在未填写模型 ID 的条目，请补全或删除'); return
  }
  providerSaving.value = true
  try {
    await post('/wb/ai-providers', {
      provider_id: f.provider_id.trim(),
      display_name: f.display_name || f.provider_id,
      kind: 'custom',
      protocol: f.protocol,
      base_url: f.base_url.trim(),
      api_key: f.api_key || '',
      models: f.models,
    })
    ElMessage.success('已创建提供商')
    providerDialog.value = false
    loadProviders()
    load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    providerSaving.value = false
  }
}

async function testProvider(row) {
  providerTesting.value = row.id
  try {
    const r = await post(`/wb/ai-providers/${row.id}/test`)
    ElMessage.success(`连通成功：${r.model}（${r.latency_ms}ms）`)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    providerTesting.value = 0
  }
}

async function removeProvider(row) {
  try {
    await ElMessageBox.confirm(`删除提供商「${row.display_name}」？该操作会同步到所有登录设备。`,
      '删除提供商', { type: 'warning' })
  } catch (e) { return }
  try {
    await del(`/wb/ai-providers/${row.id}`)
    ElMessage.success('已删除')
    loadProviders()
  } catch (e) {
    ElMessage.error(e.message)
  }
}
</script>

<style scoped>
.sec-title { font-weight: 600; margin-bottom: 10px; font-size: 14px; }
.sync-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
.sync-head .sec-title { margin-bottom: 0; }
.sync-info { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #6b7280; padding: 8px 12px; background: #f8fafc; border-radius: 8px; }
.sync-err { color: #dc2626; }
.provider-sub-title { font-weight: 600; font-size: 13px; margin: 16px 0 8px; color: #374151; }
.env-hint { font-size: 12px; color: #d97706; margin-top: 4px; }
.env-hint.ok { color: #16a34a; }
.q-mark { display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px; margin-left: 4px; border-radius: 50%; border: 1px solid #c0c4cc; color: #909399; font-size: 10px; cursor: help; }
.adv-collapse { border: none; margin-top: 2px; }
.adv-collapse :deep(.el-collapse-item__header) {
  font-weight: 600; font-size: 13px; color: #374151;
  border-bottom: none; height: 34px; line-height: 34px;
}
.adv-collapse :deep(.el-collapse-item__header .el-collapse-item__arrow) { color: #6b7280; }
.adv-collapse :deep(.el-collapse-item__wrap) { border-bottom: none; background: transparent; }
.adv-collapse :deep(.el-collapse-item__content) { padding: 4px 0 10px; }
.adv-divider { height: 1px; background: #eef1f6; margin: 4px 0 16px; }

.pd-title { font-size: 16px; font-weight: 600; color: #111827; }
.pd-desc { font-size: 13px; color: #6b7280; margin: 10px 0 16px; line-height: 1.6; }
.pd-field-hint { font-size: 12px; color: #9ca3af; margin-top: 4px; line-height: 1.5; }

/* 分段式胶囊 Tab */
.seg-tabs {
  display: inline-flex; padding: 4px; border-radius: 10px;
  background: #eef0f4; gap: 2px;
}
.seg-btn {
  border: none; background: transparent; padding: 8px 20px; border-radius: 8px;
  font-size: 13px; color: #4b5563; cursor: pointer; transition: all .15s ease;
  font-family: inherit;
}
.seg-btn:hover { color: #111827; }
.seg-btn.active {
  background: #fff; color: #111827; font-weight: 500;
  box-shadow: 0 1px 3px rgba(17, 24, 39, 0.1), 0 0 1px rgba(17, 24, 39, 0.08);
}

/* 弹窗表单细节 */
.pd-form :deep(.el-form-item__label) { font-weight: 600; color: #111827; font-size: 13px; padding-bottom: 6px; }
.pd-form :deep(.el-input__wrapper), .pd-form :deep(.el-select__wrapper) { border-radius: 8px; box-shadow: 0 0 0 1px #e2e6ee inset; }
.pd-form :deep(.el-input__wrapper:hover), .pd-form :deep(.el-select__wrapper:hover) { box-shadow: 0 0 0 1px #c6ccd8 inset; }

/* 页脚按钮 */
.pd-footer { display: flex; justify-content: flex-end; gap: 10px; }
.btn-ghost {
  background: #fff; border: 1px solid #d7dce5; border-radius: 8px;
  color: #374151; font-weight: 500;
}
.btn-ghost:hover { border-color: #b9c0cc; color: #111827; background: #f9fafb; }
.btn-dark {
  background: #1f2329; border: 1px solid #1f2329; border-radius: 8px;
  color: #fff; font-weight: 500;
}
.btn-dark:hover, .btn-dark:focus { background: #33383f; border-color: #33383f; color: #fff; }
.usage-cards { display: flex; gap: 12px; flex-wrap: wrap; }
.u-card {
  flex: 1; min-width: 120px; padding: 14px 16px; border-radius: 10px;
  background: #f8fafc; border: 1px solid #eef1f6; text-align: center;
}
.u-num { font-size: 22px; font-weight: 700; color: var(--wb-accent, #2563eb); }
.u-label { font-size: 12px; color: #6b7280; margin-top: 4px; }
</style>

<style>
/* 全局样式：新增模型提供商弹窗。
   el-dialog 会被传送到 body，.el-dialog 元素上没有 scoped 作用域属性，
   scoped + :deep 选择器全部失配，因此弹窗骨架与模型目录样式必须全局定义。 */
.el-dialog.provider-dialog { border-radius: 14px; overflow: hidden; }
.provider-dialog .el-dialog__header { padding: 18px 24px 6px; margin-right: 0; }
.provider-dialog .el-dialog__body { padding: 6px 24px 4px; }
.provider-dialog .el-dialog__footer { padding: 8px 24px 20px; }

/* ---------- 模型目录编辑器 ---------- */
.provider-dialog .model-catalog { margin-top: 4px; }
.provider-dialog .mc-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.provider-dialog .mc-title { font-weight: 600; font-size: 13px; color: #111827; }
.provider-dialog .mc-title-sub { font-size: 12px; color: #9ca3af; margin-top: 2px; }
.provider-dialog .mc-actions { display: flex; align-items: center; gap: 18px; flex-shrink: 0; margin-left: 16px; }
.provider-dialog .mc-fetch { color: #9ca3af; font-size: 13px; cursor: pointer; }
.provider-dialog .mc-fetch:hover { color: var(--wb-accent, #2563eb); }
.provider-dialog .mc-cards { display: flex; flex-direction: column; gap: 10px; margin-bottom: 2px; }
.provider-dialog .mc-card { border: 1px solid #e5e9f0; border-radius: 10px; padding: 12px 14px; background: #fff; }
.provider-dialog .mc-card-row { display: flex; align-items: center; gap: 12px; }
.provider-dialog .mc-inp-id { flex: 1.2; }
.provider-dialog .mc-inp-name { flex: 1; }
.provider-dialog .mc-icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: none; background: transparent; border-radius: 6px;
  color: #6b7280; cursor: pointer; flex-shrink: 0; transition: all .15s ease;
  padding: 0; outline: none;
}
.provider-dialog .mc-icon-btn.push { margin-left: auto; }
.provider-dialog .mc-icon-btn:hover { background: #f3f4f6; color: #111827; }
.provider-dialog .mc-icon-btn.danger:hover { background: #fef2f2; color: #dc2626; }
.provider-dialog .mc-card-more { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.provider-dialog .mc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.provider-dialog .mc-field-label { font-size: 12px; color: #6b7280; margin-bottom: 5px; }
.provider-dialog .mc-checks { display: flex; align-items: center; gap: 6px; }
.provider-dialog .mc-empty {
  border: 1px dashed #d1d5db; border-radius: 10px; padding: 20px;
  text-align: center; color: #9ca3af; font-size: 12px; margin-bottom: 4px;
  background: transparent;
}
.provider-dialog .mc-add {
  margin-top: 14px; padding: 7px 14px;
  background: transparent; border: 1px solid transparent; border-radius: 8px;
  font-size: 13px; color: #6b7280; cursor: pointer; font-family: inherit;
  transition: all .15s ease;
}
.provider-dialog .mc-add:hover { background: #f3f4f6; color: #111827; }
.provider-dialog .mc-err { color: #d97706; font-size: 12px; margin-top: 8px; }
</style>
