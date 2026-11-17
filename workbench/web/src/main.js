import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as Icons from '@element-plus/icons-vue'
import App from './App.vue'
import './style.css'
import { routes } from './router'

const app = createApp(App)
for (const [name, comp] of Object.entries(Icons)) app.component(name, comp)

app.use(createRouter({ history: createWebHashHistory(), routes }))
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')

// ---- 前端错误上报：window.onerror / unhandledrejection / Vue 错误 → 后端日志 ----
let _errSeq = 0
function reportError(kind, message, extra) {
  if (++_errSeq > 50) return  // 会话级限流，避免错误风暴刷爆日志
  try {
    fetch('/wb/client-errors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind, message: String(message || '').slice(0, 800),
        extra: String(extra || '').slice(0, 1200),
        page: location.hash, ts: Date.now(),
      }),
      keepalive: true,
    }).catch(() => {})
  } catch { /* 上报失败静默 */ }
}

window.addEventListener('error', e => {
  reportError('js_error', e.message, `${e.filename}:${e.lineno}:${e.colno}`)
})
window.addEventListener('unhandledrejection', e => {
  reportError('promise_rejection', e.reason?.message || e.reason, e.reason?.stack)
})
app.config.errorHandler = (err, instance, info) => {
  reportError('vue_error', err?.message || err, `${info}\n${err?.stack || ''}`)
  console.error(err)
}
