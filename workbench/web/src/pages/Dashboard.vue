<template>
  <div class="page">
    <div class="page-title">项目总览</div>
    <div v-if="loading" class="card muted">加载中…</div>
    <div v-else-if="!projects.length" class="card">
      <el-empty description="还没有项目，去「项目管理」创建第一个项目">
        <el-button type="primary" @click="$router.push('/projects')">去创建</el-button>
      </el-empty>
    </div>
    <div v-else class="grid">
      <div v-for="p in projects" :key="p.id" class="card proj" @click="goProject(p.id)">
        <div class="proj-head">
          <div class="proj-name">{{ p.name }}</div>
          <el-tag size="small" effect="plain">{{ p.code }}</el-tag>
        </div>
        <div class="type-rows">
          <div class="type-row" v-for="t in types" :key="t.key">
            <span class="t-label"><span class="t-dot" :style="{ background: t.color }"></span>{{ t.label }}</span>
            <span class="t-count">{{ p.cases[t.key] || 0 }} 条用例</span>
          </div>
        </div>
        <div class="rate-row">
          <span class="muted">执行进度</span>
          <el-progress :percentage="p.execution_rate" :stroke-width="8" style="flex:1" />
        </div>
        <div class="proj-foot">
          <span class="muted">待处理 Bug：<b>{{ p.bugs_open }}</b></span>
          <span class="muted" v-if="p.last_plan">最近执行：{{ p.last_plan.name }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '../api'

const router = useRouter()
const projects = ref([])
const loading = ref(true)
const types = [
  { key: 'functional', label: '功能测试', color: '#3a7afe' },
  { key: 'api', label: '接口测试', color: '#10b981' },
  { key: 'automation', label: '自动化测试', color: '#f59e0b' }
]

onMounted(async () => {
  try {
    const d = await get('/wb/dashboard')
    projects.value = d.projects
  } finally { loading.value = false }
})

function goProject(id) { router.push({ path: '/projects', query: { focus: id } }) }
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.proj { cursor: pointer; transition: box-shadow .15s, transform .15s; }
.proj:hover { box-shadow: 0 4px 16px rgba(58,122,254,.12); transform: translateY(-2px); }
.proj-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.proj-name { font-size: 15px; font-weight: 600; }
.type-rows { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.type-row { display: flex; justify-content: space-between; align-items: center; }
.t-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #4b5563; }
.t-dot { width: 8px; height: 8px; border-radius: 50%; }
.t-count { font-size: 13px; color: var(--wb-text-sub); }
.rate-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.proj-foot { display: flex; justify-content: space-between; }
</style>
