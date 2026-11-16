"""测试深度工作台业务模型（wb_ 前缀，21 张表）。

与 workbench/schema.sql（MySQL 8 线上库 Workbench）严格对应。
表已由 schema.sql 建好，create_all 对这些表是幂等 no-op；
模型在此声明是为了 ORM 读写与 SQLite 开发环境自动建表。
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from models.database import Base, LongText


class WbProject(Base):
    __tablename__ = "wb_projects"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=False, unique=True)
    description = Column(Text)
    status = Column(String(20), nullable=False, default="active", index=True)
    owner_name = Column(String(50))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class WbRequirement(Base):
    __tablename__ = "wb_requirements"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    content = Column(LongText)
    source_type = Column(String(20), nullable=False, default="manual")
    version = Column(String(20), nullable=False, default="v1")
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_project_status", "project_id", "status"),)


class WbTestPoint(Base):
    __tablename__ = "wb_test_points"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    requirement_id = Column(BigInteger, ForeignKey("wb_requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    priority = Column(String(10))
    status = Column(String(20), nullable=False, default="open")
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_tp_project_status", "project_id", "status"),)


class WbTestCase(Base):
    __tablename__ = "wb_test_cases"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    test_point_id = Column(BigInteger, ForeignKey("wb_test_points.id", ondelete="SET NULL"), index=True)
    case_no = Column(String(50), nullable=False, unique=True)
    title = Column(String(200), nullable=False)
    case_type = Column(String(20), nullable=False)
    module_path = Column(String(255))
    priority = Column(String(10))
    precondition = Column(Text)
    steps = Column(JSON)
    expected = Column(Text)
    tags = Column(JSON)
    status = Column(String(20), nullable=False, default="active", index=True)
    api_request_id = Column(BigInteger)
    # 接口用例快照：{request_id, name, method, url, headers, params, body_type, body, auth, description}
    # 创建时从接口管理抓取一份参数，保存用例不回写原接口（除非显式选择同步）
    api_snapshot = Column(JSON)
    gen_source = Column(String(20), nullable=False, default="manual")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (
        Index("idx_case_project_type", "project_id", "case_type"),
        Index("idx_case_project_module", "project_id", "module_path"),
    )


class WbApiEnvironment(Base):
    __tablename__ = "wb_api_environments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(255))
    variables = Column(JSON)
    is_default = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())


class WbApiCollection(Base):
    __tablename__ = "wb_api_collections"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    parent_id = Column(BigInteger, index=True)
    name = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())


class WbApiRequest(Base):
    __tablename__ = "wb_api_requests"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    collection_id = Column(BigInteger, ForeignKey("wb_api_collections.id", ondelete="CASCADE"), index=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    method = Column(String(10), nullable=False, default="GET")
    url = Column(String(500), nullable=False)
    headers = Column(JSON)
    params = Column(JSON)  # 文档参数 [{key, value, type, desc}]（Query/Path 参数说明）
    description = Column(String(500))  # 接口说明
    body_type = Column(String(20))
    body = Column(LongText)
    auth = Column(JSON)
    last_status_code = Column(Integer)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class WbApiScenario(Base):
    """接口测试场景树（Apifox 自动化测试式）：is_folder=1 为目录节点，目录下可再建目录或流程。"""
    __tablename__ = "wb_api_scenarios"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    parent_id = Column(BigInteger, index=True)  # 上级目录（NULL = 根）
    is_folder = Column(Integer, nullable=False, default=0)  # 1 = 目录，0 = 测试流程
    name = Column(String(200), nullable=False)
    description = Column(String(500))
    environment_id = Column(BigInteger)  # 运行环境（wb_api_environments）
    loop_count = Column(Integer, nullable=False, default=1)  # 循环次数
    last_run_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class WbApiScenarioStep(Base):
    """场景步骤：source=api 引用接口管理接口 / source=case 引用用例库接口用例；
    snapshot 为添加时的参数快照，运行按快照执行，不受原接口后续修改影响。"""
    __tablename__ = "wb_api_scenario_steps"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    scenario_id = Column(BigInteger, ForeignKey("wb_api_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(20), nullable=False, default="api")  # api / case
    ref_id = Column(BigInteger, index=True)  # 接口 id 或接口用例 id
    request_id = Column(BigInteger, ForeignKey("wb_api_requests.id", ondelete="CASCADE"), nullable=True)  # 兼容旧数据：source=api 时记录
    snapshot = Column(JSON)  # {name, method, url, headers, params, body_type, body, auth}
    enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())


class WbApiRequestHistory(Base):
    __tablename__ = "wb_api_request_history"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(BigInteger, index=True)
    project_id = Column(BigInteger, nullable=False, index=True)
    environment_id = Column(BigInteger)
    method = Column(String(10), nullable=False)
    url = Column(String(500), nullable=False)
    status_code = Column(Integer)
    duration_ms = Column(Integer)
    success = Column(Integer)
    request_snapshot = Column(LongText)
    response_snapshot = Column(LongText)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_hist_project_time", "project_id", "created_at"),)


class WbExecutionPlan(Base):
    __tablename__ = "wb_execution_plans"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    plan_type = Column(String(20), nullable=False)
    requirement_id = Column(BigInteger, index=True)  # 新建计划时关联的需求（可空）
    status = Column(String(20), nullable=False, default="running")
    executor = Column(String(50))
    total_count = Column(Integer, nullable=False, default=0)
    pass_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    block_count = Column(Integer, nullable=False, default=0)
    skip_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_plan_pts", "project_id", "plan_type", "status"),)


class WbExecutionPlanCase(Base):
    __tablename__ = "wb_execution_plan_cases"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plan_id = Column(BigInteger, ForeignKey("wb_execution_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(BigInteger, ForeignKey("wb_test_cases.id"), nullable=False, index=True)
    result = Column(String(20), nullable=False, default="pending")
    executor = Column(String(50))
    executed_at = Column(DateTime)
    note = Column(String(500))
    # 计划内快照覆盖字段（第 9 项需求）：修改只影响本计划的副本，不动用例库原用例
    title_override = Column(String(200))
    priority_override = Column(String(10))
    precondition_override = Column(Text)
    steps_override = Column(JSON)
    expected_override = Column(Text)
    __table_args__ = (Index("idx_pc_plan_result", "plan_id", "result"),)


class WbExecutionRecord(Base):
    __tablename__ = "wb_execution_records"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plan_case_id = Column(BigInteger, ForeignKey("wb_execution_plan_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(BigInteger, nullable=False, index=True)
    actual_result = Column(Text)
    step_results = Column(JSON)
    duration_ms = Column(Integer)
    bug_id = Column(BigInteger)
    executed_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_rec_case_time", "case_id", "executed_at"),)


class WbBug(Base):
    __tablename__ = "wb_bugs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bug_no = Column(String(50), nullable=False, unique=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(LongText)
    severity = Column(String(20))
    priority = Column(String(10))
    status = Column(String(20), nullable=False, default="active")
    source = Column(String(20), nullable=False, default="manual")
    plan_id = Column(BigInteger, index=True)
    assignee = Column(String(50))
    reporter = Column(String(50))
    found_at = Column(DateTime, default=func.now())
    closed_at = Column(DateTime)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (
        Index("idx_bug_project_status", "project_id", "status"),
        Index("idx_bug_status_severity", "status", "severity"),
    )


class WbBugLink(Base):
    __tablename__ = "wb_bug_links"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bug_id = Column(BigInteger, ForeignKey("wb_bugs.id", ondelete="CASCADE"), nullable=False, index=True)
    link_type = Column(String(20), nullable=False)
    link_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (
        UniqueConstraint("bug_id", "link_type", "link_id", name="uk_bug_type_id"),
        Index("idx_bl_lookup", "link_type", "link_id"),
    )


class WbTestLog(Base):
    __tablename__ = "wb_test_logs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    plan_id = Column(BigInteger, index=True)
    log_type = Column(String(20), nullable=False, default="execution")
    title = Column(String(200), nullable=False)
    content = Column(LongText)
    log_time = Column(DateTime, default=func.now())
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_log_ptt", "project_id", "log_type", "log_time"),)


class WbAttachment(Base):
    __tablename__ = "wb_attachments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    owner_type = Column(String(30), nullable=False)
    owner_id = Column(BigInteger, nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    mime_type = Column(String(100))
    uploaded_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_att_owner", "owner_type", "owner_id"),)


class WbRegressionSet(Base):
    __tablename__ = "wb_regression_sets"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    case_count = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())


class WbRegressionSetItem(Base):
    __tablename__ = "wb_regression_set_items"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    set_id = Column(BigInteger, ForeignKey("wb_regression_sets.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(BigInteger, ForeignKey("wb_test_cases.id"), nullable=False, index=True)
    added_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("set_id", "case_id", name="uk_rgset_case"),)


class WbRegressionRun(Base):
    __tablename__ = "wb_regression_runs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    set_id = Column(BigInteger, ForeignKey("wb_regression_sets.id"), nullable=False, index=True)
    project_id = Column(BigInteger, nullable=False)
    plan_id = Column(BigInteger, ForeignKey("wb_execution_plans.id", ondelete="SET NULL"))
    trigger_type = Column(String(20), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="running")
    pass_rate = Column(Numeric(5, 2))
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    __table_args__ = (Index("idx_rrun_project_time", "project_id", "started_at"),)


class WbReport(Base):
    __tablename__ = "wb_reports"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("wb_projects.id"), nullable=False, index=True)
    plan_id = Column(BigInteger, index=True)
    report_type = Column(String(20), nullable=False, default="iteration")
    title = Column(String(200), nullable=False)
    stats = Column(JSON)
    content = Column(LongText)
    generated_by = Column(String(20), nullable=False, default="ai")
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_report_pt", "project_id", "report_type"),)


class WbSetting(Base):
    __tablename__ = "wb_settings"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    setting_key = Column(String(100), nullable=False, unique=True)
    setting_group = Column(String(50), nullable=False, default="general", index=True)
    setting_value = Column(Text)
    is_secret = Column(Integer, nullable=False, default=0)
    description = Column(String(200))
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class WbAiCallLog(Base):
    __tablename__ = "wb_ai_call_logs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    scene = Column(String(30), nullable=False)
    model_name = Column(String(60), nullable=False)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    duration_ms = Column(Integer)
    success = Column(Integer, nullable=False, default=1)
    error_msg = Column(String(500))
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (Index("idx_ai_scene_time", "scene", "created_at"),)
