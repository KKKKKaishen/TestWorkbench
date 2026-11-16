<template>
  <div class="page">
    <div class="page-title">日志管理</div>
    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="选择项目" style="width:220px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-select v-model="logType" placeholder="全部类型" clearable style="width:150px" @change="load">
          <el-option v-for="v in typeList" :key="v[0]" :value="v[0]" :label="v[1]" />
        </el-select>
        <el-button type="primary" :disabled="!projectId" @click="dlg = true; form = { title: '', log_type: 'execution', content: '' }">
          <el-icon><Plus /></el-icon>写日志
        </el-button>
      </div>
      <el-table :data="logs" v-loading="loading" stripe>
        <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ typeList.find(t => t[0] === row.log_type)?.[1] }}</template>
        </el-table-column>
        <el-table-column prop="log_time" label="发生时间" width="170" />
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" link @click="view(row)">查看</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dlg" title="写测试日志" width="560px">
      <el-form label-width="80px">
        <el-form-item label="类型">
          <el-select v-model="form.log_type">
            <el-option v-for="v in typeList" :key="v[0]" :value="v[0]" :label="v[1]" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="form.content" type="textarea" :rows="6" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="viewDlg" :title="viewing?.title" width="620px">
      <div class="muted mb12">{{ viewing?.log_time }} · {{ typeList.find(t => t[0] === viewing?.log_type)?.[1] }}</div>
      <div style="white-space: pre-wrap; font-size: 13.5px;">{{ viewing?.content }}</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, del } from '../api'

const projects = ref([])
const projectId = ref(null)
const logs = ref([])
const loading = ref(false)
const logType = ref('')
const dlg = ref(false)
const viewDlg = ref(false)
const viewing = ref(null)
const form = ref({ title: '', log_type: 'execution', content: '' })
const typeList = [['execution', '执行日志'], ['review', '评审记录'], ['comm', '沟通记录'], ['other', '其他']]

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (projects.value.length) { projectId.value = projects.value[0].id; load() }
})

async function load() {
  if (!projectId.value) return
  loading.value = true
  try {
    const q = new URLSearchParams({ project_id: projectId.value })
    if (logType.value) q.set('log_type', logType.value)
    logs.value = (await get('/wb/logs?' + q)).items
  } finally { loading.value = false }
}

async function save() {
  if (!form.value.title) return ElMessage.warning('请填写标题')
  await post('/wb/logs', { ...form.value, project_id: projectId.value })
  ElMessage.success('已保存')
  dlg.value = false
  load()
}

function view(row) { viewing.value = row; viewDlg.value = true }

async function remove(row) {
  await del(`/wb/logs/${row.id}`)
  load()
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
</style>
