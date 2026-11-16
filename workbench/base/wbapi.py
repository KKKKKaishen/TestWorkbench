"""测试深度工作台业务 API（/wb 前缀）。

单机单用户使用，不做鉴权。所有端点读写 Workbench 库的 wb_ 表。
"""
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import wbmodels as wb
from config import get_upload_dir
from models.database import get_db

router = APIRouter(prefix="/wb", tags=["workbench"])

# ---------------- 序列化辅助 ----------------


def row(obj, *fields):
    """按字段白名单输出 dict（JSON 列返回原生 dict/list，FastAPI 自动序列化）。"""
    return {f: getattr(obj, f, None) for f in fields}


def next_no(db: Session, model, column, prefix):
    """生成 {prefix}-0001 式编号，取现有最大序号 +1，冲突重试。"""
    n = db.query(func.count(model.id)).scalar() or 0
    for _ in range(50):
        n += 1
        candidate = f"{prefix}-{n:04d}"
        if not db.query(model.id).filter(column == candidate).first():
            return candidate
    raise HTTPException(500, "编号生成失败")


# ================= 项目 =================


class ProjectBody(BaseModel):
    name: str
    code: str
    description: str | None = None
    owner_name: str | None = None
    status: str = "active"


def _project_stats(db: Session, project_id: int) -> dict:
    cases = dict(
        db.query(wb.WbTestCase.case_type, func.count(wb.WbTestCase.id))
        .filter(wb.WbTestCase.project_id == project_id, wb.WbTestCase.status != "deprecated")
        .group_by(wb.WbTestCase.case_type)
        .all()
    )
    bugs_open = (
        db.query(func.count(wb.WbBug.id))
        .filter(wb.WbBug.project_id == project_id, wb.WbBug.status.in_(["active", "processing", "fixed", "regressed"]))
        .scalar()
    )
    plans = (
        db.query(wb.WbExecutionPlan)
        .filter(wb.WbExecutionPlan.project_id == project_id)
        .order_by(wb.WbExecutionPlan.id.desc())
        .limit(5)
        .all()
    )
    # 最近执行的计数以明细表实时统计（与 /wb/plans 自愈口径一致）
    plan_ids = [p.id for p in plans]
    agg = {}
    if plan_ids:
        rows = (
            db.query(wb.WbExecutionPlanCase.plan_id, wb.WbExecutionPlanCase.result,
                     func.count(wb.WbExecutionPlanCase.id))
            .filter(wb.WbExecutionPlanCase.plan_id.in_(plan_ids))
            .group_by(wb.WbExecutionPlanCase.plan_id, wb.WbExecutionPlanCase.result)
            .all()
        )
        for pid, res, cnt in rows:
            agg.setdefault(pid, {})[res] = cnt
    return {
        "cases": {t: c for t, c in cases.items()},
        "cases_total": sum(cases.values()),
        "bugs_open": bugs_open,
        "recent_plans": [
            {
                "id": p.id,
                "name": p.name,
                "plan_type": p.plan_type,
                "status": p.status,
                "total": sum(agg.get(p.id, {}).values()),
                "pass": agg.get(p.id, {}).get("pass", 0),
                "fail": agg.get(p.id, {}).get("fail", 0),
                "block": agg.get(p.id, {}).get("block", 0),
                "skip": agg.get(p.id, {}).get("skip", 0),
            }
            for p in plans
        ],
    }


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(wb.WbProject).order_by(wb.WbProject.id.desc()).all()
    return [{"id": p.id, "name": p.name, "code": p.code, "description": p.description,
             "status": p.status, "owner_name": p.owner_name,
             "created_at": str(p.created_at or ""),
             "stats": _project_stats(db, p.id)} for p in projects]


@router.post("/projects")
def create_project(body: ProjectBody, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,19}", body.code):
        raise HTTPException(400, "项目代号需为 2~20 位字母开头（字母/数字/下划线），用作编号前缀")
    if db.query(wb.WbProject.id).filter(wb.WbProject.code == body.code).first():
        raise HTTPException(400, f"项目代号 {body.code} 已存在")
    p = wb.WbProject(**body.model_dump())
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.get("/projects/{pid}")
def get_project(pid: int, db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    d = row(p, "id", "name", "code", "description", "status", "owner_name", "created_at", "updated_at")
    d["stats"] = _project_stats(db, pid)
    return d


@router.put("/projects/{pid}")
def update_project(pid: int, body: ProjectBody, db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/projects/{pid}")
def delete_project(pid: int, db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    # 级联清理业务数据（外键未全量声明处手动兜底）
    for m, col in [
        (wb.WbTestCase, wb.WbTestCase.project_id),
        (wb.WbRequirement, wb.WbRequirement.project_id),
        (wb.WbTestPoint, wb.WbTestPoint.project_id),
        (wb.WbApiEnvironment, wb.WbApiEnvironment.project_id),
        (wb.WbApiCollection, wb.WbApiCollection.project_id),
        (wb.WbApiRequest, wb.WbApiRequest.project_id),
        (wb.WbApiRequestHistory, wb.WbApiRequestHistory.project_id),
        (wb.WbExecutionPlan, wb.WbExecutionPlan.project_id),
        (wb.WbBug, wb.WbBug.project_id),
        (wb.WbTestLog, wb.WbTestLog.project_id),
        (wb.WbRegressionSet, wb.WbRegressionSet.project_id),
        (wb.WbReport, wb.WbReport.project_id),
    ]:
        db.query(m).filter(col == pid).delete(synchronize_session=False)
    db.delete(p)
    db.commit()
    return {"ok": True}


# ================= 需求 / 测试点 =================


class RequirementBody(BaseModel):
    project_id: int | None = None  # 更新时可缺省（部分更新），创建时强校验
    title: str | None = None  # 更新时可缺省（部分更新），创建时强校验
    content: str | None = None
    source_type: str = "manual"
    version: str = "v1"
    status: str | None = None  # pending/split/covered/passed，需求管理页可直接改


@router.get("/requirements")
def list_requirements(project_id: int | None = None, keyword: str | None = None,
                      status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(wb.WbRequirement)
    if project_id:
        q = q.filter(wb.WbRequirement.project_id == project_id)
    if status:
        q = q.filter(wb.WbRequirement.status == status)
    if keyword:
        q = q.filter(wb.WbRequirement.title.like(f"%{keyword}%"))
    reqs = q.order_by(wb.WbRequirement.id.desc()).limit(500).all()
    tp_counts = dict(
        db.query(wb.WbTestPoint.requirement_id, func.count(wb.WbTestPoint.id))
        .group_by(wb.WbTestPoint.requirement_id)
        .all()
    )
    pname = {p.id: p.name for p in db.query(wb.WbProject).all()}
    out = []
    for r in reqs:
        d = row(r, "id", "project_id", "title", "content", "source_type", "version", "status", "created_at", "updated_at")
        d["test_point_count"] = tp_counts.get(r.id, 0)
        d["project_name"] = pname.get(r.project_id, "")
        out.append(d)
    return out


@router.post("/requirements")
def create_requirement(body: RequirementBody, db: Session = Depends(get_db)):
    if not body.project_id:
        raise HTTPException(400, "请选择所属项目")
    if not body.title or not body.title.strip():
        raise HTTPException(400, "需求标题不能为空")
    d = body.model_dump()
    if not d.get("status"):
        d.pop("status", None)  # 创建未指定状态时走数据库默认值
    r = wb.WbRequirement(**d)
    db.add(r)
    db.commit()
    return {"id": r.id}


@router.put("/requirements/{rid}")
def update_requirement(rid: int, body: RequirementBody, db: Session = Depends(get_db)):
    r = db.get(wb.WbRequirement, rid)
    if not r:
        raise HTTPException(404, "需求不存在")
    # 部分更新：只覆盖显式提供的字段（支持单独修改状态/换项目等）
    d = body.model_dump(exclude_unset=True)
    if not d:
        raise HTTPException(400, "未提供任何修改字段")
    for k, v in d.items():
        setattr(r, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/requirements/{rid}")
def delete_requirement(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbRequirement, rid)
    if not r:
        raise HTTPException(404, "需求不存在")
    db.query(wb.WbTestPoint).filter(wb.WbTestPoint.requirement_id == rid).delete(synchronize_session=False)
    db.delete(r)
    db.commit()
    return {"ok": True}


class TestPointBody(BaseModel):
    title: str | None = None  # 更新时可缺省（部分更新），创建时强校验
    description: str | None = None
    priority: str | None = None
    status: str | None = None  # pending/covered


@router.get("/requirements/{rid}/test-points")
def list_test_points(rid: int, db: Session = Depends(get_db)):
    tps = db.query(wb.WbTestPoint).filter(wb.WbTestPoint.requirement_id == rid).order_by(wb.WbTestPoint.id).all()
    return [row(t, "id", "requirement_id", "project_id", "title", "description", "priority", "status") for t in tps]


@router.post("/requirements/{rid}/test-points")
def add_test_point(rid: int, body: TestPointBody, db: Session = Depends(get_db)):
    r = db.get(wb.WbRequirement, rid)
    if not r:
        raise HTTPException(404, "需求不存在")
    if not body.title or not body.title.strip():
        raise HTTPException(400, "测试点内容不能为空")
    d = body.model_dump()
    if not d.get("status"):
        d.pop("status", None)  # 创建未指定状态时走数据库默认值
    t = wb.WbTestPoint(requirement_id=rid, project_id=r.project_id, **d)
    db.add(t)
    r.status = "split"
    db.commit()
    return {"id": t.id}


@router.put("/test-points/{tid}")
def update_test_point(tid: int, body: TestPointBody, db: Session = Depends(get_db)):
    t = db.get(wb.WbTestPoint, tid)
    if not t:
        raise HTTPException(404, "测试点不存在")
    # 部分更新：只覆盖显式提供的字段
    d = body.model_dump(exclude_unset=True)
    if not d:
        raise HTTPException(400, "未提供任何修改字段")
    for k, v in d.items():
        setattr(t, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/test-points/{tid}")
def delete_test_point(tid: int, db: Session = Depends(get_db)):
    t = db.get(wb.WbTestPoint, tid)
    if not t:
        raise HTTPException(404, "测试点不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ================= 用例库 =================


class CaseBody(BaseModel):
    project_id: int
    title: str | None = None  # 更新时可缺省（部分更新），创建时强校验
    case_type: str = "functional"
    module_path: str | None = None
    priority: str | None = None
    precondition: str | None = None
    steps: list | None = None
    expected: str | None = None
    tags: list | None = None
    test_point_id: int | None = None
    gen_source: str = "manual"
    # 接口用例参数快照（case_type=api 时使用）：保存用例不影响接口管理原接口
    api_snapshot: dict | None = None


@router.get("/cases")
def list_cases(
    project_id: int,
    case_type: str | None = None,
    module_path: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(wb.WbTestCase).filter(wb.WbTestCase.project_id == project_id)
    if case_type:
        q = q.filter(wb.WbTestCase.case_type == case_type)
    if module_path:
        q = q.filter(wb.WbTestCase.module_path == module_path)
    if status:
        q = q.filter(wb.WbTestCase.status == status)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(wb.WbTestCase.title.like(like), wb.WbTestCase.case_no.like(like)))
    total = q.count()
    cases = q.order_by(wb.WbTestCase.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "items": [
        row(c, "id", "project_id", "test_point_id", "case_no", "title", "case_type", "module_path",
            "priority", "precondition", "steps", "expected", "tags", "status", "gen_source", "version",
            "api_snapshot", "created_at", "updated_at")
        for c in cases
    ]}


@router.get("/cases/modules")
def list_modules(project_id: int, db: Session = Depends(get_db)):
    """模块树叶子路径 + 各路径用例数（前端按 / 分层构建树）。"""
    rows = (
        db.query(wb.WbTestCase.module_path, func.count(wb.WbTestCase.id))
        .filter(wb.WbTestCase.project_id == project_id, wb.WbTestCase.status != "deprecated")
        .group_by(wb.WbTestCase.module_path)
        .all()
    )
    return [{"module_path": m or "", "count": c} for m, c in rows]


@router.post("/cases")
def create_case(body: CaseBody, db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, body.project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    if not body.title or not body.title.strip():
        raise HTTPException(400, "用例标题不能为空")
    d = body.model_dump()
    d["case_no"] = next_no(db, wb.WbTestCase, wb.WbTestCase.case_no, f"{p.code}-TC")
    c = wb.WbTestCase(**d)
    db.add(c)
    if body.test_point_id:
        tp = db.get(wb.WbTestPoint, body.test_point_id)
        if tp:
            if tp.project_id != body.project_id:
                raise HTTPException(400, "测试点与用例不属于同一项目，无法关联")
            tp.status = "covered"
            r = db.get(wb.WbRequirement, tp.requirement_id)
            if r and r.status == "split":
                r.status = "covered"
    db.commit()
    return {"id": c.id, "case_no": c.case_no}


@router.get("/cases/{cid}")
def get_case(cid: int, db: Session = Depends(get_db)):
    c = db.get(wb.WbTestCase, cid)
    if not c:
        raise HTTPException(404, "用例不存在")
    return row(c, "id", "project_id", "test_point_id", "case_no", "title", "case_type", "module_path",
               "priority", "precondition", "steps", "expected", "tags", "status", "gen_source", "version",
               "api_snapshot", "created_at", "updated_at")


@router.put("/cases/{cid}")
def update_case(cid: int, body: CaseBody, db: Session = Depends(get_db)):
    c = db.get(wb.WbTestCase, cid)
    if not c:
        raise HTTPException(404, "用例不存在")
    # 部分更新：只覆盖请求中显式提供的字段，避免默认值误清空 case_type/tags 等
    d = body.model_dump(exclude_unset=True)
    d.pop("project_id", None)
    if not d:
        raise HTTPException(400, "未提供任何修改字段")
    c.version += 1
    for k, v in d.items():
        setattr(c, k, v)
    db.commit()
    return {"ok": True}


# ---------------- P2 接口测试（EX-A1 调试 / EX-A2 转用例 / 环境变量） ----------------

def _sub_vars(text, variables: dict):
    """替换 {{var}} 占位符。"""
    if not text:
        return text or ""
    for k, v in variables.items():
        text = text.replace("{{" + str(k) + "}}", str(v))
    return text


def _env_variables(db: Session, env_id):
    """取环境变量 dict 与环境名。"""
    if not env_id:
        return {}, None
    e = db.get(wb.WbApiEnvironment, env_id)
    if not e:
        raise HTTPException(404, "环境不存在")
    variables = {}
    for v in (e.variables or []):
        if isinstance(v, dict) and v.get("key"):
            variables[v["key"]] = v.get("value", "")
    return variables, e.name


def _json_path_get(obj, path):
    """按点路径取嵌套值，支持数组下标：data.token / data.list.0.id。"""
    cur = obj
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def _apply_extracts(extracts, res):
    """EX-A4 变量提取：按步骤规则从响应提取变量。

    规则结构：[{name: 变量名, source: body_json|header|status, path: 点路径或 Header 名}]
    """
    out = {}
    parsed_body = None
    body_parsed = False
    for it in (extracts or []):
        if not isinstance(it, dict) or not (it.get("name") or "").strip():
            continue
        name = it["name"].strip()
        src = it.get("source") or "body_json"
        path = (it.get("path") or "").strip()
        val = None
        if src == "status":
            val = res.get("status")
        elif src == "header":
            for k, v in (res.get("headers") or {}).items():
                if str(k).lower() == path.lower():
                    val = v
                    break
        else:  # body_json
            if not body_parsed:
                try:
                    parsed_body = json.loads(res.get("body") or "")
                except Exception:
                    parsed_body = None
                body_parsed = True
            if parsed_body is not None and path:
                val = _json_path_get(parsed_body, path)
        if val is not None:
            out[name] = str(val)
    return out


# ---- 环境变量 ----
class EnvBody(BaseModel):
    project_id: int
    name: str
    base_url: str | None = None
    variables: list | None = None  # [{key, value}]
    is_default: int = 0


@router.get("/api-envs")
def list_api_envs(project_id: int, db: Session = Depends(get_db)):
    rows = db.query(wb.WbApiEnvironment).filter_by(project_id=project_id).order_by(wb.WbApiEnvironment.id).all()
    return [row(r, "id", "project_id", "name", "base_url", "variables", "is_default") for r in rows]


@router.post("/api-envs")
def create_api_env(body: EnvBody, db: Session = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise HTTPException(400, "环境名称不能为空")
    if body.is_default:
        db.query(wb.WbApiEnvironment).filter_by(project_id=body.project_id).update({"is_default": 0})
    e = wb.WbApiEnvironment(**body.model_dump())
    db.add(e)
    db.commit()
    return {"id": e.id}


@router.put("/api-envs/{eid}")
def update_api_env(eid: int, body: EnvBody, db: Session = Depends(get_db)):
    e = db.get(wb.WbApiEnvironment, eid)
    if not e:
        raise HTTPException(404, "环境不存在")
    d = body.model_dump(exclude_unset=True)
    d.pop("project_id", None)
    if d.get("is_default"):
        db.query(wb.WbApiEnvironment).filter(
            wb.WbApiEnvironment.project_id == e.project_id, wb.WbApiEnvironment.id != eid
        ).update({"is_default": 0})
    for k, v in d.items():
        setattr(e, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api-envs/{eid}")
def delete_api_env(eid: int, db: Session = Depends(get_db)):
    e = db.get(wb.WbApiEnvironment, eid)
    if not e:
        raise HTTPException(404, "环境不存在")
    db.delete(e)
    db.commit()
    return {"ok": True}


# ---- Collections 与请求 ----
@router.get("/api-collections")
def list_api_collections(project_id: int, db: Session = Depends(get_db)):
    cols = (
        db.query(wb.WbApiCollection)
        .filter_by(project_id=project_id)
        .order_by(wb.WbApiCollection.sort_order, wb.WbApiCollection.id)
        .all()
    )
    reqs = (
        db.query(wb.WbApiRequest)
        .filter_by(project_id=project_id)
        .order_by(wb.WbApiRequest.id.desc())
        .all()
    )
    bycol = {}
    for r in reqs:
        bycol.setdefault(r.collection_id, []).append(
            row(r, "id", "collection_id", "name", "method", "url", "last_status_code", "updated_at")
        )
    collections = []
    for c in cols:
        d = row(c, "id", "parent_id", "name", "sort_order")
        d["requests"] = bycol.get(c.id, [])
        collections.append(d)
    return {"collections": collections, "orphans": bycol.get(None, [])}


class CollectionBody(BaseModel):
    project_id: int
    name: str
    parent_id: int | None = None


@router.post("/api-collections")
def create_api_collection(body: CollectionBody, db: Session = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise HTTPException(400, "集合名称不能为空")
    c = wb.WbApiCollection(**body.model_dump())
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.put("/api-collections/{cid}")
def update_api_collection(cid: int, body: CollectionBody, db: Session = Depends(get_db)):
    c = db.get(wb.WbApiCollection, cid)
    if not c:
        raise HTTPException(404, "集合不存在")
    d = body.model_dump(exclude_unset=True)
    d.pop("project_id", None)
    for k, v in d.items():
        setattr(c, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api-collections/{cid}")
def delete_api_collection(cid: int, db: Session = Depends(get_db)):
    c = db.get(wb.WbApiCollection, cid)
    if not c:
        raise HTTPException(404, "集合不存在")
    # 子目录上移一级（挂到被删集合的父级），避免 parent_id 悬空
    db.query(wb.WbApiCollection).filter(wb.WbApiCollection.parent_id == cid).update({"parent_id": c.parent_id})
    db.query(wb.WbApiRequest).filter(wb.WbApiRequest.collection_id == cid).update({"collection_id": None})
    db.delete(c)
    db.commit()
    return {"ok": True}


class ApiRequestBody(BaseModel):
    project_id: int
    collection_id: int | None = None
    name: str
    method: str = "GET"
    url: str
    headers: list | None = None
    params: list | None = None  # [{key, value, type, desc}]
    description: str | None = None
    body_type: str | None = None  # none/json/form/raw
    body: str | None = None
    auth: dict | None = None


@router.post("/api-requests")
def create_api_request(body: ApiRequestBody, db: Session = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise HTTPException(400, "请求名称不能为空")
    r = wb.WbApiRequest(**body.model_dump())
    db.add(r)
    db.commit()
    return {"id": r.id}


@router.get("/api-requests/{rid}")
def get_api_request(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbApiRequest, rid)
    if not r:
        raise HTTPException(404, "接口请求不存在")
    return row(r, "id", "collection_id", "project_id", "name", "method", "url",
               "headers", "params", "description", "body_type", "body", "auth",
               "last_status_code", "created_at", "updated_at")


@router.put("/api-requests/{rid}")
def update_api_request(rid: int, body: ApiRequestBody, db: Session = Depends(get_db)):
    r = db.get(wb.WbApiRequest, rid)
    if not r:
        raise HTTPException(404, "接口请求不存在")
    d = body.model_dump(exclude_unset=True)
    d.pop("project_id", None)
    for k, v in d.items():
        setattr(r, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api-requests/{rid}")
def delete_api_request(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbApiRequest, rid)
    if not r:
        raise HTTPException(404, "接口请求不存在")
    db.query(wb.WbApiRequestHistory).filter_by(request_id=rid).delete()
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---- 调试转用例（EX-A2） ----
@router.post("/api-requests/{rid}/to-case")
def api_request_to_case(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbApiRequest, rid)
    if not r:
        raise HTTPException(404, "接口请求不存在")
    p = db.get(wb.WbProject, r.project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    step_action = f"{r.method} {r.url}"
    expected = f"响应状态码 {r.last_status_code or 200}，返回符合预期"
    if r.body:
        step_action += f"（Body: {str(r.body)[:200]}）"
    c = wb.WbTestCase(
        case_no=next_no(db, wb.WbTestCase, wb.WbTestCase.case_no, f"{p.code}-TC"),
        project_id=r.project_id,
        title=r.name,
        case_type="api",
        priority="P1",
        precondition=f"接口可访问：{r.url}",
        steps=[{"step": 1, "action": step_action, "expected": expected}],
        expected=expected,
        gen_source="api_debug",
        api_request_id=r.id,
        status="active",
        # 参数快照：用例归档后与接口管理原接口解耦
        api_snapshot={
            "request_id": r.id, "name": r.name, "method": r.method, "url": r.url,
            "headers": r.headers, "params": r.params, "body_type": r.body_type,
            "body": r.body, "auth": r.auth, "description": r.description,
        },
    )
    db.add(c)
    db.commit()
    return {"id": c.id, "case_no": c.case_no}


# ---- 历史 ----
@router.get("/api-history")
def list_api_history(project_id: int, size: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(wb.WbApiRequestHistory)
        .filter_by(project_id=project_id)
        .order_by(wb.WbApiRequestHistory.id.desc())
        .limit(min(size, 200))
        .all()
    )
    out = []
    for h in rows:
        d = row(h, "id", "request_id", "environment_id", "method", "url", "status_code", "duration_ms", "success", "created_at")
        snap = {}
        try:
            snap = json.loads(h.response_snapshot or "{}")
        except Exception:
            pass
        d["error"] = snap.get("error")
        out.append(d)
    return out


@router.get("/api-history/{hid}")
def get_api_history(hid: int, db: Session = Depends(get_db)):
    h = db.get(wb.WbApiRequestHistory, hid)
    if not h:
        raise HTTPException(404, "历史记录不存在")
    req = {}
    resp = {}
    try:
        req = json.loads(h.request_snapshot or "{}")
        resp = json.loads(h.response_snapshot or "{}")
    except Exception:
        pass
    return {
        **row(h, "id", "method", "url", "status_code", "duration_ms", "success", "created_at"),
        "request": req, "response": resp,
    }


@router.delete("/api-history")
def clear_api_history(project_id: int, db: Session = Depends(get_db)):
    n = db.query(wb.WbApiRequestHistory).filter_by(project_id=project_id).delete()
    db.commit()
    return {"ok": True, "deleted": n}


# ---- 发送请求（后端代理，规避浏览器 CORS；EX-A1/A4） ----
class SendBody(BaseModel):
    project_id: int
    environment_id: int | None = None
    request_id: int | None = None
    method: str = "GET"
    url: str
    headers: list | None = None       # [{key, value, enabled}]
    query_params: list | None = None  # [{key, value, enabled}]
    body_type: str | None = None      # none/json/form/raw
    body: str | None = None
    auth: dict | None = None          # {type: bearer/basic, token / username, password}


def _do_send(db: Session, *, project_id, environment_id, request_id,
             method, url, headers, query_params, body_type, body, auth,
             write_history=True, extra_vars=None):
    """发送核心（供调试端点与场景批量执行共用）：变量替换/拼接/认证/执行/历史落库。

    extra_vars：运行级变量池（EX-A4 流程执行时前序步骤提取的变量），优先级高于环境变量。
    """
    import base64
    import httpx
    from urllib.parse import quote

    variables, env_name = _env_variables(db, environment_id)
    if extra_vars:
        variables = {**variables, **extra_vars}
    # 环境变量替换 + Base URL 拼接（/ 开头时）
    if environment_id:
        e = db.get(wb.WbApiEnvironment, environment_id)
        if e and e.base_url and url.startswith("/"):
            url = e.base_url.rstrip("/") + url
        url = _sub_vars(url, variables)
    else:
        url = _sub_vars(url, variables)
    if not re.match(r"^https?://", url):
        return {"error": "URL 必须以 http:// 或 https:// 开头（或选择已配置 Base URL 的环境）", "status": None,
                "duration_ms": 0, "size": 0, "headers": {}, "body": "", "url": url, "history_id": None}

    hdrs = {}
    for h in (headers or []):
        if isinstance(h, dict) and h.get("key") and h.get("enabled", 1):
            hdrs[h["key"].strip()] = _sub_vars(h.get("value") or "", variables)
    if auth:
        if auth.get("type") == "bearer" and auth.get("token"):
            hdrs["Authorization"] = "Bearer " + _sub_vars(auth["token"], variables)
        elif auth.get("type") == "basic" and auth.get("username"):
            raw = f"{auth.get('username', '')}:{auth.get('password', '')}"
            hdrs["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()

    qp = [
        (_sub_vars(p["key"], variables), _sub_vars(p.get("value") or "", variables))
        for p in (query_params or [])
        if isinstance(p, dict) and p.get("key") and p.get("enabled", 1)
    ]
    if qp:
        url += ("&" if "?" in url else "?") + "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in qp
        )

    payload = None
    if body_type == "json" and body:
        hdrs.setdefault("Content-Type", "application/json")
        payload = _sub_vars(body, variables)
    elif body_type == "raw" and body:
        payload = _sub_vars(body, variables)
    elif body_type == "form" and body:
        try:
            pairs = json.loads(body)
            if isinstance(pairs, list):
                payload = {it["key"]: it.get("value", "") for it in pairs if it.get("key")}
            else:
                payload = _sub_vars(body, variables)
        except Exception:
            payload = _sub_vars(body, variables)

    method = (method or "GET").upper()
    t0 = time.perf_counter()
    status = resp_headers = resp_text = error = None
    try:
        with httpx.Client(timeout=30, verify=False, follow_redirects=True) as client:
            if method in ("GET", "HEAD") or payload is None:
                r = client.request(method, url, headers=hdrs)
            elif isinstance(payload, dict):
                r = client.request(method, url, headers=hdrs, data=payload)
            else:
                r = client.request(method, url, headers=hdrs, content=payload)
        status = r.status_code
        resp_headers = dict(r.headers)
        resp_text = r.text
    except Exception as ex:
        error = f"{type(ex).__name__}: {ex}"
    duration_ms = int((time.perf_counter() - t0) * 1000)

    size = len(resp_text.encode("utf-8", "ignore")) if resp_text else 0
    history_id = None
    if write_history:
        req_snap = json.dumps(
            {"method": method, "url": url, "headers": hdrs, "body_type": body_type,
             "body": body, "auth": auth, "environment": env_name},
            ensure_ascii=False,
        )
        resp_snap = json.dumps(
            {"status": status, "duration_ms": duration_ms, "size": size,
             "headers": resp_headers, "body": (resp_text or "")[:100000], "error": error},
            ensure_ascii=False,
        )
        h = wb.WbApiRequestHistory(
            request_id=request_id, project_id=project_id, environment_id=environment_id,
            method=method, url=url, status_code=status, duration_ms=duration_ms,
            success=1 if status and 200 <= status < 400 and not error else 0,
            request_snapshot=req_snap, response_snapshot=resp_snap,
        )
        db.add(h)
        if request_id:
            rq = db.get(wb.WbApiRequest, request_id)
            if rq:
                rq.last_status_code = status
        db.commit()
        history_id = h.id
    return {
        "history_id": history_id, "url": url, "status": status, "duration_ms": duration_ms,
        "size": size, "headers": resp_headers, "body": resp_text, "error": error,
    }


@router.post("/api-send")
def api_send(body: SendBody, db: Session = Depends(get_db)):
    return _do_send(
        db, project_id=body.project_id, environment_id=body.environment_id,
        request_id=body.request_id, method=body.method, url=body.url,
        headers=body.headers, query_params=body.query_params,
        body_type=body.body_type, body=body.body, auth=body.auth,
    )


# ---------------- 接口测试场景（Apifox 场景式编排） ----------------

class ScenarioBody(BaseModel):
    project_id: int
    name: str
    parent_id: int | None = None
    is_folder: int = 0  # 1 = 目录节点
    description: str | None = None
    environment_id: int | None = None
    loop_count: int = 1


@router.get("/api-scenarios")
def list_api_scenarios(project_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(wb.WbApiScenario)
        .filter_by(project_id=project_id)
        .order_by(wb.WbApiScenario.id.desc())
        .all()
    )
    step_counts = dict(
        db.query(wb.WbApiScenarioStep.scenario_id, func.count(wb.WbApiScenarioStep.id))
        .group_by(wb.WbApiScenarioStep.scenario_id)
        .all()
    )
    env_names = {e.id: e.name for e in db.query(wb.WbApiEnvironment).filter_by(project_id=project_id).all()}
    out = []
    for s in rows:
        d = row(s, "id", "parent_id", "is_folder", "name", "description", "environment_id", "loop_count", "last_run_at", "created_at")
        d["step_count"] = 0 if s.is_folder else step_counts.get(s.id, 0)
        d["environment_name"] = env_names.get(s.environment_id)
        out.append(d)
    return out


@router.post("/api-scenarios")
def create_api_scenario(body: ScenarioBody, db: Session = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise HTTPException(400, "场景名称不能为空")
    s = wb.WbApiScenario(**body.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.get("/api-scenarios/{sid}")
def get_api_scenario(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    steps = (
        db.query(wb.WbApiScenarioStep)
        .filter_by(scenario_id=sid)
        .order_by(wb.WbApiScenarioStep.sort_order, wb.WbApiScenarioStep.id)
        .all()
    )
    req_ids = [st.request_id for st in steps if st.request_id]
    reqs = {r.id: r for r in db.query(wb.WbApiRequest).filter(wb.WbApiRequest.id.in_(req_ids)).all()} if req_ids else {}
    step_out = []
    for st in steps:
        snap = st.snapshot or {}
        r = reqs.get(st.request_id)
        # 展示信息优先取快照；旧数据（无快照）回退到实时接口
        step_out.append({
            "id": st.id, "source": st.source or "api", "ref_id": st.ref_id,
            "request_id": st.request_id, "enabled": st.enabled, "sort_order": st.sort_order,
            "name": snap.get("name") or (r.name if r else "(接口已删除)"),
            "method": snap.get("method") or (r.method if r else "GET"),
            "url": snap.get("url") or (r.url if r else ""),
            "last_status_code": r.last_status_code if r else None,
            "snapshot": st.snapshot,
            "missing": not snap and r is None,
        })
    d = row(s, "id", "project_id", "parent_id", "is_folder", "name", "description", "environment_id", "loop_count", "last_run_at")
    d["steps"] = step_out
    return d


@router.put("/api-scenarios/{sid}")
def update_api_scenario(sid: int, body: ScenarioBody, db: Session = Depends(get_db)):
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    d = body.model_dump(exclude_unset=True)
    d.pop("project_id", None)
    for k, v in d.items():
        setattr(s, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/api-scenarios/{sid}")
def delete_api_scenario(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    # 子目录上移一级（挂到被删节点的父级），避免 parent_id 悬空
    db.query(wb.WbApiScenario).filter(wb.WbApiScenario.parent_id == sid).update({"parent_id": s.parent_id})
    db.query(wb.WbApiScenarioStep).filter_by(scenario_id=sid).delete()
    db.delete(s)
    db.commit()
    return {"ok": True}


def _build_step_snapshot(db: Session, source: str, ref_id: int):
    """按来源抓取接口参数快照：api=接口管理实时配置；case=用例库接口用例快照。"""
    if source == "api":
        r = db.get(wb.WbApiRequest, ref_id)
        if not r:
            raise HTTPException(404, "接口不存在")
        return r, {
            "name": r.name, "method": r.method, "url": r.url,
            "headers": r.headers, "params": r.params, "body_type": r.body_type,
            "body": r.body, "auth": r.auth,
        }
    c = db.get(wb.WbTestCase, ref_id)
    if not c or c.case_type != "api":
        raise HTTPException(404, "接口用例不存在")
    snap = c.api_snapshot or {}
    if not snap.get("url"):
        # 存量用例兜底：无快照但关联了接口 -> 按接口当前配置生成快照
        if c.api_request_id:
            r = db.get(wb.WbApiRequest, c.api_request_id)
            if r:
                snap = {
                    "name": r.name, "method": r.method, "url": r.url,
                    "headers": r.headers, "params": r.params, "body_type": r.body_type,
                    "body": r.body, "auth": r.auth,
                }
        if not snap.get("url"):
            raise HTTPException(400, f"用例「{c.title}」缺少接口参数快照，请到用例库编辑该用例补全接口参数")
    return c, {
        "name": c.title, "method": snap.get("method") or "GET", "url": snap.get("url"),
        "headers": snap.get("headers"), "params": snap.get("params"),
        "body_type": snap.get("body_type"), "body": snap.get("body"), "auth": snap.get("auth"),
    }


@router.post("/api-scenarios/{sid}/steps")
def add_scenario_step(sid: int, body: dict, db: Session = Depends(get_db)):
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    source = body.get("source") or "api"
    rid = body.get("ref_id") or body.get("request_id")  # request_id 兼容旧前端
    if source not in ("api", "case"):
        raise HTTPException(400, "步骤来源非法")
    ref, snapshot = _build_step_snapshot(db, source, rid)
    # 同项目校验（case 已带 project_id 字段）
    ref_project = ref.project_id if hasattr(ref, "project_id") else None
    if ref_project and ref_project != s.project_id:
        raise HTTPException(400, "所选对象与场景不属于同一项目")
    exists = db.query(wb.WbApiScenarioStep).filter_by(scenario_id=sid, source=source, ref_id=rid).first()
    if exists:
        return {"id": exists.id, "duplicate": True}
    max_order = db.query(func.max(wb.WbApiScenarioStep.sort_order)).filter_by(scenario_id=sid).scalar() or 0
    st = wb.WbApiScenarioStep(
        scenario_id=sid, source=source, ref_id=rid,
        request_id=rid if source == "api" else None,
        snapshot=snapshot, sort_order=max_order + 1,
    )
    db.add(st)
    db.commit()
    return {"id": st.id}


@router.post("/api-scenarios/{sid}/steps/batch")
def add_scenario_steps_batch(sid: int, body: dict, db: Session = Depends(get_db)):
    """批量添加步骤（勾选后一次加入）：逐项处理，互不影响。"""
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    items = body.get("items") or []
    if not items:
        raise HTTPException(400, "未选择任何步骤")
    results = {"added": [], "skipped": [], "failed": []}
    order = db.query(func.max(wb.WbApiScenarioStep.sort_order)).filter_by(scenario_id=sid).scalar() or 0
    for it in items:
        source = it.get("source") or "api"
        rid = it.get("ref_id")
        try:
            if source not in ("api", "case"):
                raise HTTPException(400, "步骤来源非法")
            ref, snapshot = _build_step_snapshot(db, source, rid)
            ref_project = ref.project_id if hasattr(ref, "project_id") else None
            if ref_project and ref_project != s.project_id:
                raise HTTPException(400, "所选对象与场景不属于同一项目")
            exists = db.query(wb.WbApiScenarioStep).filter_by(scenario_id=sid, source=source, ref_id=rid).first()
            if exists:
                results["skipped"].append({"ref_id": rid, "name": snapshot["name"]})
                continue
            order += 1
            db.add(wb.WbApiScenarioStep(
                scenario_id=sid, source=source, ref_id=rid,
                request_id=rid if source == "api" else None,
                snapshot=snapshot, sort_order=order,
            ))
            results["added"].append({"ref_id": rid, "name": snapshot["name"]})
        except HTTPException as e:
            results["failed"].append({"ref_id": rid, "name": it.get("name") or str(rid), "error": e.detail})
        except Exception as e:
            results["failed"].append({"ref_id": rid, "name": it.get("name") or str(rid), "error": str(e)})
    db.commit()
    return results


@router.put("/api-scenarios/{sid}/steps/{step_id}")
def update_scenario_step(sid: int, step_id: int, body: dict, db: Session = Depends(get_db)):
    st = db.get(wb.WbApiScenarioStep, step_id)
    if not st or st.scenario_id != sid:
        raise HTTPException(404, "步骤不存在")
    if "enabled" in body:
        st.enabled = 1 if body["enabled"] else 0
    if "sort_order" in body:
        st.sort_order = body["sort_order"]
    # 步骤级快照编辑：只改本步骤的副本，不影响原用例/接口管理的配置
    if "snapshot" in body and isinstance(body["snapshot"], dict):
        snap = body["snapshot"]
        if not (snap.get("url") or "").strip():
            raise HTTPException(400, "接口 URL 不能为空")
        st.snapshot = {
            "name": snap.get("name") or (st.snapshot or {}).get("name") or "未命名步骤",
            "method": snap.get("method") or "GET",
            "url": snap["url"].strip(),
            "headers": snap.get("headers") or [],
            "params": snap.get("params") or [],
            "body_type": snap.get("body_type") or "none",
            "body": snap.get("body"),
            "auth": snap.get("auth"),
            "extracts": [e for e in (snap.get("extracts") or [])
                         if isinstance(e, dict) and (e.get("name") or "").strip()],
        }
    db.commit()
    return {"ok": True}


@router.delete("/api-scenarios/{sid}/steps/{step_id}")
def remove_scenario_step(sid: int, step_id: int, db: Session = Depends(get_db)):
    st = db.get(wb.WbApiScenarioStep, step_id)
    if not st or st.scenario_id != sid:
        raise HTTPException(404, "步骤不存在")
    db.delete(st)
    db.commit()
    return {"ok": True}


@router.post("/api-scenarios/{sid}/run")
def run_api_scenario(sid: int, db: Session = Depends(get_db)):
    """顺序执行场景全部启用步骤（循环 loop_count 次），逐步返回结果。"""
    s = db.get(wb.WbApiScenario, sid)
    if not s:
        raise HTTPException(404, "场景不存在")
    steps = (
        db.query(wb.WbApiScenarioStep)
        .filter_by(scenario_id=sid, enabled=1)
        .order_by(wb.WbApiScenarioStep.sort_order, wb.WbApiScenarioStep.id)
        .all()
    )
    if not steps:
        raise HTTPException(400, "场景中没有启用中的步骤")
    loops = max(1, min(s.loop_count or 1, 20))
    results = []
    run_vars = {}   # EX-A4 运行级变量池：跨步骤、跨循环下传（如登录 token 只提取一次）
    t0 = time.perf_counter()
    for loop in range(1, loops + 1):
        for st in steps:
            snap = st.snapshot or {}
            if snap.get("url"):
                # 按添加时的快照执行，不受原接口后续修改影响
                res = _do_send(
                    db, project_id=s.project_id, environment_id=s.environment_id,
                    request_id=st.request_id, method=snap.get("method") or "GET",
                    url=snap.get("url"), headers=snap.get("headers"),
                    query_params=snap.get("params"), body_type=snap.get("body_type"),
                    body=snap.get("body"), auth=snap.get("auth"),
                    extra_vars=run_vars,
                )
                name, method = snap.get("name") or "(未命名)", snap.get("method") or "GET"
            else:
                # 旧数据兜底：无快照则回退到实时接口配置
                r = db.get(wb.WbApiRequest, st.request_id) if st.request_id else None
                if not r:
                    results.append({"step_id": st.id, "name": "(接口已删除)", "method": "-", "url": "",
                                    "status": None, "error": "接口已被删除", "duration_ms": 0, "loop": loop})
                    continue
                res = _do_send(
                    db, project_id=s.project_id, environment_id=s.environment_id,
                    request_id=r.id, method=r.method, url=r.url,
                    headers=r.headers, query_params=r.params, body_type=r.body_type,
                    body=r.body, auth=r.auth,
                    extra_vars=run_vars,
                )
                name, method = r.name, r.method
            # EX-A4：按本步骤提取规则抽取变量，下传后续步骤
            extracted = _apply_extracts(snap.get("extracts"), res)
            if extracted:
                run_vars.update(extracted)
            results.append({
                "step_id": st.id, "name": name, "method": method, "url": res["url"],
                "status": res["status"], "duration_ms": res["duration_ms"],
                "size": res["size"], "error": res["error"], "loop": loop,
                "history_id": res["history_id"],
                "extracted": extracted,
            })
    total_ms = int((time.perf_counter() - t0) * 1000)
    ok_count = sum(1 for x in results if x["status"] and 200 <= x["status"] < 400 and not x["error"])
    s.last_run_at = func.now()
    db.commit()
    return {"total": len(results), "passed": ok_count,
            "failed": len(results) - ok_count, "total_ms": total_ms, "results": results}


@router.post("/cases/{cid}/deprecate")
def deprecate_case(cid: int, db: Session = Depends(get_db)):
    c = db.get(wb.WbTestCase, cid)
    if not c:
        raise HTTPException(404, "用例不存在")
    c.status = "deprecated"
    db.commit()
    return {"ok": True}


class MoveBody(BaseModel):
    case_ids: list[int]
    project_id: int


@router.post("/cases/batch-move")
def batch_move_cases(body: MoveBody, db: Session = Depends(get_db)):
    """用例跨项目移动：不改变用例类型；编号按目标项目重新生成；解除原项目测试点关联。"""
    target = db.get(wb.WbProject, body.project_id)
    if not target:
        raise HTTPException(404, "目标项目不存在")
    if not body.case_ids:
        raise HTTPException(400, "未选择用例")
    cases = db.query(wb.WbTestCase).filter(wb.WbTestCase.id.in_(body.case_ids)).all()
    if not cases:
        raise HTTPException(404, "用例不存在")
    moved, skipped = 0, []
    for c in cases:
        if c.project_id == body.project_id:
            skipped.append(c.case_no)
            continue
        c.project_id = body.project_id
        c.case_no = next_no(db, wb.WbTestCase, wb.WbTestCase.case_no, f"{target.code}-TC")
        c.test_point_id = None  # 测试点属于原项目，跨项目移动后解除关联
        moved += 1
    db.commit()
    return {"ok": True, "moved": moved, "skipped": skipped}


# ================= 执行中心 =================


class PlanBody(BaseModel):
    project_id: int
    name: str
    plan_type: str = "functional"
    requirement_id: int | None = None
    case_ids: list[int] = []
    executor: str | None = None


@router.post("/plans")
def create_plan(body: PlanBody, db: Session = Depends(get_db)):
    p = wb.WbExecutionPlan(
        project_id=body.project_id, name=body.name, plan_type=body.plan_type,
        requirement_id=body.requirement_id,
        executor=body.executor, total_count=len(body.case_ids),
        started_at=func.now(),
    )
    db.add(p)
    db.flush()
    for cid in body.case_ids:
        db.add(wb.WbExecutionPlanCase(plan_id=p.id, case_id=cid))
    db.commit()
    return {"id": p.id}


@router.get("/plans")
def list_plans(project_id: int | None = None, plan_type: str | None = None,
               keyword: str | None = None, db: Session = Depends(get_db)):
    q = db.query(wb.WbExecutionPlan)
    if project_id:
        q = q.filter(wb.WbExecutionPlan.project_id == project_id)
    if plan_type:
        q = q.filter(wb.WbExecutionPlan.plan_type == plan_type)
    if keyword:
        q = q.filter(wb.WbExecutionPlan.name.like(f"%{keyword}%"))
    plans = q.order_by(wb.WbExecutionPlan.id.desc()).limit(200).all()
    pname = {p.id: p.name for p in db.query(wb.WbProject).all()}
    rname = {r.id: r.title for r in db.query(wb.WbRequirement).all()}
    # 明细表实时重算各计划计数：防止缓存列与明细不一致（autoflush 历史数据 / 异常路径写入导致脏数据）
    # 读到不一致时同步回写缓存列与状态（自愈），保证列表统计永远以用例明细为准
    dirty = False
    if plans:
        agg: dict[int, dict[str, int]] = {}
        for pid, result, cnt in (
            db.query(wb.WbExecutionPlanCase.plan_id, wb.WbExecutionPlanCase.result,
                     func.count(wb.WbExecutionPlanCase.id))
            .filter(wb.WbExecutionPlanCase.plan_id.in_([p.id for p in plans]))
            .group_by(wb.WbExecutionPlanCase.plan_id, wb.WbExecutionPlanCase.result).all()
        ):
            agg.setdefault(pid, {})[result or "pending"] = cnt
        for p in plans:
            c = agg.get(p.id, {})
            total = sum(c.values())
            done = total - c.get("pending", 0)
            # 状态以明细实时推导：全部执行完→已完成；已完成计划有用例回退未执行→执行中；其余保持原状态
            if total and done >= total:
                new_status = "completed" if p.status != "aborted" else p.status
            elif p.status == "completed" and total:
                new_status = "running"
            else:
                new_status = p.status
            changed = (p.total_count != total or p.pass_count != c.get("pass", 0)
                       or p.fail_count != c.get("fail", 0) or p.block_count != c.get("block", 0)
                       or p.skip_count != c.get("skip", 0) or new_status != p.status)
            if changed:
                p.total_count = total
                p.pass_count = c.get("pass", 0)
                p.fail_count = c.get("fail", 0)
                p.block_count = c.get("block", 0)
                p.skip_count = c.get("skip", 0)
                p.status = new_status
                if new_status == "completed" and not p.finished_at:
                    p.finished_at = func.now()
                elif new_status == "running":
                    p.finished_at = None
                dirty = True
        if dirty:
            db.commit()
    return [{**row(p, "id", "project_id", "name", "plan_type", "requirement_id", "status", "executor",
                   "total_count", "pass_count", "fail_count", "block_count", "skip_count",
                   "started_at", "finished_at", "created_at"),
             "project_name": pname.get(p.project_id, ""),
             "requirement_title": rname.get(p.requirement_id, "") if p.requirement_id else ""} for p in plans]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(wb.WbExecutionPlan, plan_id)
    if not p:
        raise HTTPException(404, "执行计划不存在")
    items = (
        db.query(wb.WbExecutionPlanCase, wb.WbTestCase)
        .join(wb.WbTestCase, wb.WbExecutionPlanCase.case_id == wb.WbTestCase.id)
        .filter(wb.WbExecutionPlanCase.plan_id == plan_id)
        .order_by(wb.WbExecutionPlanCase.id)
        .all()
    )
    d = row(p, "id", "project_id", "name", "plan_type", "requirement_id", "status", "executor",
            "total_count", "pass_count", "fail_count", "block_count", "skip_count",
            "started_at", "finished_at")
    req = db.get(wb.WbRequirement, p.requirement_id) if p.requirement_id else None
    d["requirement_title"] = req.title if req else ""
    d["cases"] = [{
        "plan_case_id": pc.id,
        "case_id": c.id,
        "case_no": c.case_no,
        "title": pc.title_override or c.title,
        "priority": pc.priority_override or c.priority,
        "module_path": c.module_path,
        "precondition": pc.precondition_override if pc.precondition_override is not None else c.precondition,
        "steps": pc.steps_override if pc.steps_override is not None else c.steps,
        "expected": pc.expected_override if pc.expected_override is not None else c.expected,
        "edited": bool(pc.title_override or pc.priority_override or pc.precondition_override
                       or pc.steps_override is not None or pc.expected_override is not None),
        "result": pc.result,
        "note": pc.note,
        "executed_at": str(pc.executed_at or ""),
        "executor": pc.executor,
    } for pc, c in items]
    return d


class PlanCaseResultBody(BaseModel):
    result: str
    note: str | None = None
    actual_result: str | None = None
    step_results: list | None = None
    duration_ms: int | None = None
    executor: str | None = None
    # 计划内用例内容修改（快照覆盖，不影响用例库原用例）
    title: str | None = None
    priority: str | None = None
    precondition: str | None = None
    steps: list | None = None
    expected: str | None = None


def _recount_plan(db: Session, plan_id: int):
    """重算计划计数列与完成状态（add/remove/batch/submit 共用）。"""
    p = db.get(wb.WbExecutionPlan, plan_id)
    counts = dict(
        db.query(wb.WbExecutionPlanCase.result, func.count(wb.WbExecutionPlanCase.id))
        .filter(wb.WbExecutionPlanCase.plan_id == plan_id)
        .group_by(wb.WbExecutionPlanCase.result)
        .all()
    )
    p.total_count = sum(counts.values())
    p.pass_count = counts.get("pass", 0)
    p.fail_count = counts.get("fail", 0)
    p.block_count = counts.get("block", 0)
    p.skip_count = counts.get("skip", 0)
    done = p.total_count - counts.get("pending", 0)
    if p.total_count:
        if done >= p.total_count:
            if p.status != "aborted":
                p.status = "completed"
                if not p.finished_at:
                    p.finished_at = func.now()
        elif p.status == "completed":
            # 有用例被改回未执行：计划从已完成回退为执行中
            p.status = "running"
            p.finished_at = None
    return p


@router.put("/plan-cases/{pcid}")
def submit_result(pcid: int, body: PlanCaseResultBody, db: Session = Depends(get_db)):
    if body.result not in ("pending", "pass", "fail", "block", "skip"):
        raise HTTPException(400, "非法执行结果")
    pc = db.get(wb.WbExecutionPlanCase, pcid)
    if not pc:
        raise HTTPException(404, "清单条目不存在")
    pc.result = body.result
    pc.note = body.note
    pc.executor = body.executor
    pc.executed_at = func.now() if body.result != "pending" else None
    # 内容修改只写入计划内快照字段
    if body.title is not None:
        pc.title_override = body.title
    if body.priority is not None:
        pc.priority_override = body.priority
    if body.precondition is not None:
        pc.precondition_override = body.precondition
    if body.steps is not None:
        pc.steps_override = body.steps
    if body.expected is not None:
        pc.expected_override = body.expected
    db.flush()  # autoflush=False：先落库本条结果，计数统计才能读到最新状态

    rec = wb.WbExecutionRecord(
        plan_case_id=pcid, case_id=pc.case_id,
        actual_result=body.actual_result, step_results=body.step_results,
        duration_ms=body.duration_ms,
        executed_at=func.now(),
    )
    db.add(rec)

    p = _recount_plan(db, pc.plan_id)
    db.commit()
    return {"ok": True, "plan": {"pass": p.pass_count, "fail": p.fail_count,
             "block": p.block_count, "skip": p.skip_count, "status": p.status}}


class PlanCasesBody(BaseModel):
    case_ids: list[int] = []


@router.post("/plans/{plan_id}/cases")
def add_plan_cases(plan_id: int, body: PlanCasesBody, db: Session = Depends(get_db)):
    """计划内追加用例（跳过已在计划中的）。"""
    p = db.get(wb.WbExecutionPlan, plan_id)
    if not p:
        raise HTTPException(404, "执行计划不存在")
    existing = {x.case_id for x in db.query(wb.WbExecutionPlanCase).filter(
        wb.WbExecutionPlanCase.plan_id == plan_id).all()}
    added = 0
    for cid in body.case_ids:
        if cid in existing:
            continue
        if not db.get(wb.WbTestCase, cid):
            continue
        db.add(wb.WbExecutionPlanCase(plan_id=plan_id, case_id=cid))
        added += 1
    if added:
        db.flush()
        _recount_plan(db, plan_id)
    db.commit()
    return {"ok": True, "added": added}


@router.delete("/plans/{plan_id}/cases")
def remove_plan_cases(plan_id: int, plan_case_ids: list[int] = Query(...), db: Session = Depends(get_db)):
    """从计划中移除用例（连同其执行记录）。"""
    p = db.get(wb.WbExecutionPlan, plan_id)
    if not p:
        raise HTTPException(404, "执行计划不存在")
    removed = (
        db.query(wb.WbExecutionPlanCase)
        .filter(wb.WbExecutionPlanCase.plan_id == plan_id,
                wb.WbExecutionPlanCase.id.in_(plan_case_ids))
        .delete(synchronize_session=False)
    )
    _recount_plan(db, plan_id)
    db.commit()
    return {"ok": True, "removed": removed}


class BatchResultBody(BaseModel):
    plan_case_ids: list[int]
    result: str
    note: str | None = None
    actual_result: str | None = None
    executor: str | None = None


@router.post("/plan-cases/batch-result")
def batch_result(body: BatchResultBody, db: Session = Depends(get_db)):
    """多选用例一键标记执行状态（第 8 项需求）。"""
    if body.result not in ("pending", "pass", "fail", "block", "skip"):
        raise HTTPException(400, "非法执行结果")
    if not body.plan_case_ids:
        raise HTTPException(400, "未选择用例")
    pcs = db.query(wb.WbExecutionPlanCase).filter(
        wb.WbExecutionPlanCase.id.in_(body.plan_case_ids)).all()
    for pc in pcs:
        pc.result = body.result
        pc.note = body.note
        pc.executor = body.executor
        pc.executed_at = func.now() if body.result != "pending" else None
        db.add(wb.WbExecutionRecord(
            plan_case_id=pc.id, case_id=pc.case_id,
            actual_result=body.actual_result, executed_at=func.now(),
        ))
    plan_ids = {pc.plan_id for pc in pcs}
    db.flush()
    for pid in plan_ids:
        _recount_plan(db, pid)
    db.commit()
    return {"ok": True, "updated": len(pcs)}


@router.post("/plans/{plan_id}/abort")
def abort_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(wb.WbExecutionPlan, plan_id)
    if not p:
        raise HTTPException(404, "执行计划不存在")
    p.status = "aborted"
    p.finished_at = func.now()
    db.commit()
    return {"ok": True}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    """删除计划及其清单条目与执行记录（级联）。"""
    p = db.get(wb.WbExecutionPlan, plan_id)
    if not p:
        raise HTTPException(404, "执行计划不存在")
    pc_ids = [x.id for x in db.query(wb.WbExecutionPlanCase.id).filter(
        wb.WbExecutionPlanCase.plan_id == plan_id).all()]
    if pc_ids:
        db.query(wb.WbExecutionRecord).filter(
            wb.WbExecutionRecord.plan_case_id.in_(pc_ids)).delete(synchronize_session=False)
    db.query(wb.WbExecutionPlanCase).filter(
        wb.WbExecutionPlanCase.plan_id == plan_id).delete(synchronize_session=False)
    db.delete(p)
    db.commit()
    return {"ok": True}


# ================= Bug 管理 =================


class BugBody(BaseModel):
    project_id: int
    title: str
    description: str | None = None
    severity: str | None = None
    priority: str | None = None
    status: str = "active"
    assignee: str | None = None
    plan_id: int | None = None
    links: list[dict] | None = None  # [{link_type, link_id}]


@router.get("/bugs")
def list_bugs(project_id: int | None = None, status: str | None = None, keyword: str | None = None,
              page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    q = db.query(wb.WbBug)
    if project_id:
        q = q.filter(wb.WbBug.project_id == project_id)
    if status:
        q = q.filter(wb.WbBug.status == status)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(wb.WbBug.title.like(like), wb.WbBug.bug_no.like(like)))
    total = q.count()
    bugs = q.order_by(wb.WbBug.id.desc()).offset((page - 1) * size).limit(size).all()
    pname = {p.id: p.name for p in db.query(wb.WbProject).all()}
    return {"total": total, "items": [
        {**row(b, "id", "bug_no", "project_id", "title", "severity", "priority", "status",
               "source", "plan_id", "assignee", "found_at", "updated_at"),
         "project_name": pname.get(b.project_id, "")} for b in bugs
    ]}


@router.post("/bugs")
def create_bug(body: BugBody, db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, body.project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    d = body.model_dump()
    links = d.pop("links", None)
    b = wb.WbBug(bug_no=next_no(db, wb.WbBug, wb.WbBug.bug_no, f"{p.code}-BUG"),
                 reporter="local", **d)
    db.add(b)
    db.flush()
    for lk in (links or []):
        lt = lk.get("link_type")
        if lt in ("requirement", "case", "plan", "log", "regression_run") and lk.get("link_id"):
            db.add(wb.WbBugLink(bug_id=b.id, link_type=lt, link_id=int(lk["link_id"])))
    db.commit()
    return {"id": b.id, "bug_no": b.bug_no}


@router.get("/bugs/{bid}")
def get_bug(bid: int, db: Session = Depends(get_db)):
    b = db.get(wb.WbBug, bid)
    if not b:
        raise HTTPException(404, "Bug 不存在")
    d = row(b, "id", "bug_no", "project_id", "title", "description", "severity", "priority",
            "status", "source", "plan_id", "assignee", "reporter", "found_at", "closed_at", "updated_at")
    links = db.query(wb.WbBugLink).filter(wb.WbBugLink.bug_id == bid).all()
    resolved = []
    for lk in links:
        title = None
        if lk.link_type == "case":
            c = db.get(wb.WbTestCase, lk.link_id)
            title = f"{c.case_no} {c.title}" if c else None
        elif lk.link_type == "requirement":
            r = db.get(wb.WbRequirement, lk.link_id)
            title = r.title if r else None
        elif lk.link_type == "log":
            g = db.get(wb.WbTestLog, lk.link_id)
            title = g.title if g else None
        elif lk.link_type == "plan":
            pp = db.get(wb.WbExecutionPlan, lk.link_id)
            title = pp.name if pp else None
        resolved.append({"id": lk.id, "link_type": lk.link_type, "link_id": lk.link_id, "title": title})
    d["links"] = resolved
    atts = db.query(wb.WbAttachment).filter(
        wb.WbAttachment.owner_type == "bug", wb.WbAttachment.owner_id == bid).all()
    d["attachments"] = [row(a, "id", "file_name", "file_path", "file_size", "mime_type", "uploaded_at") for a in atts]
    return d


@router.put("/bugs/{bid}")
def update_bug(bid: int, body: BugBody, db: Session = Depends(get_db)):
    b = db.get(wb.WbBug, bid)
    if not b:
        raise HTTPException(404, "Bug 不存在")
    d = body.model_dump()
    d.pop("links", None)
    d.pop("project_id", None)
    if d.get("status") == "closed":
        b.closed_at = func.now()
    for k, v in d.items():
        setattr(b, k, v)
    db.commit()
    return {"ok": True}


class LinkBody(BaseModel):
    link_type: str
    link_id: int


@router.post("/bugs/{bid}/links")
def add_bug_link(bid: int, body: LinkBody, db: Session = Depends(get_db)):
    if body.link_type not in ("requirement", "case", "plan", "log", "regression_run"):
        raise HTTPException(400, "非法关联类型")
    if not db.get(wb.WbBug, bid):
        raise HTTPException(404, "Bug 不存在")
    exists = db.query(wb.WbBugLink.id).filter(
        wb.WbBugLink.bug_id == bid, wb.WbBugLink.link_type == body.link_type,
        wb.WbBugLink.link_id == body.link_id).first()
    if exists:
        return {"ok": True, "id": exists[0], "existed": True}
    lk = wb.WbBugLink(bug_id=bid, link_type=body.link_type, link_id=body.link_id)
    db.add(lk)
    db.commit()
    return {"ok": True, "id": lk.id}


@router.delete("/bug-links/{lid}")
def delete_bug_link(lid: int, db: Session = Depends(get_db)):
    lk = db.get(wb.WbBugLink, lid)
    if not lk:
        raise HTTPException(404, "关联不存在")
    db.delete(lk)
    db.commit()
    return {"ok": True}


@router.get("/bug-links")
def lookup_bugs_by_link(link_type: str, link_id: int, db: Session = Depends(get_db)):
    """从用例/需求/日志反查关联 Bug（双向跳转）。"""
    bugs = (
        db.query(wb.WbBug)
        .join(wb.WbBugLink, wb.WbBugLink.bug_id == wb.WbBug.id)
        .filter(wb.WbBugLink.link_type == link_type, wb.WbBugLink.link_id == link_id)
        .all()
    )
    return [row(b, "id", "bug_no", "title", "severity", "priority", "status") for b in bugs]


# ================= 附件 =================


@router.post("/attachments")
async def upload_attachment(
    owner_type: str = Query(...),
    owner_id: int = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if owner_type not in ("bug", "execution_record", "test_log", "api_request"):
        raise HTTPException(400, "非法挂载类型")
    upload_dir = Path(get_upload_dir())
    sub = upload_dir / "wb" / owner_type / str(owner_id)
    sub.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "file.png").suffix
    target = sub / f"{int(time.time() * 1000)}{suffix}"
    size = 0
    with open(target, "wb") as f:
        while chunk := await file.read(1024 * 512):
            size += len(chunk)
            f.write(chunk)
    a = wb.WbAttachment(
        owner_type=owner_type, owner_id=owner_id,
        file_name=file.filename or target.name,
        file_path=str(target.relative_to(upload_dir)),
        file_size=size, mime_type=file.content_type,
    )
    db.add(a)
    db.commit()
    return {"id": a.id, "file_name": a.file_name, "file_size": size, "mime_type": a.mime_type}


@router.get("/attachments")
def list_attachments(owner_type: str, owner_id: int, db: Session = Depends(get_db)):
    atts = db.query(wb.WbAttachment).filter(
        wb.WbAttachment.owner_type == owner_type,
        wb.WbAttachment.owner_id == owner_id).order_by(wb.WbAttachment.id).all()
    return [row(a, "id", "owner_type", "owner_id", "file_name", "file_path", "file_size", "mime_type", "uploaded_at") for a in atts]


@router.get("/attachments/{aid}/file")
def get_attachment_file(aid: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    a = db.get(wb.WbAttachment, aid)
    if not a:
        raise HTTPException(404, "附件不存在")
    path = Path(get_upload_dir()) / a.file_path
    if not path.exists():
        raise HTTPException(404, "文件已丢失")
    return FileResponse(path, filename=a.file_name)


@router.delete("/attachments/{aid}")
def delete_attachment(aid: int, db: Session = Depends(get_db)):
    a = db.get(wb.WbAttachment, aid)
    if not a:
        raise HTTPException(404, "附件不存在")
    path = Path(get_upload_dir()) / a.file_path
    if path.exists():
        path.unlink(missing_ok=True)
    db.delete(a)
    db.commit()
    return {"ok": True}


# ================= 日志 =================


class LogBody(BaseModel):
    project_id: int
    title: str
    log_type: str = "execution"
    content: str | None = None
    plan_id: int | None = None


@router.get("/logs")
def list_logs(project_id: int, log_type: str | None = None, page: int = 1, size: int = 50,
              db: Session = Depends(get_db)):
    q = db.query(wb.WbTestLog).filter(wb.WbTestLog.project_id == project_id)
    if log_type:
        q = q.filter(wb.WbTestLog.log_type == log_type)
    total = q.count()
    logs = q.order_by(wb.WbTestLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "items": [
        row(l, "id", "project_id", "plan_id", "log_type", "title", "content", "log_time", "created_at")
        for l in logs
    ]}


@router.post("/logs")
def create_log(body: LogBody, db: Session = Depends(get_db)):
    l = wb.WbTestLog(**body.model_dump())
    db.add(l)
    db.commit()
    return {"id": l.id}


@router.put("/logs/{lid}")
def update_log(lid: int, body: LogBody, db: Session = Depends(get_db)):
    l = db.get(wb.WbTestLog, lid)
    if not l:
        raise HTTPException(404, "日志不存在")
    d = body.model_dump()
    d.pop("project_id", None)
    for k, v in d.items():
        setattr(l, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/logs/{lid}")
def delete_log(lid: int, db: Session = Depends(get_db)):
    l = db.get(wb.WbTestLog, lid)
    if not l:
        raise HTTPException(404, "日志不存在")
    db.delete(l)
    db.commit()
    return {"ok": True}


# ================= 报告 =================


@router.post("/reports/generate")
def generate_report(project_id: int, report_type: str = "iteration", db: Session = Depends(get_db)):
    p = db.get(wb.WbProject, project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    case_groups = dict(
        db.query(wb.WbTestCase.case_type, func.count(wb.WbTestCase.id))
        .filter(wb.WbTestCase.project_id == project_id, wb.WbTestCase.status == "active")
        .group_by(wb.WbTestCase.case_type).all()
    )
    plan_groups = dict(
        db.query(wb.WbExecutionPlan.plan_type, func.count(wb.WbExecutionPlan.id))
        .filter(wb.WbExecutionPlan.project_id == project_id)
        .group_by(wb.WbExecutionPlan.plan_type).all()
    )
    executed = (
        db.query(func.sum(wb.WbExecutionPlan.pass_count + wb.WbExecutionPlan.fail_count
                          + wb.WbExecutionPlan.block_count + wb.WbExecutionPlan.skip_count))
        .filter(wb.WbExecutionPlan.project_id == project_id).scalar() or 0
    )
    total_exec = (
        db.query(func.sum(wb.WbExecutionPlan.total_count))
        .filter(wb.WbExecutionPlan.project_id == project_id).scalar() or 0
    )
    bug_stats = dict(
        db.query(wb.WbBug.status, func.count(wb.WbBug.id))
        .filter(wb.WbBug.project_id == project_id)
        .group_by(wb.WbBug.status).all()
    )
    bugs_total = sum(bug_stats.values())
    passed = (db.query(func.sum(wb.WbExecutionPlan.pass_count))
              .filter(wb.WbExecutionPlan.project_id == project_id).scalar() or 0)
    stats = {
        "cases": case_groups,
        "plans": plan_groups,
        "executed": int(executed),
        "execution_rate": round(float(executed) / float(total_exec) * 100, 1) if total_exec else 0,
        "pass_rate": round(float(passed) / float(executed) * 100, 1) if executed else 0,
        "bugs": bug_stats,
        "bugs_total": bugs_total,
    }
    lines = [
        f"# {p.name} 测试报告",
        "",
        f"- 用例规模：功能 {case_groups.get('functional', 0)} / 接口 {case_groups.get('api', 0)} / 自动化 {case_groups.get('automation', 0)}",
        f"- 执行进度：{stats['executed']}/{total_exec}（{stats['execution_rate']}%）",
        f"- 通过率：{stats['pass_rate']}%",
        f"- 缺陷分布：{json.dumps(bug_stats, ensure_ascii=False)}（合计 {bugs_total}）",
        "",
        "## 风险与遗留",
        "- 待 AI 生成（配置模型 key 后可用）：失败聚类、风险评估、遗留问题清单",
    ]
    rep = wb.WbReport(
        project_id=project_id, report_type=report_type,
        title=f"{p.name} 测试报告",
        stats=stats, content="\n".join(lines), generated_by="manual",
    )
    db.add(rep)
    db.commit()
    return {"id": rep.id}


@router.get("/reports")
def list_reports(project_id: int, db: Session = Depends(get_db)):
    reps = db.query(wb.WbReport).filter(wb.WbReport.project_id == project_id).order_by(wb.WbReport.id.desc()).all()
    return [row(r, "id", "project_id", "plan_id", "report_type", "title", "stats", "generated_by", "created_at") for r in reps]


@router.get("/reports/{rid}")
def get_report(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    return row(r, "id", "project_id", "report_type", "title", "stats", "content", "generated_by", "created_at")


@router.delete("/reports/{rid}")
def delete_report(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbReport, rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ================= 总览看板 =================


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    projects = db.query(wb.WbProject).filter(wb.WbProject.status == "active").order_by(wb.WbProject.id.desc()).all()
    out = []
    for p in projects:
        case_groups = dict(
            db.query(wb.WbTestCase.case_type, func.count(wb.WbTestCase.id))
            .filter(wb.WbTestCase.project_id == p.id, wb.WbTestCase.status == "active")
            .group_by(wb.WbTestCase.case_type).all()
        )
        plans = (
            db.query(wb.WbExecutionPlan)
            .filter(wb.WbExecutionPlan.project_id == p.id)
            .order_by(wb.WbExecutionPlan.id.desc()).first()
        )
        bugs_open = (
            db.query(func.count(wb.WbBug.id))
            .filter(wb.WbBug.project_id == p.id,
                    wb.WbBug.status.in_(["active", "processing", "fixed", "regressed"])).scalar()
        )
        executed = sum(
            float(db.query(func.sum(getattr(wb.WbExecutionPlan, c)))
                  .filter(wb.WbExecutionPlan.project_id == p.id).scalar() or 0)
            for c in ("pass_count", "fail_count", "block_count", "skip_count")
        )
        total = float(db.query(func.sum(wb.WbExecutionPlan.total_count)).filter(
            wb.WbExecutionPlan.project_id == p.id).scalar() or 0)
        out.append({
            "id": p.id, "name": p.name, "code": p.code,
            "cases": case_groups,
            "execution_rate": round(executed / total * 100, 1) if total else 0,
            "bugs_open": bugs_open,
            "last_plan": {"name": plans.name, "plan_type": plans.plan_type} if plans else None,
        })
    return {"projects": out}


# ================= 设置 =================

_SECRET_GROUPS = {"ai_model"}


def _secret_cipher():
    """AES 加密：密钥存 data/secret.key（首次生成）。cryptography 为 pymysql 依赖，已在环境内。"""
    from cryptography.fernet import Fernet
    base = Path(get_upload_dir()).parent
    key_file = base / "secret.key"
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(Fernet.generate_key())
    return Fernet(key_file.read_bytes())


class SettingBody(BaseModel):
    setting_key: str
    setting_group: str = "general"
    setting_value: str | None = None
    is_secret: int = 0
    description: str | None = None


@router.get("/settings")
def list_settings(group: str | None = None, db: Session = Depends(get_db)):
    q = db.query(wb.WbSetting)
    if group:
        q = q.filter(wb.WbSetting.setting_group == group)
    items = q.order_by(wb.WbSetting.setting_group, wb.WbSetting.setting_key).all()
    out = []
    for s in items:
        d = row(s, "id", "setting_key", "setting_group", "is_secret", "description", "updated_at")
        d["setting_value"] = "******" if s.is_secret else s.setting_value
        d["has_value"] = bool(s.setting_value)
        out.append(d)
    return out


@router.put("/settings")
def upsert_setting(body: SettingBody, db: Session = Depends(get_db)):
    s = db.query(wb.WbSetting).filter(wb.WbSetting.setting_key == body.setting_key).first()
    value = body.setting_value
    if body.is_secret and value:
        value = _secret_cipher().encrypt(value.encode()).decode()
    if s:
        if value is not None:
            s.setting_value = value
        s.setting_group = body.setting_group
        s.is_secret = body.is_secret
        s.description = body.description or s.description
    else:
        s = wb.WbSetting(**body.model_dump(exclude={"setting_value"}))
        s.setting_value = value
        db.add(s)
    db.commit()
    return {"ok": True}


@router.get("/settings/reveal")
def reveal_setting(setting_key: str, db: Session = Depends(get_db)):
    """解密回显（仅本地单用户场景；用于"测试连通"时读取 key）。"""
    s = db.query(wb.WbSetting).filter(wb.WbSetting.setting_key == setting_key).first()
    if not s or not s.setting_value:
        return {"value": None}
    if s.is_secret:
        return {"value": _secret_cipher().decrypt(s.setting_value.encode()).decode()}
    return {"value": s.setting_value}


@router.post("/settings/test-model")
def test_model(provider: str = "deepseek", db: Session = Depends(get_db)):
    """用库里配置的 key 做一次最小连通性测试。"""
    key_row = db.query(wb.WbSetting).filter(
        wb.WbSetting.setting_key == f"ai.{provider}.api_key").first()
    if not key_row or not key_row.setting_value:
        raise HTTPException(400, f"{provider} 的 API key 未配置")
    key = key_row.setting_value
    if key_row.is_secret:
        key = _secret_cipher().decrypt(key.encode()).decode()
    base_urls = {"deepseek": "https://api.deepseek.com",
                 "glm": "https://open.bigmodel.cn/api/paas/v4",
                 "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
    models = {"deepseek": "deepseek-chat", "glm": "glm-4-flash", "qwen": "qwen-turbo"}
    url = base_urls[provider]
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=json.dumps({"model": models[provider], "messages": [{"role": "user", "content": "ping"}],
                         "max_tokens": 5}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        db.add(wb.WbAiCallLog(scene="connectivity_test", model_name=models[provider],
                              duration_ms=int((time.time() - start) * 1000), success=1))
        db.commit()
        return {"ok": True, "model": body.get("model"), "latency_ms": int((time.time() - start) * 1000)}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        db.add(wb.WbAiCallLog(scene="connectivity_test", model_name=models[provider],
                              success=0, error_msg=f"HTTP {e.code}: {detail}"))
        db.commit()
        raise HTTPException(400, f"HTTP {e.code}: {detail}")
    except Exception as e:
        raise HTTPException(400, f"连通失败: {e}")
