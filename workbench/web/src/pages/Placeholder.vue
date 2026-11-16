<template>
  <div class="page">
    <div class="card" style="min-height: 400px; display:flex; align-items:center; justify-content:center;">
      <el-empty :description="`「${$route.meta.title}」按排期在后续版本交付，敬请期待`">
        <template #image><el-icon :size="64" color="#c0cfe8"><Box /></el-icon></template>
      </el-empty>
    </div>
  </div>
</template>
