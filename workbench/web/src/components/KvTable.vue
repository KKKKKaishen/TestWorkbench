<template>
  <div class="kv-table">
    <div class="kv-row kv-head muted">
      <span class="kv-enable" />
      <span style="width: 32%">键</span>
      <span style="flex: 1">值</span>
      <span class="kv-del" />
    </div>
    <div v-for="(r, i) in rows" :key="i" class="kv-row">
      <el-checkbox
        :model-value="!!r.enabled" class="kv-enable"
        @update:model-value="v => setCell(i, 'enabled', v ? 1 : 0)"
      />
      <el-input
        :model-value="r.key" placeholder="Key" class="kv-input"
        @update:model-value="v => setCell(i, 'key', v)"
      />
      <el-input
        :model-value="r.value" placeholder="Value" class="kv-input kv-val"
        @update:model-value="v => setCell(i, 'value', v)"
      />
      <el-icon class="kv-del" @click="removeRow(i)"><Delete /></el-icon>
    </div>
    <el-button link type="primary" size="small" class="kv-add" @click="addRow">
      <el-icon><Plus /></el-icon>添加一行
    </el-button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])

const rows = computed(() => props.modelValue)

function setCell(i, k, v) {
  const next = props.modelValue.map((r, idx) => (idx === i ? { ...r, [k]: v } : r))
  emit('update:modelValue', next)
}

function addRow() {
  emit('update:modelValue', [...props.modelValue, { key: '', value: '', enabled: 1 }])
}

function removeRow(i) {
  emit('update:modelValue', props.modelValue.filter((_, idx) => idx !== i))
}
</script>

<style scoped>
.kv-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; max-width: 760px; }
.kv-head { font-size: 12px; margin-bottom: 4px; }
.kv-enable { width: 24px; flex-shrink: 0; }
.kv-input { flex-shrink: 0; width: 32%; }
.kv-val { width: auto; flex: 1; }
.kv-del { color: #d1d5db; cursor: pointer; flex-shrink: 0; }
.kv-del:hover { color: #ef4444; }
.kv-add { margin-top: 2px; }
</style>
