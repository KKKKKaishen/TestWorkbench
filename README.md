# TestWorkbench（Cyu 工作坊）

一体化测试工作台：测试点管理、用例库（功能/接口/自动化用例）、接口管理与调试、接口测试流程编排（Apifox 风格步骤编辑器）、需求管理与测试点关联。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.13 + FastAPI + SQLAlchemy（MySQL） |
| 前端 | Vue 3 + Element Plus + Vite |
| 桌面壳 | pywebview + PyInstaller（可选打包 .exe） |
| AI 底座 | AI-Test-Case-Generator（LLM 生成测试用例） |

## 目录结构

```
├── workbench/
│   ├── base/          # FastAPI 后端（含 AI 用例生成底座）
│   │   ├── main.py        # AI 底座服务入口
│   │   ├── wbapi.py       # 工作坊业务 API（工作台主体）
│   │   ├── wbmodels.py    # 工作坊数据模型
│   │   └── requirements.txt
│   ├── web/           # Vue3 前端（构建产物由 vite 生成）
│   ├── pack-test/     # PyInstaller 桌面打包脚本（run.py + spec）
│   └── schema.sql     # 数据库建表脚本
```

## 环境要求

- Python 3.13+（建议先 `python -m venv venv` 建虚拟环境）
- Node.js 18+ / npm
- MySQL 8.x
- Windows 下运行桌面端需 pywebview（依赖 WebView2）

## 首次部署步骤

### 1. 后端

```bash
cd workbench/base
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # Linux/Mac: pip install -r requirements.txt
```

配置 `.env`（仓库已含一份可用的 `.env`，按需修改数据库连接等字段）。

初始化数据库（在 MySQL 中执行）：

```bash
mysql -u<user> -p <database> < ../schema.sql
```

启动工作台服务（端口 8901）：

```bash
venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8901
```

> AI 用例生成底座（main.py）与工作坊业务 API（wbapi.py）挂载在同一服务。

### 2. 前端

```bash
cd workbench/web
npm install
npm run build        # 产物输出到 dist/，由后端静态托管
```

浏览器访问 `http://127.0.0.1:8901/app/`。

### 3. 打包桌面 .exe（可选）

```bash
cd workbench/pack-test
pyinstaller TestWorkbenchShell.spec
# 产物在 dist/ 下
```

## 日常开发

- 后端热重载：`uvicorn main:app --reload --port 8901`
- 前端开发模式：`cd workbench/web && npm run dev`
- 回归测试：`cd workbench/base && venv/Scripts/python -m pytest`

## 注意事项

- `venv/`、`node_modules/`、`wheels/`、`dist/`、`build/`、日志与 `__pycache__` 均不入库（见 `.gitignore`），克隆后按上述步骤重建即可
- 数据库连接信息在 `workbench/base/.env`
