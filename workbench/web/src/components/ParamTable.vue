<template>
  <div class="param-table">
    <div class="p-row p-head muted">
      <span class="p-enable" />
      <span style="width: 20%">参数名</span>
      <span style="width: 20%">参数值</span>
      <span style="width: 14%">类型</span>
      <span style="flex: 1">说明</span>
      <span class="p-del" />
    </div>
    <div v-for="(r, i) in rows" :key="i" class="p-row">
      <el-checkbox
        :model-value="r.enabled !== 0" class="p-enable"
        @update:model-value="v => setCell(i, 'enabled', v ? 1 : 0)"
      />
      <el-input :model-value="r.key" placeholder="参数名" size="small" @update:model-value="v => setCell(i, 'key', v)" />
      <el-input :model-value="r.value" placeholder="参数值" size="small" @update:model-value="v => setCell(i, 'value', v)" />
      <el-select :model-value="r.type || 'string'" size="small" @update:model-value="v => setCell(i, 'type', v)">
        <el-option v-for="t in ['string', 'integer', 'number', 'boolean']" :key="t" :value="t" :label="t" />
      </el-select>
      <el-input :model-value="r.desc" placeholder="说明" size="small" @update:model-value="v => setCell(i, 'desc', v)" />
      <el-icon class="p-del" @click="removeRow(i)"><Delete /></el-icon>
    </div>
    <el-button link type="primary" size="small" class="p-add" @click="addRow">
      <el-icon><Plus /></el-icon>添加参数
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
  emit('update:modelValue', [...props.modelValue, { key: '', value: '', type: 'string', desc: '', enabled: 1 }])
}

function removeRow(i) {
  emit('update:modelValue', props.modelValue.filter((_, idx) => idx !== i))
}
</script>

<style scoped>
.p-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; max-width: 820px; }
.p-head { font-size: 12px; margin-bottom: 4px; }
.p-enable { width: 24px; flex-shrink: 0; }
.p-row > .el-input:nth-of-type(1), .p-row > .el-input:nth-of-type(2) { flex-shrink: 0; }
.p-row > .el-input:nth-of-type(1) { width: 20%; }
.p-row > .el-input:nth-of-type(2) { width: 20%; }
.p-row > .el-select { width: 14%; flex-shrink: 0; }
.p-row > .el-input:nth-of-type(3) { width: auto; flex: 1; }
.p-del { color: #d1d5db; cursor: pointer; flex-shrink: 0; }
.p-del:hover { color: #ef4444; }
.p-add { margin-top: 2px; }
</style>
