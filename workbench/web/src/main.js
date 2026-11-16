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
