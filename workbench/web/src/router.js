import Dashboard from './pages/Dashboard.vue'
import Projects from './pages/Projects.vue'
import Requirements from './pages/Requirements.vue'
import Cases from './pages/Cases.vue'
import ApiManage from './pages/ApiManage.vue'
import Execution from './pages/Execution.vue'
import ApiTest from './pages/ApiTest.vue'
import AutoTest from './pages/Placeholder.vue'
import Bugs from './pages/Bugs.vue'
import Logs from './pages/Logs.vue'
import Regression from './pages/Placeholder.vue'
import Reports from './pages/Reports.vue'
import Knowledge from './pages/Knowledge.vue'
import Settings from './pages/Settings.vue'

export const routes = [
  { path: '/', component: Dashboard, meta: { title: '首页' } },
  { path: '/projects', component: Projects, meta: { title: '项目管理' } },
  { path: '/requirements', component: Requirements, meta: { title: '需求管理' } },
  { path: '/cases', component: Cases, meta: { title: '用例库' } },
  { path: '/apimanage', component: ApiManage, meta: { title: '接口管理' } },
  { path: '/exec/functional', component: Execution, meta: { title: '功能测试', planType: 'functional' } },
  { path: '/exec/api', component: ApiTest, meta: { title: '接口测试' } },
  { path: '/exec/auto', component: AutoTest, meta: { title: '自动化测试', building: true } },
  { path: '/bugs', component: Bugs, meta: { title: 'Bug 管理' } },
  { path: '/logs', component: Logs, meta: { title: '日志管理' } },
  { path: '/regression', component: Regression, meta: { title: '回归测试', building: true } },
  { path: '/reports', component: Reports, meta: { title: '测试报告' } },
  { path: '/knowledge', component: Knowledge, meta: { title: '知识库' } },
  { path: '/settings', component: Settings, meta: { title: '设置' } }
]
