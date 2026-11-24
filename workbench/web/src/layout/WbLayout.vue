<template>
  <div class="wb-shell-wrap">
    <!-- 自绘标题栏（客户端模式）：与侧边栏一体化，无分隔线；整条可拖拽移动窗口 -->
    <header class="wb-titlebar">
      <div class="pywebview-drag-region wb-titlebar-drag">
        <div class="wb-titlebar-brand">
          <span class="tb-mark">C</span>
          <span class="tb-name">Cyu工作坊</span>
        </div>
      </div>
      <div v-if="inShell" class="wb-titlebar-btns">
        <button class="tb-btn" title="最小化" @click="winMin"><el-icon><Minus /></el-icon></button>
        <button class="tb-btn" :title="isMax ? '还原' : '最大化'" @click="winMax">
          <!-- 与 Windows 标准字形一致：最大化 = 单方框，还原 = 双层叠框 -->
          <svg v-if="!isMax" width="10" height="10" viewBox="0 0 10 10">
            <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" />
          </svg>
          <svg v-else width="10" height="10" viewBox="0 0 10 10">
            <rect x="0.5" y="2.5" width="7" height="7" fill="none" stroke="currentColor" />
            <path d="M2.5 2.5 V0.5 H9.5 V7.5 H7.5" fill="none" stroke="currentColor" />
          </svg>
        </button>
        <button class="tb-btn tb-close" title="关闭" @click="winClose"><el-icon><Close /></el-icon></button>
      </div>
    </header>

    <div class="wb-shell">
    <!-- 侧边导航 -->
    <aside class="wb-side">
      <nav class="wb-nav">
        <router-link v-for="m in menus" :key="m.path" :to="m.path" class="wb-nav-item" active-class="active">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </router-link>

        <!-- 执行中心分组 -->
        <div class="wb-nav-group">
          <div class="wb-nav-group-title"><el-icon><Operation /></el-icon><span>执行中心</span></div>
          <router-link v-for="s in execMenus" :key="s.path" :to="s.path" class="wb-nav-item sub" active-class="active">
            <span class="dot"></span>
            <span>{{ s.label }}</span>
          </router-link>
        </div>
      </nav>

      <!-- 左下角设置入口（需求 #10） -->
      <div class="wb-side-bottom">
        <router-link to="/settings" class="wb-nav-item" active-class="active" v-if="$route.path !== '/settings'">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </router-link>
        <router-link v-else to="/" class="wb-nav-item">
          <el-icon><Back /></el-icon>
          <span>返回首页</span>
        </router-link>
      </div>
    </aside>

    <!-- 主内容 -->
    <main class="wb-main">
      <header class="wb-header">
        <span class="wb-header-title">{{ $route.meta.title }}</span>
        <span v-if="$route.meta.building" class="building-tag">模块建设中 · P2/P3 交付</span>
      </header>
      <router-view />
    </main>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

// 客户端模式检测：pywebview 注入的 JS 桥可用时才显示窗口控制按钮
const inShell = ref(false)
const isMax = ref(false)   // 由壳进程 toggle_max 返回值驱动图标切换
function _markShell() { inShell.value = !!(window.pywebview && window.pywebview.api) }
onMounted(() => {
  _markShell()
  window.addEventListener('pywebviewready', _markShell)
})
onUnmounted(() => window.removeEventListener('pywebviewready', _markShell))

function winMin() { window.pywebview?.api?.minimize() }
async function winMax() {
  try {
    const st = await window.pywebview?.api?.toggle_max()
    isMax.value = !!st
  } catch { /* 旧版壳无返回值 */ }
}
function winClose() { window.pywebview?.api?.close() }

const menus = [
  { path: '/', label: '首页', icon: 'HomeFilled' },
  { path: '/projects', label: '项目管理', icon: 'Folder' },
  { path: '/requirements', label: '需求管理', icon: 'List' },
  { path: '/cases', label: '用例库', icon: 'Document' },
  { path: '/apimanage', label: '接口管理', icon: 'Connection' },
  { path: '/bugs', label: 'Bug 管理', icon: 'WarningFilled' },
  { path: '/logs', label: '日志管理', icon: 'Notebook' },
  { path: '/regression', label: '回归测试', icon: 'RefreshLeft' },
  { path: '/reports', label: '测试报告', icon: 'DataAnalysis' },
  { path: '/knowledge', label: '知识库', icon: 'Collection' }
]
const execMenus = [
  { path: '/exec/functional', label: '功能测试' },
  { path: '/exec/api', label: '接口测试' },
  { path: '/exec/auto', label: '自动化测试' }
]
</script>

<style scoped>
/* 自绘标题栏（客户端模式）：与侧边栏同色一体，无分隔线 */
.wb-shell-wrap { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.wb-titlebar {
  height: 44px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  background: #ffffff;
  user-select: none;
}
.wb-titlebar-drag { flex: 1; height: 100%; display: flex; align-items: center; }
.wb-titlebar-brand { display: flex; align-items: center; gap: 10px; padding-left: 16px; }
.tb-mark {
  width: 28px; height: 28px; border-radius: 8px;
  background: var(--wb-accent); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px;
}
.tb-name { font-weight: 600; font-size: 14px; color: #111827; }
.wb-titlebar-btns { display: flex; height: 100%; }
.tb-btn {
  width: 44px; height: 100%; border: none; background: transparent;
  display: flex; align-items: center; justify-content: center;
  color: #6b7280; cursor: pointer; font-size: 14px; outline: none;
}
.tb-btn:hover { background: #eef1f6; color: #111827; }
.tb-btn svg { display: block; }
.tb-btn.tb-close:hover { background: #e81123; color: #fff; }

.wb-shell { display: flex; flex: 1; min-height: 0; overflow: hidden; }

.wb-side {
  width: 208px;
  background: #ffffff;
  border-right: 1px solid var(--wb-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.wb-nav { flex: 1; padding: 8px 10px 6px; overflow-y: auto; }
.wb-nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px; margin: 2px 0;
  border-radius: 8px; color: #4b5563;
  text-decoration: none; font-size: 13.5px;
  transition: background .15s;
}
.wb-nav-item:hover { background: #f3f6fb; }
.wb-nav-item.active { background: var(--wb-accent-soft); color: var(--wb-accent); font-weight: 600; }
.wb-nav-item.sub { padding-left: 14px; }
.wb-nav-item.sub .dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; opacity: .6; }

.wb-nav-group { margin-top: 8px; }
.wb-nav-group-title {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px 4px; font-size: 12px; color: var(--wb-text-sub);
}

.wb-side-bottom { border-top: 1px solid var(--wb-border); padding: 8px 10px; }

.wb-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.wb-header {
  height: 52px; flex-shrink: 0;
  display: flex; align-items: center; gap: 12px;
  padding: 0 24px;
  background: #fff; border-bottom: 1px solid var(--wb-border);
}
.wb-header-title { font-size: 15px; font-weight: 600; }
.building-tag {
  font-size: 12px; color: #b45309; background: #fef3c7;
  padding: 2px 10px; border-radius: 20px;
}
</style>
