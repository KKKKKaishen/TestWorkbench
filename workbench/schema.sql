-- =====================================================================
-- 测试深度工作台 · 数据库设计 v1.1（MySQL 8 / InnoDB / utf8mb4）
-- 变更：2026-11-11 全字段补充中文注释（COLUMN COMMENT）
-- 命名规则：底座 AI-TCG 既有表保持原样不动；工作台新增表统一 wb_ 前缀。
-- 库名：Workbench（2026-11-11 由 aitest_rag 改名而来）
-- =====================================================================

CREATE DATABASE IF NOT EXISTS Workbench
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE Workbench;

-- =========================================================
-- 1. 项目域
-- =========================================================
CREATE TABLE wb_projects (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  name          VARCHAR(100)  NOT NULL COMMENT '项目名称',
  code          VARCHAR(50)   NOT NULL COMMENT '项目代号（用例/Bug 编号前缀，如 SHOP）',
  description   TEXT          NULL COMMENT '项目描述/简介',
  status        VARCHAR(20)   NOT NULL DEFAULT 'active' COMMENT '项目状态：active=进行中，archived=已归档',
  owner_name    VARCHAR(50)   NULL COMMENT '负责人姓名（单机版仅作备注展示）',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  UNIQUE KEY uk_code (code),
  KEY idx_status (status)
) COMMENT '项目（一等实体，需求/用例/执行/Bug 全部挂在项目下）';

CREATE TABLE wb_requirements (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  title         VARCHAR(200)  NOT NULL COMMENT '需求标题',
  content       MEDIUMTEXT    NULL COMMENT '需求正文/粘贴的 PRD 片段原文',
  source_type   VARCHAR(20)   NOT NULL DEFAULT 'manual' COMMENT '来源：manual=手动录入，import_doc=文档导入，ai=AI 整理',
  version       VARCHAR(20)   NOT NULL DEFAULT 'v1' COMMENT '需求版本号',
  status        VARCHAR(20)   NOT NULL DEFAULT 'pending' COMMENT '需求状态：pending=待分析，split=已拆解测试点，covered=已覆盖用例，passed=测试通过',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  KEY idx_project_status (project_id, status),
  CONSTRAINT fk_req_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '需求（项目详情页内嵌 Tab，支持需求-用例追溯）';

CREATE TABLE wb_test_points (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  requirement_id  BIGINT UNSIGNED NOT NULL COMMENT '所属需求ID（外键 wb_requirements）',
  project_id      BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（冗余，便于按项目查询）',
  title           VARCHAR(200)  NOT NULL COMMENT '测试点标题',
  description     TEXT          NULL COMMENT '测试点详细说明/验证要点',
  priority        VARCHAR(10)   NULL COMMENT '优先级：P0=最高，P3=最低',
  status          VARCHAR(20)   NOT NULL DEFAULT 'open' COMMENT '状态：open=待覆盖，covered=已被用例覆盖，deprecated=已废弃',
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（AI 拆解或手动添加时间）',
  KEY idx_requirement (requirement_id),
  KEY idx_project_status (project_id, status),
  CONSTRAINT fk_tp_req FOREIGN KEY (requirement_id) REFERENCES wb_requirements(id) ON DELETE CASCADE
) COMMENT '测试点（AI 拆解需求的产物，需求与用例间的中间层）';

-- =========================================================
-- 2. 用例域
-- =========================================================
CREATE TABLE wb_test_cases (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id      BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  test_point_id   BIGINT UNSIGNED NULL COMMENT '来源测试点ID（外键 wb_test_points，追溯用；自由创建的用例为空）',
  case_no         VARCHAR(50)   NOT NULL COMMENT '用例编号，规则 {项目代号}-TC-0001，全局唯一',
  title           VARCHAR(200)  NOT NULL COMMENT '用例标题',
  case_type       VARCHAR(20)   NOT NULL COMMENT '用例类型：functional=功能测试，api=接口测试，automation=自动化测试',
  module_path     VARCHAR(255)  NULL COMMENT '所属模块树路径，如 商城/购物车（用例库左侧菜单按此分组）',
  priority        VARCHAR(10)   NULL COMMENT '优先级：P0=冒烟必测，P1=核心，P2=一般，P3=低',
  precondition    TEXT          NULL COMMENT '前置条件（执行前需满足的状态/数据）',
  steps           JSON          NULL COMMENT '测试步骤，JSON 数组 [{step:序号, action:操作, expected:预期}]',
  expected        TEXT          NULL COMMENT '整体预期结果',
  tags            JSON          NULL COMMENT '标签，JSON 数组（如 回归/冒烟/异常场景）',
  status          VARCHAR(20)   NOT NULL DEFAULT 'active' COMMENT '用例状态：draft=草稿，active=生效可用，deprecated=已废弃',
  api_request_id  BIGINT UNSIGNED NULL COMMENT '接口用例来源：调试请求转用例的链接（外键 wb_api_requests）',
  gen_source      VARCHAR(20)   NOT NULL DEFAULT 'manual' COMMENT '生成方式：manual=人工编写，ai=AI 生成，rule_engine=规则引擎生成',
  version         INT           NOT NULL DEFAULT 1 COMMENT '用例版本号，每次编辑递增',
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  UNIQUE KEY uk_case_no (case_no),
  KEY idx_project_type (project_id, case_type),
  KEY idx_project_module (project_id, module_path(64)),
  KEY idx_status (status),
  KEY idx_test_point (test_point_id),
  CONSTRAINT fk_case_project FOREIGN KEY (project_id) REFERENCES wb_projects(id),
  CONSTRAINT fk_case_tp FOREIGN KEY (test_point_id) REFERENCES wb_test_points(id) ON DELETE SET NULL
) COMMENT '统一用例库（功能/接口/自动化三类合一，执行中心按 case_type 分泳道取数）';

-- =========================================================
-- 3. 接口调试域（Postman 核心）
-- =========================================================
CREATE TABLE wb_api_environments (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  name          VARCHAR(100)  NOT NULL COMMENT '环境名称（如 开发/测试/预发/生产）',
  base_url      VARCHAR(255)  NULL COMMENT '环境基础地址（配合 {{var}} 变量插值）',
  variables     JSON          NULL COMMENT '环境变量键值对 JSON（如 token/host），请求 URL 与 Header 中可用 {{key}} 引用',
  is_default    TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '是否默认环境：1=是，0=否',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  KEY idx_project (project_id),
  CONSTRAINT fk_env_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '接口测试环境变量（多环境切换）';

CREATE TABLE wb_api_collections (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  parent_id     BIGINT UNSIGNED NULL COMMENT '父级目录ID（树形结构，顶级目录为空）',
  name          VARCHAR(100)  NOT NULL COMMENT '目录/集合名称',
  sort_order    INT           NOT NULL DEFAULT 0 COMMENT '同层排序号（小在前）',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  KEY idx_project (project_id),
  KEY idx_parent (parent_id),
  CONSTRAINT fk_col_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '接口 Collections 目录树（Postman 式组织结构）';

CREATE TABLE wb_api_requests (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  collection_id   BIGINT UNSIGNED NULL COMMENT '所属 Collections 目录ID（外键 wb_api_collections，未归组为空）',
  project_id      BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  name            VARCHAR(200)  NOT NULL COMMENT '请求名称',
  method          VARCHAR(10)   NOT NULL DEFAULT 'GET' COMMENT 'HTTP 方法：GET/POST/PUT/DELETE/PATCH 等',
  url             VARCHAR(500)  NOT NULL COMMENT '请求 URL（支持 {{变量}} 插值）',
  headers         JSON          NULL COMMENT '请求头，JSON 数组 [{key, value, enabled}]',
  body_type       VARCHAR(20)   NULL COMMENT '请求体类型：none/json/form/x-www-urlencoded/raw',
  body            MEDIUMTEXT    NULL COMMENT '请求体内容（按 body_type 解析）',
  auth            JSON          NULL COMMENT '认证配置 JSON（Bearer/Basic/自定义，如 {type, token}）',
  last_status_code INT          NULL COMMENT '最近一次调试的 HTTP 状态码（列表页预览用）',
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后编辑时间',
  KEY idx_collection (collection_id),
  KEY idx_project (project_id),
  CONSTRAINT fk_req_col FOREIGN KEY (collection_id) REFERENCES wb_api_collections(id) ON DELETE CASCADE
) COMMENT '接口调试请求（可一键转为接口用例进用例库）';

CREATE TABLE wb_api_request_history (
  id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  request_id        BIGINT UNSIGNED NULL COMMENT '关联的请求ID（外键 wb_api_requests；一次性未保存请求为空）',
  project_id        BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（冗余，便于按项目查历史）',
  environment_id    BIGINT UNSIGNED NULL COMMENT '执行时使用的环境ID（外键 wb_api_environments）',
  method            VARCHAR(10)  NOT NULL COMMENT 'HTTP 方法（快照冗余）',
  url               VARCHAR(500) NOT NULL COMMENT '实际请求 URL（变量替换后）',
  status_code       INT          NULL COMMENT '响应 HTTP 状态码',
  duration_ms       INT          NULL COMMENT '响应耗时（毫秒）',
  success           TINYINT(1)   NULL COMMENT '是否成功：1=成功，0=失败（状态码/断言判定）',
  request_snapshot  LONGTEXT     NULL COMMENT '请求完整快照（头/体原样留档，排查用）',
  response_snapshot LONGTEXT     NULL COMMENT '响应完整快照（头/体/原样留档）',
  created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
  KEY idx_request_time (request_id, created_at),
  KEY idx_project_time (project_id, created_at)
) COMMENT '接口请求历史（每次发送留档，支持回放与排查）';

-- =========================================================
-- 4. 执行域
-- =========================================================
CREATE TABLE wb_execution_plans (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  name          VARCHAR(200)  NOT NULL COMMENT '执行计划/轮次名称（如 登录模块第3轮回归）',
  plan_type     VARCHAR(20)   NOT NULL COMMENT '执行类型：functional=功能，api=接口，automation=自动化，regression=回归',
  status        VARCHAR(20)   NOT NULL DEFAULT 'running' COMMENT '计划状态：running=进行中，completed=已完成，aborted=已中止',
  executor      VARCHAR(50)   NULL COMMENT '执行人（单机版为记录字段）',
  total_count   INT           NOT NULL DEFAULT 0 COMMENT '计划内用例总数（冗余计数）',
  pass_count    INT           NOT NULL DEFAULT 0 COMMENT '已通过用例数（冗余计数，随执行刷新）',
  fail_count    INT           NOT NULL DEFAULT 0 COMMENT '失败用例数（冗余计数）',
  block_count   INT           NOT NULL DEFAULT 0 COMMENT '阻塞用例数（冗余计数）',
  skip_count    INT           NOT NULL DEFAULT 0 COMMENT '跳过用例数（冗余计数）',
  started_at    DATETIME      NULL COMMENT '执行开始时间',
  finished_at   DATETIME      NULL COMMENT '执行完成时间',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '计划创建时间',
  KEY idx_project_type_status (project_id, plan_type, status),
  KEY idx_created (created_at),
  CONSTRAINT fk_plan_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '执行计划/轮次（执行中心三个子菜单按类型发起；首页进度汇总读冗余计数列）';

CREATE TABLE wb_execution_plan_cases (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  plan_id       BIGINT UNSIGNED NOT NULL COMMENT '所属执行计划ID（外键 wb_execution_plans）',
  case_id       BIGINT UNSIGNED NOT NULL COMMENT '用例ID（外键 wb_test_cases）',
  result        VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT '执行结果：pending=待执行，pass=通过，fail=失败，block=阻塞，skip=跳过',
  executor      VARCHAR(50)  NULL COMMENT '实际执行人',
  executed_at   DATETIME     NULL COMMENT '实际执行时间（点单式勾选时写入）',
  note          VARCHAR(500) NULL COMMENT '执行备注（一句话说明失败原因等）',
  KEY idx_plan_result (plan_id, result),
  KEY idx_case (case_id),
  CONSTRAINT fk_pc_plan FOREIGN KEY (plan_id) REFERENCES wb_execution_plans(id) ON DELETE CASCADE,
  CONSTRAINT fk_pc_case FOREIGN KEY (case_id) REFERENCES wb_test_cases(id)
) COMMENT '计划-用例清单（点单式执行列表，左用例右记录）';

CREATE TABLE wb_execution_records (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  plan_case_id    BIGINT UNSIGNED NOT NULL COMMENT '所属清单条目ID（外键 wb_execution_plan_cases）',
  case_id         BIGINT UNSIGNED NOT NULL COMMENT '用例ID（冗余，便于按用例查执行历史）',
  actual_result   TEXT         NULL COMMENT '实际结果描述',
  step_results    JSON         NULL COMMENT '分步骤执行结果 JSON [{step, status, note}]',
  duration_ms     INT          NULL COMMENT '本条用例执行耗时（毫秒）',
  bug_id          BIGINT UNSIGNED NULL COMMENT '失败转 Bug 后回填的 BugID（外键 wb_bugs）',
  executed_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录写入时间',
  KEY idx_plan_case (plan_case_id),
  KEY idx_case_time (case_id, executed_at),
  CONSTRAINT fk_rec_pc FOREIGN KEY (plan_case_id) REFERENCES wb_execution_plan_cases(id) ON DELETE CASCADE
) COMMENT '执行记录（步骤明细留档；截图/附件经 wb_attachments 挂载）';

-- =========================================================
-- 5. 缺陷域（禅道式四向关联）
-- =========================================================
CREATE TABLE wb_bugs (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  bug_no        VARCHAR(50)   NOT NULL COMMENT 'Bug 编号，规则 {项目代号}-BUG-0001，全局唯一',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  title         VARCHAR(200)  NOT NULL COMMENT 'Bug 标题',
  description   MEDIUMTEXT    NULL COMMENT 'Bug 详情（模板：复现步骤/实际结果/期望结果/影响范围）',
  severity      VARCHAR(20)   NULL COMMENT '严重程度：fatal=致命，serious=严重，normal=一般，minor=轻微',
  priority      VARCHAR(10)   NULL COMMENT '处理优先级：P0=紧急，P3=低',
  status        VARCHAR(20)   NOT NULL DEFAULT 'active' COMMENT 'Bug 状态：active=新建，processing=处理中，fixed=已修复待回归，regressed=回归失败，closed=已关闭，rejected=已拒绝',
  source        VARCHAR(20)   NOT NULL DEFAULT 'manual' COMMENT '来源：manual=手动创建，execution_fail=功能执行失败转出，api_test=接口测试失败转出',
  plan_id       BIGINT UNSIGNED NULL COMMENT '发现来源执行计划ID（外键 wb_execution_plans）',
  assignee      VARCHAR(50)   NULL COMMENT '指派处理人',
  reporter      VARCHAR(50)   NULL COMMENT '报告人（单机版为记录字段）',
  found_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发现时间',
  closed_at     DATETIME      NULL COMMENT '关闭时间',
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
  UNIQUE KEY uk_bug_no (bug_no),
  KEY idx_project_status (project_id, status),
  KEY idx_plan (plan_id),
  KEY idx_status_severity (status, severity),
  CONSTRAINT fk_bug_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT 'Bug 缺陷（截图经 wb_attachments 挂载，四向关联经 wb_bug_links）';

CREATE TABLE wb_bug_links (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  bug_id        BIGINT UNSIGNED NOT NULL COMMENT 'Bug ID（外键 wb_bugs）',
  link_type     VARCHAR(20)  NOT NULL COMMENT '关联对象类型：requirement=需求，case=用例，plan=执行计划，log=测试日志，regression_run=回归执行',
  link_id       BIGINT UNSIGNED NOT NULL COMMENT '关联对象的ID（按 link_type 指向对应表）',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '关联创建时间',
  UNIQUE KEY uk_bug_type_id (bug_id, link_type, link_id),
  KEY idx_link_lookup (link_type, link_id),
  CONSTRAINT fk_bl_bug FOREIGN KEY (bug_id) REFERENCES wb_bugs(id) ON DELETE CASCADE
) COMMENT 'Bug 通用关联表（四向双向关联实现；idx_link_lookup 支持从用例/需求/日志反查 Bug）';

-- =========================================================
-- 6. 日志域
-- =========================================================
CREATE TABLE wb_test_logs (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  plan_id       BIGINT UNSIGNED NULL COMMENT '关联执行计划ID（可空，外键 wb_execution_plans）',
  log_type      VARCHAR(20)  NOT NULL DEFAULT 'execution' COMMENT '日志类型：execution=执行日志，review=评审记录，comm=沟通记录，other=其他',
  title         VARCHAR(200) NOT NULL COMMENT '日志标题',
  content       MEDIUMTEXT   NULL COMMENT '日志正文（支持长文本）',
  log_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '业务发生时间（可与创建时间不同）',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  KEY idx_project_type_time (project_id, log_type, log_time),
  KEY idx_plan (plan_id),
  CONSTRAINT fk_log_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '测试日志（可经 wb_bug_links 关联 Bug，双向跳转）';

-- =========================================================
-- 7. 附件（通用多态挂载：Bug 截图 / 执行截图 / 日志附件）
-- =========================================================
CREATE TABLE wb_attachments (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  owner_type    VARCHAR(30)  NOT NULL COMMENT '挂载对象类型：bug=Bug，execution_record=执行记录，test_log=测试日志，api_request=接口请求',
  owner_id      BIGINT UNSIGNED NOT NULL COMMENT '挂载对象ID（按 owner_type 指向对应表）',
  file_name     VARCHAR(255) NOT NULL COMMENT '原始文件名（含扩展名）',
  file_path     VARCHAR(500) NOT NULL COMMENT '存储相对路径（data/ 附件目录，exe 程序目录分离）',
  file_size     INT          NULL COMMENT '文件大小（字节）',
  mime_type     VARCHAR(100) NULL COMMENT 'MIME 类型（image/png 等）',
  uploaded_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
  KEY idx_owner (owner_type, owner_id)
) COMMENT '通用附件表（Bug 支持 Ctrl+V 粘贴截图/拖拽/多图；执行截图/日志附件同表挂载）';

-- =========================================================
-- 8. 回归域
-- =========================================================
CREATE TABLE wb_regression_sets (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  name          VARCHAR(200) NOT NULL COMMENT '回归集名称（如 商城核心回归集）',
  description   TEXT         NULL COMMENT '回归集说明/适用场景',
  case_count    INT          NOT NULL DEFAULT 0 COMMENT '回归集内用例数（冗余计数，条目增删时刷新）',
  last_run_at   DATETIME     NULL COMMENT '最近一次回归执行时间',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  KEY idx_project (project_id),
  CONSTRAINT fk_rgset_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '回归集（圈选的固定用例集合，供反复回归）';

CREATE TABLE wb_regression_set_items (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  set_id        BIGINT UNSIGNED NOT NULL COMMENT '所属回归集ID（外键 wb_regression_sets）',
  case_id       BIGINT UNSIGNED NOT NULL COMMENT '用例ID（外键 wb_test_cases）',
  added_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入回归集时间',
  UNIQUE KEY uk_set_case (set_id, case_id),
  KEY idx_case (case_id),
  CONSTRAINT fk_ri_set FOREIGN KEY (set_id) REFERENCES wb_regression_sets(id) ON DELETE CASCADE,
  CONSTRAINT fk_ri_case FOREIGN KEY (case_id) REFERENCES wb_test_cases(id)
) COMMENT '回归集条目（回归集与用例的多对多关联）';

CREATE TABLE wb_regression_runs (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  set_id        BIGINT UNSIGNED NOT NULL COMMENT '回归集ID（外键 wb_regression_sets）',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（冗余，便于按项目查询）',
  plan_id       BIGINT UNSIGNED NULL COMMENT '实际落地的执行计划ID（外键 wb_execution_plans，plan_type=regression，进度汇入执行中心）',
  trigger_type  VARCHAR(20)  NOT NULL DEFAULT 'manual' COMMENT '触发方式：manual=手动触发，scheduled=定时触发（P3）',
  status        VARCHAR(20)  NOT NULL DEFAULT 'running' COMMENT '回归执行状态：running=进行中，completed=已完成，failed=执行失败',
  pass_rate     DECIMAL(5,2) NULL COMMENT '本次回归通过率（百分比，如 95.50）',
  started_at    DATETIME     NULL COMMENT '回归开始时间',
  finished_at   DATETIME     NULL COMMENT '回归结束时间',
  KEY idx_set (set_id),
  KEY idx_project_time (project_id, started_at),
  CONSTRAINT fk_rrun_set FOREIGN KEY (set_id) REFERENCES wb_regression_sets(id),
  CONSTRAINT fk_rrun_plan FOREIGN KEY (plan_id) REFERENCES wb_execution_plans(id) ON DELETE SET NULL
) COMMENT '回归执行记录（Bug 关闭卡点：需存在关联的回归通过记录）';

CREATE TABLE wb_regression_schedules (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  set_id        BIGINT UNSIGNED NOT NULL COMMENT '回归集ID（外键 wb_regression_sets）',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（冗余，便于按项目查询）',
  name          VARCHAR(200) NOT NULL COMMENT '计划名称（如 每晚核心回归）',
  cron          VARCHAR(50)  NOT NULL COMMENT '5 段 cron 表达式：分 时 日 月 周',
  environment_id BIGINT UNSIGNED NULL COMMENT '执行环境ID（空 = 用默认环境）',
  enabled       TINYINT      NOT NULL DEFAULT 1 COMMENT '是否启用：1=启用，0=停用',
  last_run_at   DATETIME     NULL COMMENT '最近一次触发时间',
  next_run_at   DATETIME     NULL COMMENT '下次触发时间（调度线程据此判断）',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  KEY idx_rsch_set (set_id),
  KEY idx_rsch_project (project_id),
  KEY idx_rsch_next (enabled, next_run_at),
  CONSTRAINT fk_rsch_set FOREIGN KEY (set_id) REFERENCES wb_regression_sets(id) ON DELETE CASCADE
) COMMENT '回归定时计划（RG-02：cron 定时触发回归集执行）';

-- =========================================================
-- 9. 报告 / 设置 / AI 观测
-- =========================================================
CREATE TABLE wb_reports (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  project_id    BIGINT UNSIGNED NOT NULL COMMENT '所属项目ID（外键 wb_projects）',
  plan_id       BIGINT UNSIGNED NULL COMMENT '关联执行计划ID（单轮报告时有值，外键 wb_execution_plans）',
  report_type   VARCHAR(20)  NOT NULL DEFAULT 'iteration' COMMENT '报告类型：iteration=迭代测试报告，regression=回归报告，summary=项目汇总',
  title         VARCHAR(200) NOT NULL COMMENT '报告标题',
  stats         JSON         NULL COMMENT '统计快照 JSON（需求覆盖率/通过率/缺陷分布等，生成时点冻结）',
  content       MEDIUMTEXT   NULL COMMENT '报告正文（AI 生成或人工编辑，Markdown）',
  generated_by  VARCHAR(20)  NOT NULL DEFAULT 'ai' COMMENT '生成方式：ai=AI 生成，manual=人工生成',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '报告生成时间',
  KEY idx_project_type (project_id, report_type),
  KEY idx_plan (plan_id),
  CONSTRAINT fk_report_project FOREIGN KEY (project_id) REFERENCES wb_projects(id)
) COMMENT '测试报告（数据全部来自工作台内真实执行记录，可导出）';

CREATE TABLE wb_settings (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  setting_key   VARCHAR(100) NOT NULL COMMENT '配置键（点分命名，如 ai.deepseek.api_key）',
  setting_group VARCHAR(50)  NOT NULL DEFAULT 'general' COMMENT '配置分组：general=通用，ai_model=模型配置，database=数据库，notification=通知',
  setting_value TEXT         NULL COMMENT '配置值（is_secret=1 时为 AES 加密密文）',
  is_secret     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否敏感配置：1=是（界面打码显示，落库前加密），0=否',
  description   VARCHAR(200) NULL COMMENT '配置项说明（设置界面提示文案）',
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间',
  UNIQUE KEY uk_key (setting_key),
  KEY idx_group (setting_group)
) COMMENT '设置中心（首页左下角齿轮入口；DeepSeek/GLM/Qwen 三家模型 key 在 ai_model 组管理）';

CREATE TABLE wb_ai_call_logs (
  id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  scene             VARCHAR(30)  NOT NULL COMMENT '调用场景：point_split=需求拆测试点，case_gen=用例生成，assert_suggest=断言建议，fail_analyze=失败分析，report_gen=报告生成',
  model_name        VARCHAR(60)  NOT NULL COMMENT '使用的模型名称（如 deepseek-chat/glm-4/qwen-max）',
  prompt_tokens     INT          NULL COMMENT '输入消耗 token 数',
  completion_tokens INT          NULL COMMENT '输出消耗 token 数',
  duration_ms       INT          NULL COMMENT '本次调用耗时（毫秒）',
  success           TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '调用是否成功：1=成功，0=失败',
  error_msg         VARCHAR(500) NULL COMMENT '失败时的错误信息（截断至500字符）',
  created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '调用时间',
  KEY idx_scene_time (scene, created_at)
) COMMENT 'AI 调用流水（成本/成功率观测，设置界面展示用量统计）';

CREATE TABLE wb_kb_chunks (
  id          BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增ID',
  kb_id       VARCHAR(36) NOT NULL COMMENT '底座知识库ID（knowledge_bases.id，项目映射）',
  file_id     VARCHAR(36) NOT NULL COMMENT '底座知识文件ID（knowledge_files.id）',
  chunk_no    INT         NOT NULL DEFAULT 0 COMMENT '分块序号（从0起）',
  content     MEDIUMTEXT  NOT NULL COMMENT '分块正文（关键词检索通道）',
  created_at  DATETIME    NULL DEFAULT CURRENT_TIMESTAMP COMMENT '写入时间',
  KEY ix_wb_kb_chunks_kb_id (kb_id),
  KEY ix_wb_kb_chunks_file_id (file_id)
) COMMENT '知识库文档分块（关键词检索通道；语义检索走底座 ChromaDB 向量库）';
