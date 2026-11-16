<template>
  <el-dialog :model-value="modelValue" title="选择接口（从接口管理）" width="560px" @update:model-value="v => emit('update:modelValue', v)">
    <el-input v-model="keyword" placeholder="搜索接口名称 / URL" clearable style="margin-bottom: 10px">
      <template #prefix><el-icon><Search /></el-icon></template>
    </el-input>
    <div class="pick-list">
      <template v-for="item in displayList" :key="item.col.id">
        <div v-if="filterReqs(item.col.requests).length" class="pick-col muted">
          {{ '　'.repeat(item.depth) + (item.depth ? '└ ' : '') + item.col.name }}
        </div>
        <div
          v-for="rq in filterReqs(item.col.requests)" :key="rq.id"
          class="req-item pick-item" role="button"
          @click="pick(rq)"
        >
          <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ rq.method }}</span>
          <span class="req-name">{{ rq.name }}</span>
          <span class="muted pick-url">{{ rq.url }}</span>
        </div>
      </template>
      <template v-if="filterReqs(orphans).length">
        <div class="pick-col muted">未分组</div>
        <div
          v-for="rq in filterReqs(orphans)" :key="rq.id"
          class="req-item pick-item" role="button"
          @click="pick(rq)"
        >
          <span class="m-tag" :class="'m-' + rq.method.toLowerCase()">{{ rq.method }}</span>
          <span class="req-name">{{ rq.name }}</span>
          <span class="muted pick-url">{{ rq.url }}</span>
        </div>
      </template>
      <el-empty
        v-if="!displayList.length && !filterReqs(orphans).length"
        :description="keyword ? '无匹配接口' : '接口管理中还没有接口，先去「接口管理」新建'"
        :image-size="60"
      />
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { get } from '../api'

const props = defineProps({
  modelValue: Boolean,
  projectId: Number,
})
const emit = defineEmits(['update:modelValue', 'select'])

const keyword = ref('')
const collections = ref([])
const orphans = ref([])

watch(() => props.modelValue, async (open) => {
  if (!open) return
  keyword.value = ''
  try {
    const d = await get(`/wb/api-collections?project_id=${props.projectId}`)
    const byId = new Map(d.collections.map(c => [c.id, c]))
    for (const c of d.collections) {
      let dep = 0, p = c.parent_id
      while (p && byId.has(p)) { dep++; p = byId.get(p).parent_id }
      c._depth = dep
    }
    collections.value = d.collections
    orphans.value = d.orphans
  } catch (e) {
    ElMessage.error('接口列表加载失败：' + e.message)
  }
})

function filterReqs(list) {
  if (!keyword.value) return list || []
  const k = keyword.value.toLowerCase()
  return (list || []).filter(r => r.name.toLowerCase().includes(k) || (r.url || '').toLowerCase().includes(k))
}

// 平铺树（含层级），并过滤掉搜索后无命中接口的目录
const displayList = computed(() => {
  const out = []
  const hasMatch = c => filterReqs(c.requests).length || c.children.some(hasMatch)
  const walk = (list, depth) => {
    for (const c of list) {
      if (keyword.value && !hasMatch(c)) continue
      out.push({ col: c, depth })
      walk(c.children, depth + 1)
    }
  }
  const byId = new Map()
  for (const c of collections.value) { c.children = []; byId.set(c.id, c) }
  const roots = []
  for (const c of collections.value) {
    const p = c.parent_id ? byId.get(c.parent_id) : null
    if (p && p !== c) p.children.push(c)
    else roots.push(c)
  }
  walk(roots, 0)
  return out
})

async function pick(rq) {
  // 拉取完整配置（含 headers/params/body/auth），供快照保存
  const detail = await get(`/wb/api-requests/${rq.id}`)
  emit('select', detail)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.pick-list { max-height: 46vh; overflow-y: auto; }
.pick-col { font-size: 12px; font-weight: 600; padding: 8px 8px 4px; }
.req-item { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.req-item:hover { background: #f3f6fb; }
.req-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pick-url { font-size: 11px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.m-tag { width: 46px; text-align: center; flex-shrink: 0; font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 0; font-family: Consolas, Monaco, monospace; }
.m-get { color: #16a34a; background: #dcfce7; }
.m-post { color: #d97706; background: #fef3c7; }
.m-put { color: #2563eb; background: #dbeafe; }
.m-delete { color: #dc2626; background: #fee2e2; }
.m-patch { color: #7c3aed; background: #ede9fe; }
</style>
