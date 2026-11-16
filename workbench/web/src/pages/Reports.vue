<template>
  <div class="page">
    <div class="page-title">测试报告</div>
    <div class="card">
      <div class="toolbar">
        <el-select v-model="projectId" placeholder="选择项目" style="width:220px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <el-button type="primary" :disabled="!projectId" @click="generate">一键生成报告</el-button>
        <span class="muted">数据全部来自工作台真实执行记录；AI 摘要在配置模型 key 后自动增强</span>
      </div>
      <el-table :data="reports" v-loading="loading" stripe>
        <el-table-column prop="title" label="报告" min-width="220" show-overflow-tooltip />
        <el-table-column prop="report_type" label="类型" width="110">
          <template #default="{ row }">{{ { iteration: '迭代报告', regression: '回归报告', summary: '项目汇总' }[row.report_type] }}</template>
        </el-table-column>
        <el-table-column label="执行进度" width="110">
          <template #default="{ row }">{{ row.stats?.executed ?? '—' }} 条</template>
        </el-table-column>
        <el-table-column label="通过率" width="90">
          <template #default="{ row }">{{ row.stats?.pass_rate ?? '—' }}%</template>
        </el-table-column>
        <el-table-column prop="created_at" label="生成时间" width="170" />
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="view(row)">查看</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="viewDlg" :title="viewing?.title" width="680px">
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
const reports = ref([])
const loading = ref(false)
const viewDlg = ref(false)
const viewing = ref(null)

onMounted(async () => {
  projects.value = await get('/wb/projects')
  if (projects.value.length) { projectId.value = projects.value[0].id; load() }
})

async function load() {
  if (!projectId.value) return
  loading.value = true
  try { reports.value = await get(`/wb/reports?project_id=${projectId.value}`) } finally { loading.value = false }
}

async function generate() {
  await post(`/wb/reports/generate?project_id=${projectId.value}`)
  ElMessage.success('报告已生成')
  load()
}

function view(row) {
  get(`/wb/reports/${row.id}`).then(d => { viewing.value = d; viewDlg.value = true })
}

async function remove(row) {
  await del(`/wb/reports/${row.id}`)
  load()
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; }
</style>
