"""测试深度工作台业务 API（/wb 前缀）。

单机单用户使用，不做鉴权。所有端点读写 Workbench 库的 wb_ 表。
"""
import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi import Header as _Header, Request as _Request, Response as _Response, \
    Depends as _Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, text as _text
from sqlalchemy.orm import Session

import wbmodels as wb
from config import get_upload_dir
from models.database import get_db, get_engine

logger = logging.getLogger(__name__)

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


def _eval_assertions(assertions, res):
    """EX-A3 断言判定：[{type: status|json_path|response_time, op, value, path}] → [{passed, desc}]。"""
    out = []
    parsed = None
    body_parsed = False
    for a in (assertions or []):
        if not isinstance(a, dict):
            continue
        t = a.get("type") or "status"
        op = a.get("op") or "=="
        val = a.get("value")
        if t == "status":
            actual = res.get("status")
        elif t == "response_time":
            actual = res.get("duration_ms")
        else:  # json_path
            if not body_parsed:
                try:
                    parsed = json.loads(res.get("body") or "")
                except Exception:
                    parsed = None
                body_parsed = True
            actual = _json_path_get(parsed, a.get("path")) if parsed is not None else None

        if op == "not_null":
            passed = actual is not None and actual != ""
        elif op == "contains":
            passed = val is not None and str(val) in str(actual if actual is not None else "")
        else:
            try:
                an, vn = float(actual), float(val)
            except (TypeError, ValueError):
                an = vn = None
            if op == "==":
                passed = (an == vn) if an is not None else (str(actual) == str(val))
            elif op == "!=":
                passed = (an != vn) if an is not None else (str(actual) != str(val))
            elif op == ">=":
                passed = an is not None and vn is not None and an >= vn
            elif op == "<=":
                passed = an is not None and vn is not None and an <= vn
            elif op == ">":
                passed = an is not None and vn is not None and an > vn
            elif op == "<":
                passed = an is not None and vn is not None and an < vn
            else:
                passed = False
        type_name = {"status": "状态码", "response_time": "响应耗时(ms)", "json_path": "字段"}.get(t, t)
        where = f"[{a.get('path')}]" if t == "json_path" else ""
        out.append({
            "passed": bool(passed),
            "desc": f"{type_name}{where} {op} {val if val is not None else ''}（实际: {actual if actual is not None else '无'}）",
        })
    return out


def _schema_example(schema, depth=0):
    """从 JSON Schema 粗略生成示例值（导入 OpenAPI 时填充请求体示例）。"""
    if not isinstance(schema, dict) or depth > 6:
        return None
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    t = schema.get("type") or ("object" if schema.get("properties") else None)
    if t == "object":
        props = schema.get("properties") or {}
        return {k: _schema_example(v, depth + 1) for k, v in props.items()} if props else {}
    if t == "array":
        item = _schema_example(schema.get("items") or {}, depth + 1)
        return [item] if item is not None else []
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return True
    return ""


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


@router.post("/api-import-openapi")
def import_openapi(body: dict, db: Session = Depends(get_db)):
    """EX-A3：导入 OpenAPI/Swagger 文档（JSON/YAML），按 paths 生成接口集合与接口定义。"""
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(400, "缺少 project_id")
    content = body.get("content") or ""
    if not content.strip():
        raise HTTPException(400, "文档内容为空")
    try:
        spec = json.loads(content)
    except Exception:
        try:
            import yaml
            spec = yaml.safe_load(content)
        except Exception:
            raise HTTPException(400, "内容不是合法的 JSON/YAML")
    if not isinstance(spec, dict) or "paths" not in spec:
        raise HTTPException(400, "未找到 paths 节点，请确认是 OpenAPI/Swagger 文档")

    info = spec.get("info") or {}
    title = (str(info.get("title") or "导入接口集") or "导入接口集").strip()[:100]
    base_url = ""
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        base_url = str(servers[0].get("url") or "").rstrip("/")
    if not base_url and spec.get("host"):  # Swagger 2.0
        scheme = (spec.get("schemes") or ["https"])[0]
        base_url = f"{scheme}://{spec['host']}{spec.get('basePath') or ''}".rstrip("/")

    col = wb.WbApiCollection(project_id=project_id, name=title, parent_id=body.get("collection_id"))
    db.add(col)
    db.flush()

    count = skipped = 0
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for m, op in methods.items():
            m = str(m).lower()
            if m not in ("get", "post", "put", "delete", "patch") or not isinstance(op, dict):
                continue
            summary = str(op.get("summary") or op.get("operationId") or f"{m.upper()} {path}").strip()
            params = []
            body_type, body = "none", None
            for p in (op.get("parameters") or []):
                if not isinstance(p, dict) or not p.get("name"):
                    continue
                if p.get("in") == "query":
                    params.append({"key": p["name"], "value": "", "type": p.get("type") or "string",
                                   "desc": (p.get("description") or "")[:200]})
                elif p.get("in") == "body" and p.get("schema"):  # Swagger 2.0
                    body_type = "json"
                    body = json.dumps(_schema_example(p["schema"]), ensure_ascii=False)
                elif p.get("in") == "formData":
                    if body_type == "none":
                        body_type = "form"
            rb = ((op.get("requestBody") or {}).get("content") or {})  # OpenAPI 3.x
            jc = rb.get("application/json")
            if jc is not None and isinstance(jc, dict):
                body_type = "json"
                example = jc.get("example") if jc.get("example") is not None else _schema_example(jc.get("schema"))
                try:
                    body = json.dumps(example, ensure_ascii=False)
                except Exception:
                    body = None
            url = (base_url + path) if base_url else path
            if len(url) > 480:
                skipped += 1
                continue
            db.add(wb.WbApiRequest(
                project_id=project_id, collection_id=col.id,
                name=summary[:190], method=m.upper(), url=url,
                params=params or None, body_type=body_type, body=body,
                description=(str(op.get("description") or "") or None),
            ))
            count += 1
    if count == 0:
        db.rollback()
        raise HTTPException(400, "文档中没有可导入的接口（仅支持 GET/POST/PUT/DELETE/PATCH）")
    db.commit()
    return {"ok": True, "collection_id": col.id, "collection_name": title,
            "imported": count, "skipped": skipped, "base_url": base_url}


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
            "assertions": [a for a in (snap.get("assertions") or [])
                           if isinstance(a, dict) and (a.get("type") or "status")],
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
            # EX-A3：断言判定（状态码 / JSON 字段 / 响应耗时）
            assertion_results = _eval_assertions(snap.get("assertions"), res)
            results.append({
                "step_id": st.id, "name": name, "method": method, "url": res["url"],
                "status": res["status"], "duration_ms": res["duration_ms"],
                "size": res["size"], "error": res["error"], "loop": loop,
                "history_id": res["history_id"],
                "extracted": extracted,
                "assertions": assertion_results,
            })
    total_ms = int((time.perf_counter() - t0) * 1000)
    # 通过 = HTTP 成功 且 无异常 且 全部断言通过（无断言时仅看 HTTP）
    ok_count = sum(
        1 for x in results
        if x.get("status") and 200 <= x["status"] < 400 and not x.get("error")
        and all(a["passed"] for a in (x.get("assertions") or []))
    )
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
    d["regression_ready"] = _bug_regression_ready(db, b)
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
        # B-04/RG-04：关闭前必须关联用例且存在回归通过记录
        if not _bug_regression_ready(db, b):
            raise HTTPException(
                400, "关闭被拦截：Bug 关联用例尚无回归通过记录，请先发起回归验证并通过（RG-04）")
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


# ================= 回归测试管理（RG-01~05） =================


def _cron_next(expr: str, base: datetime | None = None) -> datetime:
    """最小 5 段 cron 解析：分 时 日 月 周。支持 *、*/n、a-b、逗号列表。
    返回下一次触发时间（本地时间）；一年内无触发点则抛 ValueError。"""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron 表达式必须为 5 段：分 时 日 月 周（如 0 9 * * 1）")

    def parse_field(f: str, low: int, high: int) -> set:
        vals = set()
        for part in f.split(","):
            part = part.strip()
            step = 1
            if "/" in part:
                part, step_s = part.split("/", 1)
                step = int(step_s)
                if step < 1:
                    raise ValueError(f"cron 步长无效: {f}")
            if part == "*":
                rng = list(range(low, high + 1))
            elif "-" in part:
                a, b = part.split("-", 1)
                rng = list(range(int(a), int(b) + 1))
            else:
                rng = [int(part)]
            vals.update(rng[::step])
        if not vals or any(v < low or v > high for v in vals):
            raise ValueError(f"cron 字段超出取值范围: {f}")
        return vals

    minutes = parse_field(fields[0], 0, 59)
    hours = parse_field(fields[1], 0, 23)
    days = parse_field(fields[2], 1, 31)
    months = parse_field(fields[3], 1, 12)
    # cron 规定周日=0；Python weekday() 周一=0，转换 (wd+1)%7
    weekdays = {(w) % 7 for w in parse_field(fields[4], 0, 6)}
    dom_star, dow_star = fields[2] == "*", fields[4] == "*"

    t = (base or datetime.now()) + timedelta(minutes=1)
    t = t.replace(second=0, microsecond=0)
    end = t + timedelta(days=366)
    while t < end:
        if t.month not in months:
            # 整月跳过：跳到下月 1 日
            t = (t.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            continue
        dom_ok, dow_ok = t.day in days, (t.weekday() + 1) % 7 in weekdays
        # 标准 cron 语义：日与周仅一个受限→按受限项；两个都受限→任一命中；都为 * → 恒通过
        if dom_star and not dow_star:
            day_ok = dow_ok
        elif dow_star and not dom_star:
            day_ok = dom_ok
        elif not dom_star and not dow_star:
            day_ok = dom_ok or dow_ok
        else:
            day_ok = True
        if not day_ok:
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute not in minutes:
            t += timedelta(minutes=1)
            continue
        return t
    raise ValueError("cron 表达式一年内无触发时间")


class RegressionSetBody(BaseModel):
    project_id: int
    name: str
    description: str | None = None
    case_ids: list[int] = []


@router.get("/regression-sets")
def list_regression_sets(project_id: int, db: Session = Depends(get_db)):
    sets = (db.query(wb.WbRegressionSet).filter(wb.WbRegressionSet.project_id == project_id)
            .order_by(wb.WbRegressionSet.id.desc()).all())
    out = []
    for s in sets:
        d = row(s, "id", "project_id", "name", "description", "case_count", "last_run_at", "created_at")
        # 最近一次执行摘要
        last = (db.query(wb.WbRegressionRun).filter(wb.WbRegressionRun.set_id == s.id)
                .order_by(wb.WbRegressionRun.id.desc()).first())
        d["last_run"] = row(last, "id", "status", "pass_rate", "trigger_type", "finished_at") if last else None
        # 启用中的定时计划数
        d["schedule_count"] = db.query(wb.WbRegressionSchedule.id).filter(
            wb.WbRegressionSchedule.set_id == s.id, wb.WbRegressionSchedule.enabled == 1).count()
        out.append(d)
    return out


@router.post("/regression-sets")
def create_regression_set(body: RegressionSetBody, db: Session = Depends(get_db)):
    if not db.get(wb.WbProject, body.project_id):
        raise HTTPException(404, "项目不存在")
    s = wb.WbRegressionSet(project_id=body.project_id, name=body.name, description=body.description)
    db.add(s)
    db.flush()
    added = _add_set_items(db, s, body.case_ids)
    db.commit()
    return {"id": s.id, "added": added}


def _add_set_items(db: Session, s: wb.WbRegressionSet, case_ids: list[int]) -> int:
    """向回归集追加用例（校验归属项目、去重），刷新冗余计数。"""
    existing = {x.case_id for x in db.query(wb.WbRegressionSetItem).filter_by(set_id=s.id).all()}
    added = 0
    for cid in case_ids:
        if cid in existing:
            continue
        c = db.get(wb.WbTestCase, cid)
        if not c or c.project_id != s.project_id:
            continue
        db.add(wb.WbRegressionSetItem(set_id=s.id, case_id=cid))
        existing.add(cid)
        added += 1
    db.flush()  # autoflush=False：计数前先落库新增条目，否则冗余计数恒为 0
    s.case_count = db.query(wb.WbRegressionSetItem).filter_by(set_id=s.id).count()
    return added


@router.get("/regression-sets/{sid}")
def get_regression_set(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbRegressionSet, sid)
    if not s:
        raise HTTPException(404, "回归集不存在")
    items = (db.query(wb.WbRegressionSetItem, wb.WbTestCase)
             .join(wb.WbTestCase, wb.WbRegressionSetItem.case_id == wb.WbTestCase.id)
             .filter(wb.WbRegressionSetItem.set_id == sid)
             .order_by(wb.WbTestCase.case_no).all())
    schedules = (db.query(wb.WbRegressionSchedule).filter(wb.WbRegressionSchedule.set_id == sid)
                 .order_by(wb.WbRegressionSchedule.id.desc()).all())
    return {**row(s, "id", "project_id", "name", "description", "case_count", "last_run_at", "created_at"),
            "cases": [row(c, "id", "case_no", "title", "case_type", "priority", "module_path") for _, c in items],
            "schedules": [row(x, "id", "name", "cron", "enabled", "environment_id",
                              "last_run_at", "next_run_at") for x in schedules]}


@router.put("/regression-sets/{sid}")
def update_regression_set(sid: int, body: RegressionSetBody, db: Session = Depends(get_db)):
    s = db.get(wb.WbRegressionSet, sid)
    if not s:
        raise HTTPException(404, "回归集不存在")
    s.name, s.description = body.name, body.description
    # 全量替换条目
    db.query(wb.WbRegressionSetItem).filter_by(set_id=sid).delete(synchronize_session=False)
    db.flush()
    _add_set_items(db, s, body.case_ids)
    db.commit()
    return {"ok": True}


@router.delete("/regression-sets/{sid}")
def delete_regression_set(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbRegressionSet, sid)
    if not s:
        raise HTTPException(404, "回归集不存在")
    # 历史执行记录与定时计划保留（运行记录 set_id 外键不级联），仅删除集合与条目
    db.query(wb.WbRegressionSetItem).filter_by(set_id=sid).delete(synchronize_session=False)
    db.query(wb.WbRegressionSchedule).filter_by(set_id=sid).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True}


class P0SetBody(BaseModel):
    project_id: int
    name: str = "P0 核心回归集"
    description: str | None = None


@router.post("/regression-sets/p0")
def create_p0_regression_set(body: P0SetBody, db: Session = Depends(get_db)):
    """RG-01：P0 用例一键成集——圈选项目内全部 P0 优先级的启用用例。"""
    cases = (db.query(wb.WbTestCase)
             .filter(wb.WbTestCase.project_id == body.project_id,
                     wb.WbTestCase.priority == "P0", wb.WbTestCase.status == "active").all())
    if not cases:
        raise HTTPException(400, "项目内没有 P0 优先级的启用用例")
    s = wb.WbRegressionSet(project_id=body.project_id, name=body.name,
                           description=body.description or f"自动圈选 {len(cases)} 条 P0 用例")
    db.add(s)
    db.flush()
    _add_set_items(db, s, [c.id for c in cases])
    db.commit()
    return {"id": s.id, "added": s.case_count}


# ---------- 回归执行（RG-03/05） ----------


def _start_regression_run(db: Session, s: wb.WbRegressionSet, environment_id: int | None,
                          trigger_type: str):
    """创建回归执行记录 + 落地执行计划（plan_type=regression，进度汇入执行中心）。"""
    items = db.query(wb.WbRegressionSetItem).filter_by(set_id=s.id).all()
    if not items:
        raise HTTPException(400, f"回归集「{s.name}」为空，请先圈选用例")
    case_ids = [x.case_id for x in items]
    plan = wb.WbExecutionPlan(
        project_id=s.project_id, name=f"回归-{s.name}-{datetime.now().strftime('%m%d %H:%M')}",
        plan_type="regression", executor="auto" if trigger_type == "scheduled" else "local",
        total_count=len(case_ids), started_at=func.now(),
    )
    db.add(plan)
    db.flush()
    for cid in case_ids:
        db.add(wb.WbExecutionPlanCase(plan_id=plan.id, case_id=cid))
    run = wb.WbRegressionRun(
        set_id=s.id, project_id=s.project_id, plan_id=plan.id,
        trigger_type=trigger_type, status="running", started_at=func.now(),
    )
    db.add(run)
    db.flush()
    return run, plan


def _run_api_case(db: Session, run, case: wb.WbTestCase, environment_id: int | None) -> dict:
    """单条接口用例自动执行；失败自动重跑一次（RG-05）。返回执行摘要。"""
    snap = case.api_snapshot or {}
    kwargs = dict(
        project_id=run.project_id, environment_id=environment_id,
        request_id=snap.get("request_id"), method=snap.get("method") or "GET",
        url=snap.get("url"), headers=snap.get("headers"), query_params=snap.get("params"),
        body_type=snap.get("body_type"), body=snap.get("body"), auth=snap.get("auth"),
    )
    res = _do_send(db, **kwargs)
    ok = bool(res["status"] and 200 <= res["status"] < 400 and not res["error"])
    rerun = None
    if not ok:
        res2 = _do_send(db, **kwargs)
        ok2 = bool(res2["status"] and 200 <= res2["status"] < 400 and not res2["error"])
        rerun = {"status": res2["status"], "error": res2["error"], "duration_ms": res2["duration_ms"]}
        if ok2:
            res, ok = res2, True
    return {"res": res, "ok": ok, "rerun": rerun}


def _execute_regression(db: Session, run: wb.WbRegressionRun, plan: wb.WbExecutionPlan,
                        s: wb.WbRegressionSet, environment_id: int | None) -> list[dict]:
    """RG-03：回归执行引擎。接口用例自动执行（失败自动重跑一次，RG-05）；
    功能用例保持待执行（进入执行中心人工标记）；结果写执行历史。"""
    rows = (db.query(wb.WbExecutionPlanCase, wb.WbTestCase)
            .join(wb.WbTestCase, wb.WbExecutionPlanCase.case_id == wb.WbTestCase.id)
            .filter(wb.WbExecutionPlanCase.plan_id == plan.id)
            .order_by(wb.WbExecutionPlanCase.id).all())
    auto_results = []
    for pc, case in rows:
        snap = case.api_snapshot or {}
        if case.case_type != "api" or not snap.get("url"):
            continue  # 功能用例：留待人工执行（plan_case 保持 pending）
        try:
            r = _run_api_case(db, run, case, environment_id)
        except Exception as ex:
            pc.result, pc.note, pc.executed_at, pc.executor = "fail", f"执行异常：{ex}", func.now(), "auto"
            db.add(wb.WbExecutionRecord(plan_case_id=pc.id, case_id=pc.case_id,
                                        actual_result=f"执行异常：{ex}", executed_at=func.now()))
            auto_results.append({"case_id": case.id, "case_no": case.case_no, "title": case.title,
                                 "result": "fail", "error": str(ex)})
            continue
        res, ok, rerun = r["res"], r["ok"], r["rerun"]
        pc.result = "pass" if ok else "fail"
        pc.executor = "auto"
        pc.executed_at = func.now()
        if rerun and ok:
            pc.note = "首次失败已自动重跑通过（RG-05）"
        elif rerun:
            pc.note = "失败（已自动重跑一次仍未通过，RG-05）"
        else:
            pc.note = "回归自动执行"
        db.add(wb.WbExecutionRecord(
            plan_case_id=pc.id, case_id=pc.case_id,
            actual_result=f"HTTP {res['status']} · {res['duration_ms']}ms"
                          + (f" · {res['error']}" if res["error"] else ""),
            step_results=[{"status": res["status"], "duration_ms": res["duration_ms"],
                           "error": res["error"], "history_id": res["history_id"],
                           "rerun": rerun}],
            duration_ms=res["duration_ms"], executed_at=func.now(),
        ))
        if not ok:
            # RG-05：重跑仍失败 → 生成测试日志（回归域），便于追溯与关联 Bug
            db.add(wb.WbTestLog(
                project_id=run.project_id, plan_id=plan.id, log_type="regression",
                title=f"回归失败：{case.case_no} {case.title}",
                content=json.dumps({
                    "run_id": run.id, "plan_id": plan.id, "set_id": s.id,
                    "case_id": case.id, "case_no": case.case_no, "title": case.title,
                    "status": res["status"], "duration_ms": res["duration_ms"],
                    "error": res["error"], "rerun": rerun, "url": res.get("url"),
                }, ensure_ascii=False),
            ))
        auto_results.append({
            "case_id": case.id, "case_no": case.case_no, "title": case.title,
            "result": "pass" if ok else "fail", "status": res["status"],
            "duration_ms": res["duration_ms"], "error": res["error"], "rerun": rerun,
        })
    db.flush()
    _recount_plan(db, plan.id)
    _sync_regression_run(db, run)
    db.commit()
    return auto_results


def _sync_regression_run(db: Session, run: wb.WbRegressionRun):
    """由落地计划实时推导回归记录状态/通过率（人工补执行后自动完结）。"""
    p = db.get(wb.WbExecutionPlan, run.plan_id) if run.plan_id else None
    if not p:
        return
    if p.status == "completed" and run.status != "completed":
        run.status = "completed"
        # 注意：p.finished_at 可能是 func.now() SQL 表达式（_recount_plan 刚赋值未 flush），
        # 不能对其做布尔/比较运算，这里统一取当前时间
        run.finished_at = func.now()
        if p.total_count:
            run.pass_rate = round(p.pass_count / p.total_count * 100, 2)
        s = db.get(wb.WbRegressionSet, run.set_id)
        if s:
            s.last_run_at = run.finished_at
    elif p.status in ("running", "aborted") and run.status == "completed":
        run.status, run.finished_at = "running", None


class RegressionRunBody(BaseModel):
    set_id: int
    environment_id: int | None = None
    trigger_type: str = "manual"  # manual / scheduled


@router.post("/regression-runs")
def trigger_regression(body: RegressionRunBody, db: Session = Depends(get_db)):
    """RG-02/03：手动触发回归执行。"""
    if body.trigger_type not in ("manual", "scheduled"):
        raise HTTPException(400, "非法触发方式")
    s = db.get(wb.WbRegressionSet, body.set_id)
    if not s:
        raise HTTPException(404, "回归集不存在")
    run, plan = _start_regression_run(db, s, body.environment_id, body.trigger_type)
    auto_results = _execute_regression(db, run, plan, s, body.environment_id)
    pending = (db.query(wb.WbExecutionPlanCase).filter(
        wb.WbExecutionPlanCase.plan_id == plan.id, wb.WbExecutionPlanCase.result == "pending").count())
    return {"run_id": run.id, "plan_id": plan.id, "auto_results": auto_results,
            "pending_count": pending,
            "summary": {"total": plan.total_count, "pass": plan.pass_count,
                        "fail": plan.fail_count, "status": plan.status}}


@router.get("/regression-runs")
def list_regression_runs(project_id: int | None = None, set_id: int | None = None,
                         db: Session = Depends(get_db)):
    q = db.query(wb.WbRegressionRun)
    if project_id:
        q = q.filter(wb.WbRegressionRun.project_id == project_id)
    if set_id:
        q = q.filter(wb.WbRegressionRun.set_id == set_id)
    runs = q.order_by(wb.WbRegressionRun.id.desc()).limit(200).all()
    sname = {s.id: s.name for s in db.query(wb.WbRegressionSet).all()}
    out = []
    for r in runs:
        _sync_regression_run(db, r)
        p = db.get(wb.WbExecutionPlan, r.plan_id) if r.plan_id else None
        d = row(r, "id", "set_id", "project_id", "plan_id", "trigger_type", "status",
                "pass_rate", "started_at", "finished_at")
        d["set_name"] = sname.get(r.set_id, "")
        if p:
            d.update({"plan_name": p.name, "total_count": p.total_count, "pass_count": p.pass_count,
                      "fail_count": p.fail_count, "pending_count": p.total_count - p.pass_count - p.fail_count
                      - p.block_count - p.skip_count, "plan_status": p.status})
        else:
            d.update({"plan_name": "", "total_count": 0, "pass_count": 0, "fail_count": 0,
                      "pending_count": 0, "plan_status": ""})
        out.append(d)
    if runs:
        db.commit()
    return out


@router.delete("/regression-runs/{rid}")
def delete_regression_run(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbRegressionRun, rid)
    if not r:
        raise HTTPException(404, "回归记录不存在")
    db.delete(r)  # 关联的执行计划保留在执行中心历史中
    db.commit()
    return {"ok": True}


# ---------- 定时计划（RG-02） ----------


class ScheduleBody(BaseModel):
    set_id: int
    name: str | None = None
    cron: str
    environment_id: int | None = None
    enabled: int = 1


@router.get("/regression-schedules")
def list_regression_schedules(project_id: int, db: Session = Depends(get_db)):
    rows = (db.query(wb.WbRegressionSchedule).filter(wb.WbRegressionSchedule.project_id == project_id)
            .order_by(wb.WbRegressionSchedule.id.desc()).all())
    sname = {s.id: s.name for s in db.query(wb.WbRegressionSet).all()}
    ename = {e.id: e.name for e in db.query(wb.WbApiEnvironment).filter(
        wb.WbApiEnvironment.project_id == project_id).all()}
    return [{**row(x, "id", "set_id", "project_id", "name", "cron", "environment_id", "enabled",
                   "last_run_at", "next_run_at", "created_at"),
             "set_name": sname.get(x.set_id, ""),
             "environment_name": ename.get(x.environment_id, "默认环境")} for x in rows]


@router.post("/regression-schedules")
def create_regression_schedule(body: ScheduleBody, db: Session = Depends(get_db)):
    s = db.get(wb.WbRegressionSet, body.set_id)
    if not s:
        raise HTTPException(404, "回归集不存在")
    try:
        nxt = _cron_next(body.cron)
    except ValueError as ex:
        raise HTTPException(400, f"cron 表达式无效：{ex}")
    sch = wb.WbRegressionSchedule(
        set_id=s.id, project_id=s.project_id,
        name=body.name or f"{s.name}·定时回归", cron=body.cron.strip(),
        environment_id=body.environment_id, enabled=1 if body.enabled else 0, next_run_at=nxt,
    )
    db.add(sch)
    db.commit()
    return {"id": sch.id, "next_run_at": str(nxt)}


@router.put("/regression-schedules/{schid}")
def update_regression_schedule(schid: int, body: ScheduleBody, db: Session = Depends(get_db)):
    sch = db.get(wb.WbRegressionSchedule, schid)
    if not sch:
        raise HTTPException(404, "定时计划不存在")
    sch.cron = body.cron.strip()
    sch.environment_id = body.environment_id
    sch.enabled = 1 if body.enabled else 0
    if body.name:
        sch.name = body.name
    try:
        sch.next_run_at = _cron_next(sch.cron)
    except ValueError as ex:
        raise HTTPException(400, f"cron 表达式无效：{ex}")
    db.commit()
    return {"ok": True, "next_run_at": str(sch.next_run_at)}


@router.post("/regression-schedules/{schid}/toggle")
def toggle_regression_schedule(schid: int, db: Session = Depends(get_db)):
    sch = db.get(wb.WbRegressionSchedule, schid)
    if not sch:
        raise HTTPException(404, "定时计划不存在")
    sch.enabled = 0 if sch.enabled else 1
    if sch.enabled:
        sch.next_run_at = _cron_next(sch.cron)
    db.commit()
    return {"ok": True, "enabled": sch.enabled}


@router.post("/regression-schedules/{schid}/run-now")
def run_schedule_now(schid: int, db: Session = Depends(get_db)):
    sch = db.get(wb.WbRegressionSchedule, schid)
    if not sch:
        raise HTTPException(404, "定时计划不存在")
    s = db.get(wb.WbRegressionSet, sch.set_id)
    if not s:
        raise HTTPException(404, "回归集不存在")
    run, plan = _start_regression_run(db, s, sch.environment_id, "manual")
    auto_results = _execute_regression(db, run, plan, s, sch.environment_id)
    return {"run_id": run.id, "plan_id": plan.id, "auto_results": auto_results}


@router.delete("/regression-schedules/{schid}")
def delete_regression_schedule(schid: int, db: Session = Depends(get_db)):
    sch = db.get(wb.WbRegressionSchedule, schid)
    if not sch:
        raise HTTPException(404, "定时计划不存在")
    db.delete(sch)
    db.commit()
    return {"ok": True}


_scheduler_started = False


def start_regression_scheduler():
    """启动回归调度线程（main.py lifespan 调用，幂等）。每 30s 扫描到期的定时计划触发回归。"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        from models.database import create_session
        while True:
            time.sleep(30)
            db = None
            try:
                db = create_session()
                now = datetime.now()
                due = (db.query(wb.WbRegressionSchedule)
                       .filter(wb.WbRegressionSchedule.enabled == 1,
                               wb.WbRegressionSchedule.next_run_at != None,  # noqa: E711
                               wb.WbRegressionSchedule.next_run_at <= now).all())
                for sch in due:
                    try:
                        s = db.get(wb.WbRegressionSet, sch.set_id)
                        if s:
                            run, plan = _start_regression_run(db, s, sch.environment_id, "scheduled")
                            _execute_regression(db, run, plan, s, sch.environment_id)
                        sch.last_run_at = datetime.now()
                        sch.next_run_at = _cron_next(sch.cron)
                        db.commit()
                    except Exception as ex:
                        db.rollback()
                        logger.exception("回归计划 %s 触发失败", sch.id)
            except Exception as ex:
                logger.exception("回归调度循环异常: %s", ex)
            finally:
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass

    threading.Thread(target=_loop, daemon=True, name="regression-scheduler").start()


class BugRegressionBody(BaseModel):
    case_ids: list[int] = []  # 空 = 使用 Bug 关联用例
    environment_id: int | None = None


@router.post("/bugs/{bid}/regression")
def bug_regression(bid: int, body: BugRegressionBody, db: Session = Depends(get_db)):
    """RG-04：Bug 定向回归——「已修复」后对关联用例（或指定用例）发起回归验证。"""
    b = db.get(wb.WbBug, bid)
    if not b:
        raise HTTPException(404, "Bug 不存在")
    case_ids = body.case_ids or [lk.link_id for lk in
                                 db.query(wb.WbBugLink).filter_by(bug_id=bid, link_type="case").all()]
    if not case_ids:
        raise HTTPException(400, "Bug 未关联用例，请先在详情中关联用例后再发起回归验证")
    # get-or-create 定向回归集，条目与本次用例对齐
    s = (db.query(wb.WbRegressionSet)
         .filter(wb.WbRegressionSet.project_id == b.project_id,
                 wb.WbRegressionSet.name == f"定向回归-{b.bug_no}").first())
    if not s:
        s = wb.WbRegressionSet(project_id=b.project_id, name=f"定向回归-{b.bug_no}",
                               description=f"Bug {b.bug_no} 定向回归用例集")
        db.add(s)
        db.flush()
    else:
        db.query(wb.WbRegressionSetItem).filter_by(set_id=s.id).delete(synchronize_session=False)
        db.flush()
    _add_set_items(db, s, case_ids)
    db.commit()
    run, plan = _start_regression_run(db, s, body.environment_id, "manual")
    auto_results = _execute_regression(db, run, plan, s, body.environment_id)
    pending = (db.query(wb.WbExecutionPlanCase).filter(
        wb.WbExecutionPlanCase.plan_id == plan.id, wb.WbExecutionPlanCase.result == "pending").count())
    all_pass = plan.status == "completed" and plan.fail_count == 0 and pending == 0
    if not all_pass and plan.fail_count > 0 and b.status == "fixed":
        b.status = "regressed"  # 回归失败：流转为「回归失败」
        db.commit()
    return {"run_id": run.id, "plan_id": plan.id, "set_id": s.id, "auto_results": auto_results,
            "pending_count": pending, "all_pass": all_pass,
            "bug_status": b.status,
            "summary": {"total": plan.total_count, "pass": plan.pass_count, "fail": plan.fail_count}}


def _bug_regression_ready(db: Session, b: wb.WbBug) -> bool:
    """B-04/RG-04 关闭卡点判定：每个关联用例都存在回归执行中通过的记录。"""
    case_ids = [lk.link_id for lk in
                db.query(wb.WbBugLink).filter_by(bug_id=b.id, link_type="case").all()]
    if not case_ids:
        return False
    passed = {r[0] for r in (
        db.query(wb.WbExecutionPlanCase.case_id)
        .join(wb.WbRegressionRun, wb.WbRegressionRun.plan_id == wb.WbExecutionPlanCase.plan_id)
        .join(wb.WbExecutionPlan, wb.WbExecutionPlan.id == wb.WbRegressionRun.plan_id)
        .filter(wb.WbRegressionRun.project_id == b.project_id,
                wb.WbExecutionPlan.plan_type == "regression",
                wb.WbExecutionPlanCase.case_id.in_(case_ids),
                wb.WbExecutionPlanCase.result == "pass").all())}
    return all(cid in passed for cid in case_ids)


# ================= 自动化测试（EX-M1~M3 + L-04） =================

_AI_PROVIDERS = {
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
}


def _local_llm_conf(db: Session) -> tuple[str, str, str] | None:
    """本地大模型（OpenAI 兼容端点）配置：base_url 必填，model 默认 qwen2.5:7b。
    返回 (base_url, model, api_key)；未配置 base_url 返回 None。"""
    base_url = (_decrypt_wb_secret(db, "ai.local.base_url") or "").strip()
    if not base_url:
        return None
    model = (_decrypt_wb_secret(db, "ai.local.model") or "").strip() or "qwen2.5:7b"
    api_key = (_decrypt_wb_secret(db, "ai.local.api_key") or "").strip() or "local"
    return base_url.rstrip("/"), model, api_key


def _call_chat_llm(db: Session, scene: str, system: str, user: str, max_tokens: int = 3000) -> str:
    """通用 AI 调用（EX-M1 骨架生成 / EX-M3 失败分析共用）。
    优先本地大模型（OpenAI 兼容端点，离线可用），再依序尝试云厂商；全未配置抛 400 提示。"""
    import urllib.error
    import urllib.request

    providers: list[tuple[str, str, str, str]] = []  # (provider, base_url, model, api_key)
    local = _local_llm_conf(db)
    if local:
        providers.append(("local", local[0], local[1], local[2]))
    for provider, (base_url, model) in _AI_PROVIDERS.items():
        key = _decrypt_wb_secret(db, f"ai.{provider}.api_key")
        if key:
            providers.append((provider, base_url, model, key))

    last_err = None
    for provider, base_url, model, key in providers:
        start = time.time()
        try:
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps({
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "max_tokens": max_tokens, "temperature": 0.2,
                }).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode())
            text = body["choices"][0]["message"]["content"] or ""
            db.add(wb.WbAiCallLog(scene=scene, model_name=model,
                                  duration_ms=int((time.time() - start) * 1000), success=1))
            db.commit()
            return text
        except urllib.error.HTTPError as e:
            last_err = f"{provider} HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            last_err = f"{provider}: {e}"
        db.add(wb.WbAiCallLog(scene=scene, model_name=model, success=0, error_msg=last_err[:500]))
        db.commit()
    if last_err is None:
        raise HTTPException(400, "AI 能力未配置：请先在「设置 → AI 模型」中配置本地大模型端点或任一云模型 API key")
    raise HTTPException(400, f"AI 调用失败：{last_err}")


def _py_name(s: str) -> str:
    """用例标题转合法 Python 标识符片段。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "_", s or "").strip("_")
    return s[:40] or "case"


def _template_skeleton(case: wb.WbTestCase) -> str:
    """无 AI key 时的确定性模板骨架（人工补全断言）。"""
    steps = case.steps or []
    step_lines = []
    for i, st in enumerate(steps, 1):
        action = (st.get("action") if isinstance(st, dict) else None) or (st if isinstance(st, str) else "")
        step_lines.append(f"    # 步骤{i}：{action}")
        step_lines.append(f"    # TODO 人工补全：将步骤{i}翻译为 Playwright 操作（page.goto / page.click / page.fill ...）")
    if not step_lines:
        step_lines = ["    # TODO 人工补全：操作步骤（page.goto / page.click / page.fill ...）"]
    fn = f"test_{_py_name(case.case_no)}_{_py_name(case.title)}"
    pre = case.precondition or "无"
    return f'''"""用例 {case.case_no} {case.title}
前置条件：{pre}
预期结果：{case.expected or "（未填写）"}

由工作坊自动生成骨架，TODO 处人工补全后即可执行。
"""
from playwright.sync_api import Page, expect


def {fn}(page: Page):
{chr(10).join(step_lines)}
    # 预期结果断言
    # TODO 人工补全：expect(page.get_by_text("...")).to_be_visible() 等
    assert True, "骨架未补全：请编辑脚本后运行"
'''


def _ai_skeleton(db: Session, case: wb.WbTestCase) -> str:
    system = (
        "你是资深测试自动化工程师。根据功能测试用例生成 pytest + Playwright（sync API）测试脚本骨架。"
        "要求：1) 每个用例一个 test 函数，参数为 page: Page；2) 用例步骤写成注释 + 对应的 Playwright 操作示例代码（含合理占位选择器，"
        "不能确定的地方用 TODO 注释标出）；3) 预期结果用 expect 断言表达（选择器不确定处写 TODO）；"
        "4) 不确定的目标 URL 用 page.goto('https://TODO-填写实际地址') 占位；5) 只输出 Python 代码，不要解释。"
    )
    steps = json.dumps(case.steps or [], ensure_ascii=False)
    user = f"用例编号：{case.case_no}\n标题：{case.title}\n前置条件：{case.precondition or '无'}\n测试步骤：{steps}\n预期结果：{case.expected or '未填写'}"
    text = _call_chat_llm(db, "auto_script_gen", system, user)
    # 剥掉可能的 ```python 围栏
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


class AutoScriptBody(BaseModel):
    title: str | None = None
    script: str | None = None
    status: str | None = None
    case_id: int | None = None


@router.get("/auto-scripts")
def list_auto_scripts(project_id: int, db: Session = Depends(get_db)):
    rows = (db.query(wb.WbAutoScript).filter(wb.WbAutoScript.project_id == project_id)
            .order_by(wb.WbAutoScript.id.desc()).all())
    out = []
    for s in rows:
        d = row(s, "id", "project_id", "case_id", "case_no", "title", "gen_source", "status",
                "last_run_at", "created_at", "updated_at")
        last = (db.query(wb.WbAutoRun).filter(wb.WbAutoRun.script_id == s.id)
                .order_by(wb.WbAutoRun.id.desc()).first())
        d["last_run"] = row(last, "id", "status", "passed_count", "failed_count", "total_count",
                            "duration_ms", "finished_at") if last else None
        d["script_size"] = len(s.script or "")
        out.append(d)
    return out


class AutoGenerateBody(BaseModel):
    project_id: int
    case_ids: list[int] = []


@router.post("/auto-scripts/generate")
def generate_auto_scripts(body: AutoGenerateBody, db: Session = Depends(get_db)):
    """EX-M1：从用例库勾选用例 → AI 生成 pytest+Playwright 脚本骨架。
    AI 未配置时回退确定性模板（同样人工补全可用）。"""
    created = []
    for cid in body.case_ids:
        c = db.get(wb.WbTestCase, cid)
        if not c or c.project_id != body.project_id:
            continue
        try:
            code, source = _ai_skeleton(db, c), "ai"
        except HTTPException as e:
            if "AI 能力未配置" not in str(e.detail) and "AI 调用失败" not in str(e.detail):
                raise
            code, source = _template_skeleton(c), "template"
        s = wb.WbAutoScript(project_id=body.project_id, case_id=c.id, case_no=c.case_no,
                            title=f"{c.case_no} {c.title}", script=code, gen_source=source)
        db.add(s)
        db.flush()
        created.append({"id": s.id, "title": s.title, "gen_source": source})
    if not created:
        raise HTTPException(400, "没有可生成的用例（请检查用例归属）")
    db.commit()
    return {"created": created,
            "ai_used": all(x["gen_source"] == "ai" for x in created),
            "note": None if any(x["gen_source"] == "ai" for x in created)
                    else "AI key 未配置，已用内置模板生成骨架（编辑脚本补全后即可执行）"}


class AutoScriptCreateBody(BaseModel):
    project_id: int
    title: str
    script: str
    case_no: str | None = None


@router.post("/auto-scripts")
def create_auto_script(body: AutoScriptCreateBody, db: Session = Depends(get_db)):
    """手动导入脚本：粘贴或上传 .py 文件内容直接建脚本（status=ready 可执行）。"""
    if not body.script.strip():
        raise HTTPException(400, "脚本内容为空")
    if "def test_" not in body.script:
        raise HTTPException(400, "脚本中未找到 test 函数（需为 pytest 格式，如 def test_xxx(page):）")
    s = wb.WbAutoScript(project_id=body.project_id, case_id=None, case_no=body.case_no,
                        title=body.title.strip() or "手动导入脚本", script=body.script,
                        gen_source="manual", status="ready")
    db.add(s)
    db.commit()
    return {"id": s.id, "title": s.title, "gen_source": "manual"}


@router.get("/auto-scripts/{sid}")
def get_auto_script(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbAutoScript, sid)
    if not s:
        raise HTTPException(404, "自动化脚本不存在")
    d = row(s, "id", "project_id", "case_id", "case_no", "title", "script", "gen_source",
            "status", "last_run_at", "created_at", "updated_at")
    runs = (db.query(wb.WbAutoRun).filter(wb.WbAutoRun.script_id == sid)
            .order_by(wb.WbAutoRun.id.desc()).limit(20).all())
    d["runs"] = [row(r, "id", "status", "total_count", "passed_count", "failed_count",
                     "duration_ms", "created_at", "finished_at") for r in runs]
    return d


@router.put("/auto-scripts/{sid}")
def update_auto_script(sid: int, body: AutoScriptBody, db: Session = Depends(get_db)):
    """EX-M1 人工补全：保存脚本代码。"""
    s = db.get(wb.WbAutoScript, sid)
    if not s:
        raise HTTPException(404, "自动化脚本不存在")
    if body.title is not None:
        s.title = body.title
    if body.script is not None:
        s.script = body.script
        s.gen_source = "manual" if s.gen_source == "template" else s.gen_source
    if body.status is not None:
        s.status = body.status
    db.commit()
    return {"ok": True}


@router.delete("/auto-scripts/{sid}")
def delete_auto_script(sid: int, db: Session = Depends(get_db)):
    s = db.get(wb.WbAutoScript, sid)
    if not s:
        raise HTTPException(404, "自动化脚本不存在")
    db.delete(s)  # runs 级联删除（附件文件保留在磁盘）
    db.commit()
    return {"ok": True}


# ---------- 执行器（EX-M2：独立 pytest 子进程） ----------

_RUN_TIMEOUT_S = 300


def _collect_junit(xml_path: Path) -> dict:
    """解析 junit xml：总数/通过/失败/错误。"""
    import xml.etree.ElementTree as ET

    total = passed = failed = 0
    failed_names = []
    try:
        root = ET.parse(xml_path).getroot()
        suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
        for su in suites:
            total += int(su.get("tests", 0) or 0)
            failed += int(su.get("failures", 0) or 0) + int(su.get("errors", 0) or 0)
            for tc in su.iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    failed_names.append(f'{tc.get("classname","")}.{tc.get("name","")}')
        passed = max(0, total - failed)
    except Exception:
        pass
    return {"total": total, "passed": passed, "failed": failed, "failed_names": failed_names}


def _run_auto_script_in_pool(run_id: int, script_id: int):
    """后台线程：写脚本与产物目录 → pytest 子进程 → 解析结果 → 归档附件 → 日志留存。"""
    from models.database import create_session

    db = create_session()
    artifacts_dir: Path | None = None
    try:
        s = db.get(wb.WbAutoScript, script_id)
        run = db.get(wb.WbAutoRun, run_id)
        if not s or not run:
            return
        # 必须绝对路径：pytest.ini 会让 rootdir 变为 base，相对路径参数会错位拼接
        run_dir = (Path(get_upload_dir()).resolve() / "auto_runs" / str(run_id))
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = run_dir / "artifacts"
        test_file = run_dir / f"test_auto_{script_id}.py"
        test_file.write_text(s.script or "", encoding="utf-8")
        junit = run_dir / "junit.xml"

        run_args = [
            str(test_file), "-v", "--tb=short", "-p", "pytest_playwright",
            f"--junitxml={junit}",
            "--screenshot=only-on-failure", "--video=retain-on-failure",
            "--tracing=retain-on-failure", f"--output={artifacts_dir}",
            "-p", "no:cacheprovider",
        ]
        t0 = time.perf_counter()
        try:
            if getattr(sys, "frozen", False):
                # 冻结环境无独立 python.exe：server.exe 自调用 --run-pytest 子进程模式
                payload = run_dir / "pytest_args.json"
                payload.write_text(json.dumps(run_args), encoding="utf-8")
                result_file = run_dir / "pytest_result.json"
                proc = subprocess.run(
                    [sys.executable, "--run-pytest", str(payload), str(result_file)],
                    cwd=str(run_dir), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=_RUN_TIMEOUT_S)
                if result_file.exists():
                    r = json.loads(result_file.read_text(encoding="utf-8"))
                    out = (r.get("stdout") or "") + "\n" + (r.get("stderr") or "")
                    rc = int(r.get("rc") or 0)
                else:  # 子进程异常退出且未写结果
                    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
                    rc = proc.returncode if proc.returncode is not None else -1
            else:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", *run_args],
                    cwd=str(run_dir), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=_RUN_TIMEOUT_S)
                out = (proc.stdout or "") + "\n" + (proc.stderr or "")
                rc = proc.returncode
        except subprocess.TimeoutExpired as ex:
            out = (ex.stdout or "") if isinstance(ex.stdout, str) else ""
            out += "\n[执行超时：超过 300 秒已被终止]"
            rc = -9
        duration_ms = int((time.perf_counter() - t0) * 1000)

        # 输出只保留尾部 15000 字符（AI 分析与展示用）
        out_tail = out[-15000:]
        stats = _collect_junit(junit) if junit.exists() else {"total": 0, "passed": 0, "failed": 0, "failed_names": []}
        if stats["total"] == 0 and rc not in (0,):
            status = "error"  # 脚本语法错误/收集失败等
        elif stats["failed"] or rc not in (0,):
            status = "failed"
        else:
            status = "passed"

        run.status = status
        run.duration_ms = duration_ms
        run.total_count = stats["total"]
        run.passed_count = stats["passed"]
        run.failed_count = stats["failed"]
        run.output = out_tail
        run.finished_at = func.now()
        s.last_run_at = func.now()
        db.flush()

        # L-04 附件留存：截图/Trace/视频/报告随执行记录归档（附件表挂载）
        att_count = 0
        if artifacts_dir and artifacts_dir.exists():
            for f in sorted(artifacts_dir.rglob("*")):
                if f.is_file() and f.stat().st_size > 0 and f.stat().st_size <= 50 * 1024 * 1024:
                    db.add(wb.WbAttachment(
                        owner_type="auto_run", owner_id=run_id,
                        file_name=f.name, file_path=str(f),
                        file_size=f.stat().st_size,
                        mime_type={".png": "image/png", ".webm": "video/webm", ".zip": "application/zip",
                                   ".xml": "text/xml"}.get(f.suffix, "application/octet-stream"),
                    ))
                    att_count += 1
        db.flush()

        # 日志留存：执行摘要进日志管理（log_type=automation）
        db.add(wb.WbTestLog(
            project_id=run.project_id, log_type="automation",
            title=f"自动化执行：{s.title}（{status}）",
            content=json.dumps({
                "run_id": run.id, "script_id": s.id, "status": status,
                "total": stats["total"], "passed": stats["passed"], "failed": stats["failed"],
                "failed_names": stats["failed_names"], "duration_ms": duration_ms,
                "artifacts": att_count, "output_tail": out_tail[-2000:],
            }, ensure_ascii=False),
        ))
        db.commit()
    except Exception as ex:
        logger.exception("自动化执行 %s 异常", run_id)
        try:
            db.rollback()
            run = db.get(wb.WbAutoRun, run_id)
            if run:
                run.status, run.output, run.finished_at = "error", f"执行器异常：{ex}", func.now()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/client-errors")
def report_client_error(body: dict):
    """前端 JS 错误上报（window.onerror / unhandledrejection）：落入 workbench.log。"""
    try:
        payload = json.dumps(body, ensure_ascii=False)[:2000]
    except Exception:
        payload = str(body)[:2000]
    logging.getLogger("frontend").error("前端错误: %s", payload)
    return {"ok": True}


@router.post("/auto-scripts/{sid}/run")
def run_auto_script(sid: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    s = db.get(wb.WbAutoScript, sid)
    if not s:
        raise HTTPException(404, "自动化脚本不存在")
    if not (s.script or "").strip():
        raise HTTPException(400, "脚本为空，请先补全")
    run = wb.WbAutoRun(script_id=s.id, project_id=s.project_id, status="running")
    db.add(run)
    db.flush()
    run_id = run.id
    db.commit()
    background.add_task(_run_auto_script_in_pool, run_id, sid)
    return {"run_id": run_id}


@router.get("/auto-runs")
def list_auto_runs(project_id: int, db: Session = Depends(get_db)):
    rows = (db.query(wb.WbAutoRun, wb.WbAutoScript)
            .join(wb.WbAutoScript, wb.WbAutoRun.script_id == wb.WbAutoScript.id)
            .filter(wb.WbAutoRun.project_id == project_id)
            .order_by(wb.WbAutoRun.id.desc()).limit(100).all())
    return [{**row(r, "id", "script_id", "status", "duration_ms", "total_count", "passed_count",
                   "failed_count", "analyzed", "created_at", "finished_at"),
             "script_title": s.title, "case_no": s.case_no} for r, s in rows]


@router.get("/auto-runs/{rid}")
def get_auto_run(rid: int, db: Session = Depends(get_db)):
    r = db.get(wb.WbAutoRun, rid)
    if not r:
        raise HTTPException(404, "执行记录不存在")
    s = db.get(wb.WbAutoScript, r.script_id)
    d = row(r, "id", "script_id", "status", "duration_ms", "total_count", "passed_count",
            "failed_count", "output", "analysis", "analyzed", "created_at", "finished_at")
    d["script_title"] = s.title if s else ""
    atts = db.query(wb.WbAttachment).filter(
        wb.WbAttachment.owner_type == "auto_run", wb.WbAttachment.owner_id == rid).all()
    d["attachments"] = [row(a, "id", "file_name", "file_size", "mime_type", "uploaded_at") for a in atts]
    return d


@router.post("/auto-runs/{rid}/analyze")
def analyze_auto_run(rid: int, db: Session = Depends(get_db)):
    """EX-M3：AI 失败分析——读取 pytest 输出，给出原因与修复建议。"""
    r = db.get(wb.WbAutoRun, rid)
    if not r:
        raise HTTPException(404, "执行记录不存在")
    if r.status == "passed":
        raise HTTPException(400, "执行已通过，无需失败分析")
    s = db.get(wb.WbAutoScript, r.script_id)
    system = (
        "你是资深 Playwright 自动化测试专家。分析失败的 pytest 测试输出，"
        "输出：1) 失败原因（逐条，引用关键报错行）；2) 修复建议（具体到选择器/等待/环境层面）；"
        "3) 是否疑似环境问题（目标服务不可用/超时）。用中文，条理化，不超过 400 字。"
    )
    user = (f"脚本标题：{s.title if s else ''}\n脚本代码：\n{(s.script if s else '')[:4000]}\n\n"
            f"pytest 输出：\n{r.output or '（无输出）'}")
    text = _call_chat_llm(db, "auto_fail_analysis", system, user)
    r.analysis, r.analyzed = text, 1
    db.commit()
    return {"analysis": text}


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


@router.get("/ai-usage-stats")
def ai_usage_stats(db: Session = Depends(get_db)):
    """S-05 用量统计：汇总 AI 调用流水（wb_ai_call_logs）。"""
    from datetime import datetime, timedelta

    total = db.query(wb.WbAiCallLog).count()
    ok_n = db.query(wb.WbAiCallLog).filter(wb.WbAiCallLog.success == 1).count()
    pt = db.query(func.coalesce(func.sum(wb.WbAiCallLog.prompt_tokens), 0)).scalar() or 0
    ct = db.query(func.coalesce(func.sum(wb.WbAiCallLog.completion_tokens), 0)).scalar() or 0
    avg_ms = db.query(func.avg(wb.WbAiCallLog.duration_ms)).scalar()

    # 按模型
    by_model = []
    for name, calls, succ, p, c, ms in (
        db.query(
            wb.WbAiCallLog.model_name, func.count(wb.WbAiCallLog.id),
            func.sum(wb.WbAiCallLog.success), func.coalesce(func.sum(wb.WbAiCallLog.prompt_tokens), 0),
            func.coalesce(func.sum(wb.WbAiCallLog.completion_tokens), 0), func.avg(wb.WbAiCallLog.duration_ms),
        ).group_by(wb.WbAiCallLog.model_name).all()
    ):
        by_model.append({"model_name": name, "calls": calls, "success": int(succ or 0),
                         "prompt_tokens": int(p), "completion_tokens": int(c), "avg_ms": int(ms or 0)})

    # 按天（近 14 天）
    since = datetime.now() - timedelta(days=13)
    by_day = []
    for day, calls, succ in (
        db.query(func.date(wb.WbAiCallLog.created_at), func.count(wb.WbAiCallLog.id), func.sum(wb.WbAiCallLog.success))
        .filter(wb.WbAiCallLog.created_at >= since)
        .group_by(func.date(wb.WbAiCallLog.created_at)).order_by(func.date(wb.WbAiCallLog.created_at)).all()
    ):
        by_day.append({"day": str(day), "calls": calls, "success": int(succ or 0)})

    # 最近 20 条流水
    recent = []
    for r in db.query(wb.WbAiCallLog).order_by(wb.WbAiCallLog.id.desc()).limit(20).all():
        recent.append(row(r, "id", "scene", "model_name", "prompt_tokens", "completion_tokens",
                          "duration_ms", "success", "error_msg", "created_at"))

    return {
        "total": total, "success": ok_n,
        "success_rate": round(ok_n / total * 100, 1) if total else 0,
        "prompt_tokens": int(pt), "completion_tokens": int(ct),
        "avg_ms": int(avg_ms) if avg_ms else 0,
        "by_model": by_model, "by_day": by_day, "recent": recent,
    }


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
    """用库里配置的 key 做一次最小连通性测试；provider=local 走本地 OpenAI 兼容端点。"""
    import urllib.error
    import urllib.request

    if provider == "local":
        conf = _local_llm_conf(db)
        if not conf:
            raise HTTPException(400, "本地大模型未配置：请先填写服务端点（如 http://127.0.0.1:11434/v1）")
        url, model, key = conf
    else:
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
        model = models[provider]
    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                         "max_tokens": 5}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        db.add(wb.WbAiCallLog(scene="connectivity_test", model_name=model,
                              duration_ms=int((time.time() - start) * 1000), success=1))
        db.commit()
        return {"ok": True, "model": body.get("model"), "latency_ms": int((time.time() - start) * 1000)}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        db.add(wb.WbAiCallLog(scene="connectivity_test", model_name=model,
                              success=0, error_msg=f"HTTP {e.code}: {detail}"))
        db.commit()
        raise HTTPException(400, f"HTTP {e.code}: {detail}")
    except Exception as e:
        db.add(wb.WbAiCallLog(scene="connectivity_test", model_name=model,
                              success=0, error_msg=str(e)[:500]))
        db.commit()
        raise HTTPException(400, f"连通失败: {e}")


# ================= 知识库（K-01~03：桥接底座 knowledge_service/ChromaDB） =================
#
# 设计：每个工作坊项目对应底座 knowledge_bases 表中的一个库（description 存
# workshop_project:{id} 标记做映射，不改底座表结构）。文件复用底座
# KnowledgeFile 表与向量化管线（DocumentProcessor.save_to_chroma），检索复用
# ChromaRetriever。项目隔离 = 库映射；owner_user_id 置 None（单机无用户体系）。


_KB_ALLOWED_EXT = {".pdf", ".docx", ".md", ".txt"}
_KB_MARKER_PREFIX = "workshop_project:"


def _decrypt_wb_secret(db: Session, setting_key: str) -> str | None:
    """读取工作坊设置项并解密（is_secret=1 时为 Fernet 密文）。"""
    s = db.query(wb.WbSetting).filter(wb.WbSetting.setting_key == setting_key).first()
    if not s or not s.setting_value:
        return None
    if s.is_secret:
        try:
            return _secret_cipher().decrypt(s.setting_value.encode()).decode()
        except Exception:
            return None
    return s.setting_value


def _ensure_embedding_ready(db: Session) -> str | None:
    """确保 embedding 可用：优先 .env 的 EMBEDDING_API_KEY，缺失时回退设置页
    配置的通义千问（DashScope）key——text-embedding-v4 即 DashScope 模型。
    返回就绪来源（'env'/'settings'/None）。"""
    import os

    from config import get_embedding_config

    key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if key:
        return "env"
    qwen_key = _decrypt_wb_secret(db, "ai.qwen.api_key")
    if not qwen_key:
        return None
    os.environ["EMBEDDING_API_KEY"] = qwen_key
    if not os.getenv("EMBEDDING_BASE_URL", "").strip():
        os.environ["EMBEDDING_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    get_embedding_config.cache_clear()
    return "settings"


def _get_project_kb(db: Session, project_id: int):
    """取或创建项目对应的知识库（get-or-create）。"""
    from models.knowledge_models import KnowledgeBase

    marker = f"{_KB_MARKER_PREFIX}{project_id}"
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.description == marker).first()
    if kb:
        return kb
    import uuid as _uuid

    kb = KnowledgeBase(
        name=f"项目知识库 #{project_id}",
        description=marker,
        collection_name=f"wbkb_{_uuid.uuid4().hex[:16]}",
        owner_user_id=None,
        visibility="private",
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def _load_wb_document(file_path: str):
    """按扩展名加载并分块文档（pdf 走底座版面还原链路，docx/md/txt 走纯文本分块）。"""
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from utils.file_handle import get_document_processor

    ext = Path(file_path).suffix.lower()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200,
        separators=["\n\n", "。", "！", "？", "\n", " ", ""],
    )

    if ext == ".pdf":
        return get_document_processor().load_pdf(file_path)

    text = ""
    if ext == ".docx":
        from docx import Document as DocxDocument

        d = DocxDocument(file_path)
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            for r in table.rows:
                cells = [c.text.strip() for c in r.cells]
                if any(cells):
                    parts.append("| " + " | ".join(cells) + " |")
        text = "\n".join(parts)
    else:  # .md / .txt
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")

    text = text.strip()
    if not text:
        return []
    source = Path(file_path).name
    chunks = splitter.split_text(text)
    return [Document(page_content=c, metadata={"source": source}) for c in chunks if c.strip()]


def _process_wb_document(file_id: str, kb_id: str):
    """后台处理工作坊知识文档：解析 → 分块 → MySQL 分块表（关键词检索通道）
    + 向量化入 Chroma（语义检索通道，配置 Embedding Key 后启用）。"""
    import logging as _logging

    from models.knowledge_models import KnowledgeBase, KnowledgeFile
    from services.knowledge_service import (
        _refresh_kb_file_count,
        resolve_upload_path,
    )
    from utils.file_handle import get_document_processor

    log = _logging.getLogger("wb.knowledge")
    from models.database import create_session

    with create_session() as db:
        try:
            fr = (db.query(KnowledgeFile)
                  .filter(KnowledgeFile.id == file_id, KnowledgeFile.knowledge_base_id == kb_id)
                  .first())
            if not fr:
                log.error("知识文档记录不存在: %s", file_id)
                return
            fr.status = "processing"
            db.commit()

            path = resolve_upload_path(fr.file_path)
            if not path.exists():
                db.delete(fr)
                _refresh_kb_file_count(db, kb_id)
                db.commit()
                return

            splits = _load_wb_document(str(path))
            kb = db.get(KnowledgeBase, kb_id)

            # 通道一：分块入 MySQL（关键词检索，无外部依赖、始终可用）
            db.query(wb.WbKbChunk).filter(wb.WbKbChunk.file_id == file_id).delete()
            for i, sp in enumerate(splits):
                text = (sp.page_content or "").strip()
                if text:
                    db.add(wb.WbKbChunk(kb_id=kb_id, file_id=file_id, chunk_no=i, content=text))

            # 通道二：向量化入 Chroma（语义检索，Embedding Key 就绪时启用）
            chunk_count, skipped = 0, 0
            embed_ok = _ensure_embedding_ready(db) is not None
            if embed_ok and splits:
                chunk_count, skipped = get_document_processor().save_to_chroma(
                    splits=splits, collection_name=kb.collection_name,
                    file_metadata={"file_id": fr.id, "filename": fr.filename,
                                   "knowledge_base_id": kb_id},
                )
                if splits and chunk_count == 0:
                    raise RuntimeError("所有分片向量化失败，未能写入向量库")

            fr.chunk_count = chunk_count or sum(1 for sp in splits if (sp.page_content or "").strip())
            fr.skipped_chunks = skipped
            if skipped and skipped / max(1, chunk_count + skipped) >= 0.2:
                fr.status = "failed"
                fr.error = f"{skipped}/{chunk_count + skipped} 个分片向量化失败，请重试"
            else:
                fr.status = "completed"
                fr.error = f"{skipped}/{chunk_count + skipped} 个分片被跳过" if skipped else None
            _refresh_kb_file_count(db, kb_id)
            db.commit()
            log.info("知识文档处理完成: %s, 分片 %d, 跳过 %d, 语义索引 %s",
                     fr.filename, fr.chunk_count, skipped, "OK" if embed_ok else "未配置Key(仅关键词检索)")
        except Exception as e:
            db.rollback()
            fr = (db.query(KnowledgeFile)
                  .filter(KnowledgeFile.id == file_id, KnowledgeFile.knowledge_base_id == kb_id)
                  .first())
            if fr:
                fr.status = "failed"
                fr.error = str(e)[:500]
                db.commit()
            log.error("知识文档处理失败: %s", e, exc_info=True)


def _process_wb_document_in_pool(file_id: str, kb_id: str):
    """在底座文档线程池中执行（避免并发上传挤占请求线程）。"""
    from services.knowledge_service import get_document_pool

    get_document_pool().submit(_process_wb_document, file_id, kb_id).result()


class KnowledgeSearchBody(BaseModel):
    project_id: int
    query: str
    top_k: int = 5


@router.get("/knowledge")
def list_knowledge(project_id: int, db: Session = Depends(get_db)):
    """项目知识文档列表 + embedding 就绪状态。"""
    from models.knowledge_models import KnowledgeBase, KnowledgeFile

    marker = f"{_KB_MARKER_PREFIX}{project_id}"
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.description == marker).first()
    files = []
    if kb:
        files = (db.query(KnowledgeFile)
                 .filter(KnowledgeFile.knowledge_base_id == kb.id)
                 .order_by(KnowledgeFile.uploaded_at.desc()).all())
    return {
        "kb_id": kb.id if kb else None,
        "embedding_ready": _ensure_embedding_ready(db) is not None,
        "files": [
            row(f, "id", "filename", "file_size", "file_type", "status",
                "chunk_count", "skipped_chunks", "error", "uploaded_at")
            for f in files
        ],
    }


@router.post("/knowledge/upload")
async def upload_knowledge_file(
    background_tasks: BackgroundTasks,
    project_id: int = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传知识文档（pdf/docx/md/txt ≤50MB），后台分块入库（Embedding Key 就绪时同步向量化）。"""
    import hashlib
    import uuid as _uuid

    from models.knowledge_models import KnowledgeFile
    from services.knowledge_service import (
        _refresh_kb_file_count,
        get_upload_root,
    )

    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    ext = Path(file.filename).suffix.lower()
    if ext not in _KB_ALLOWED_EXT:
        raise HTTPException(400, "仅支持 PDF / Word(docx) / Markdown / TXT 文件")

    kb = _get_project_kb(db, project_id)
    save_path = (get_upload_root() / f"{_uuid.uuid4().hex}{ext}").resolve()

    try:
        total_size = 0
        digest = hashlib.sha256()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as buf:
            while chunk := file.file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > 50 * 1024 * 1024:
                    raise HTTPException(400, "文件大小不能超过 50MB")
                digest.update(chunk)
                buf.write(chunk)
        if total_size == 0:
            raise HTTPException(400, "文件内容不能为空")

        dup = (db.query(KnowledgeFile)
               .filter(KnowledgeFile.knowledge_base_id == kb.id,
                       KnowledgeFile.content_hash == digest.hexdigest(),
                       KnowledgeFile.status != "failed")
               .first())
        if dup:
            raise HTTPException(409, f"已存在相同内容的文件《{dup.filename}》，无需重复上传")

        fr = KnowledgeFile(
            knowledge_base_id=kb.id, filename=file.filename,
            file_path=save_path.name, file_size=total_size,
            file_type=ext.lstrip("."), status="pending",
            content_hash=digest.hexdigest(),
        )
        db.add(fr)
        db.commit()
        _refresh_kb_file_count(db, kb.id)
        db.refresh(fr)

        background_tasks.add_task(_process_wb_document_in_pool, fr.id, kb.id)
        return {"id": fr.id, "filename": fr.filename}
    except HTTPException:
        if save_path.exists():
            save_path.unlink()
        raise
    except Exception as e:
        if save_path.exists():
            save_path.unlink()
        raise HTTPException(500, f"上传失败: {e}")


@router.delete("/knowledge/files/{file_id}")
def delete_knowledge_file_wb(file_id: str, project_id: int, db: Session = Depends(get_db)):
    """删除文档：先删向量再删记录（镜像底座顺序）。"""
    from models.knowledge_models import KnowledgeBase, KnowledgeFile
    from services.knowledge_service import _refresh_kb_file_count, resolve_upload_path
    from utils.file_handle import get_document_processor

    kb = _get_project_kb(db, project_id)
    fr = (db.query(KnowledgeFile)
          .filter(KnowledgeFile.id == file_id, KnowledgeFile.knowledge_base_id == kb.id)
          .first())
    if not fr:
        raise HTTPException(404, "文档不存在")

    if fr.status in ("completed", "failed"):
        ok = get_document_processor().delete_documents_by_file_id(kb.collection_name, fr.id)
        if not ok and fr.status == "completed":
            raise HTTPException(500, "向量删除失败，请稍后重试")

    # 清理关键词检索通道的分块
    db.query(wb.WbKbChunk).filter(wb.WbKbChunk.file_id == fr.id).delete()

    path = None
    try:
        path = resolve_upload_path(fr.file_path)
    except HTTPException:
        pass

    db.delete(fr)
    _refresh_kb_file_count(db, kb.id)
    db.commit()
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass
    return {"ok": True}


@router.post("/knowledge/files/{file_id}/retry")
def retry_knowledge_file_wb(file_id: str, project_id: int,
                            background_tasks: BackgroundTasks,
                            db: Session = Depends(get_db)):
    """重试失败文档（重置为 pending 并重新入队处理）。"""
    from models.knowledge_models import KnowledgeFile

    kb = _get_project_kb(db, project_id)
    fr = (db.query(KnowledgeFile)
          .filter(KnowledgeFile.id == file_id, KnowledgeFile.knowledge_base_id == kb.id)
          .first())
    if not fr:
        raise HTTPException(404, "文档不存在")
    fr.status = "pending"
    fr.error = None
    db.commit()
    background_tasks.add_task(_process_wb_document_in_pool, fr.id, kb.id)
    return {"ok": True}


@router.post("/knowledge/files/{file_id}/reindex")
def reindex_knowledge_file_wb(file_id: str, project_id: int,
                              background_tasks: BackgroundTasks,
                              db: Session = Depends(get_db)):
    """重建索引：先清该文档的旧向量再重新处理。"""
    from models.knowledge_models import KnowledgeFile
    from utils.file_handle import get_document_processor

    kb = _get_project_kb(db, project_id)
    fr = (db.query(KnowledgeFile)
          .filter(KnowledgeFile.id == file_id, KnowledgeFile.knowledge_base_id == kb.id)
          .first())
    if not fr:
        raise HTTPException(404, "文档不存在")
    get_document_processor().delete_documents_by_file_id(kb.collection_name, fr.id)
    fr.status = "pending"
    fr.error = None
    db.commit()
    background_tasks.add_task(_process_wb_document_in_pool, fr.id, kb.id)
    return {"ok": True}


@router.post("/knowledge/search")
async def search_knowledge_wb(body: KnowledgeSearchBody, db: Session = Depends(get_db)):
    """知识检索：配置 Embedding Key 时走 Chroma 语义检索；
    未配置时回退 MySQL 分块表关键词检索（开箱即用）。"""
    from models.knowledge_models import KnowledgeBase
    from utils.file_handle import get_chromadb_client
    from utils.retriever import ChromaRetriever

    if not (body.query or "").strip():
        raise HTTPException(400, "检索内容不能为空")

    marker = f"{_KB_MARKER_PREFIX}{body.project_id}"
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.description == marker).first()
    if not kb:
        return {"results": [], "total_chunks": 0, "mode": "keyword"}

    top_k = max(1, min(body.top_k or 5, 20))

    # 语义通道
    if _ensure_embedding_ready(db):
        client = get_chromadb_client()
        try:
            collection = client.get_collection(name=kb.collection_name)
        except Exception:
            collection = None
        if collection and collection.count():
            retriever = ChromaRetriever(collection_name=kb.collection_name, chroma_client=client)
            try:
                docs = await retriever.get_relevant_documents(body.query.strip(), n_results=top_k)
            except Exception as e:
                detail = str(e)
                if "api_key" in detail.lower() or "401" in detail or "auth" in detail.lower():
                    raise HTTPException(400, "Embedding 调用失败：API Key 无效或已过期")
                raise HTTPException(500, f"检索失败: {detail[:200]}")
            results = []
            for d in docs:
                meta = d.metadata or {}
                results.append({
                    "content": d.page_content,
                    "source": meta.get("filename") or meta.get("source") or "",
                    "page": meta.get("page"),
                    "file_id": meta.get("file_id"),
                })
            return {"results": results, "total_chunks": collection.count(), "mode": "semantic"}

    # 关键词通道（无 Embedding Key 或语义集合为空）
    from models.knowledge_models import KnowledgeFile

    terms = [t for t in re.split(r"[\s,，。;；]+", body.query.strip()) if t]
    if not terms:
        return {"results": [], "total_chunks": 0, "mode": "keyword"}

    total = db.query(func.count(wb.WbKbChunk.id)).filter(wb.WbKbChunk.kb_id == kb.id).scalar() or 0
    like = lambda t: f"%{t}%"
    rows = (db.query(wb.WbKbChunk, KnowledgeFile.filename)
            .outerjoin(KnowledgeFile, KnowledgeFile.id == wb.WbKbChunk.file_id)
            .filter(wb.WbKbChunk.kb_id == kb.id)
            .filter(or_(*[wb.WbKbChunk.content.like(like(t)) for t in terms]))
            .all())

    def score(content: str) -> int:
        return sum(content.count(t) for t in terms)

    ranked = sorted(rows, key=lambda r: score(r[0].content or ""), reverse=True)[:top_k]
    results = [{
        "content": r[0].content,
        "source": r[1] or "(文件已删除)",
        "page": None,
        "file_id": r[0].file_id,
    } for r in ranked]
    return {"results": results, "total_chunks": total, "mode": "keyword"}


# ================= 云同步（客户端配置端点 + 服务器推拉端点） =================

import hashlib as _hashlib
import os as _os
import time as _time
import uuid as _uuid

import wbsync


@router.get("/sync/status")
def sync_status():
    """客户端同步状态（本机端点，无鉴权）。"""
    return wbsync.status_snapshot()


class SyncConfigBody(BaseModel):
    cloud_url: str = ""
    token: str = ""
    client_name: str = ""
    enabled: bool = True


@router.post("/sync/config")
def sync_config(body: SyncConfigBody):
    """启用/更新/停用云同步。启用时注册客户端并一次性平移存量 ID。"""
    try:
        if body.enabled:
            return wbsync.enable_sync(body.cloud_url, body.token, body.client_name)
        return wbsync.disable_sync()
    except Exception as ex:
        raise HTTPException(400, f"同步配置失败：{ex}")


@router.post("/sync/now")
def sync_now():
    """触发一次立即同步。"""
    wbsync.trigger_sync_now()
    return {"ok": True}


def _check_sync_token(x_sync_token: str = _Header(default="")):
    """服务器侧推拉端点鉴权：WB_SYNC_TOKEN 未配置 = 云同步未启用。"""
    expected = (_os.getenv("WB_SYNC_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "服务器未启用云同步（未配置 WB_SYNC_TOKEN）")
    if x_sync_token != expected:
        raise HTTPException(401, "同步访问令牌无效")


@router.get("/sync/ping")
def sync_ping(_t: None = _Depends(_check_sync_token)):
    return {"ok": True, "server_time": _time.time_ns() // 1000}


@router.post("/sync/register")
def sync_register(body: dict, _t: None = _Depends(_check_sync_token)):
    """客户端注册：分配 client_id 与 ID 区间序号（ordinal）。"""
    from models.database import create_session

    name = str(body.get("client_name") or "客户端")[:50]
    with create_session() as db:
        count = db.execute(_text(
            "SELECT COUNT(*) FROM wb_sync_state WHERE state_key LIKE 'client:%'")).scalar() or 0
        client_id = _uuid.uuid4().hex
        ordinal = int(count) + 1
        db.execute(_text(
            "INSERT INTO wb_sync_state(state_key, state_value) VALUES(:k, :v)"),
            {"k": f"client:{client_id}", "v": json.dumps(
                {"name": name, "ordinal": ordinal,
                 "registered_at": datetime.now().isoformat()}, ensure_ascii=False)})
        db.commit()
    logger.info("同步客户端注册：%s（ordinal=%d）", name, ordinal)
    return {"client_id": client_id, "ordinal": ordinal}


@router.post("/sync/push")
def sync_push(body: dict, _t: None = _Depends(_check_sync_token)):
    """客户端推送变更行：按 stamp LWW 落库，被拒行说明服务器已有更新版本。"""
    rows = body.get("rows") or []
    accepted, rejected = [], []
    now_us = _time.time_ns() // 1000
    max_future = now_us + 5 * 60 * 1_000_000  # 客户端时钟超前的钳制上限
    tables = wbsync._synced_tables()
    eng = get_engine()
    conn = eng.connect()
    try:
        with wbsync.apply_bypass(conn):
            for r in rows:
                try:
                    tname = str(r.get("table") or "")
                    pk = int(r.get("pk"))
                    stamp = int(r.get("stamp") or 0)
                    t = tables.get(tname)
                    if t is None or pk <= 0 or stamp <= 0:
                        raise ValueError("行参数不完整")
                    if stamp > max_future:
                        stamp = now_us  # 时钟超前钳制
                    existing = conn.execute(_text(
                        "SELECT stamp FROM wb_sync_rowver WHERE table_name = :t AND row_pk = :p"),
                        {"t": tname, "p": pk}).first()
                    if existing and int(existing[0]) >= stamp:
                        rejected.append({"table": tname, "pk": pk, "reason": "server_newer"})
                        continue
                    client = str(r.get("client") or "client")[:40]
                    if r.get("op") == "d":
                        hard, null = wbsync._collect_cascade_victims(conn, [(tname, pk)])
                        conn.execute(_text(f"DELETE FROM `{tname}` WHERE id = :p"), {"p": pk})
                        wbsync._rv_write(conn, tname, pk, stamp, client, deleted=True, dirty=False)
                        wbsync._apply_cascade_aftermath(conn, hard, null, stamp, client)
                    else:
                        wbsync._upsert_row(conn, t, r.get("data") or {})
                        wbsync._rv_write(conn, tname, pk, stamp, client, deleted=False, dirty=False)
                    accepted.append({"table": tname, "pk": pk})
                except Exception as ex:
                    rejected.append({"table": str(r.get("table")), "pk": r.get("pk"),
                                     "reason": str(ex)[:200]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"accepted": accepted, "rejected": rejected}


@router.get("/sync/pull")
def sync_pull(cursor_stamp: int = 0, cursor_id: int = 0, limit: int = 500,
              _t: None = _Depends(_check_sync_token)):
    """客户端拉取服务器变更（游标 = (stamp, rowver.id)，整数纪元微秒）。"""
    limit = max(1, min(2000, limit))
    tables = wbsync._synced_tables()
    eng = get_engine()
    out = []
    with eng.connect() as conn:
        rvs = conn.execute(_text(
            "SELECT id, table_name, row_pk, stamp, deleted FROM wb_sync_rowver "
            "WHERE stamp > :cs OR (stamp = :cs AND id > :ci) ORDER BY stamp, id LIMIT :n"),
            {"cs": cursor_stamp, "ci": cursor_id, "n": limit}).fetchall()
        for rid, tname, pk, stamp, deleted in rvs:
            t = tables.get(tname)
            item = {"table": tname, "pk": pk, "stamp": int(stamp), "deleted": bool(deleted)}
            if t is not None and not deleted:
                r = conn.execute(t.select().where(t.c.id == pk)).first()
                if r is None:
                    item["deleted"] = True  # 有版本但行已不在：按墓碑返回
                else:
                    item["data"] = wbsync._row_to_payload(t, r)
            out.append(item)
        last = rvs[-1] if rvs else None
    return {
        "rows": out,
        "has_more": len(rvs) >= limit,
        "cursor_stamp": int(last[3]) if last else cursor_stamp,
        "cursor_id": int(last[0]) if last else cursor_id,
    }


@router.get("/sync/files/list")
def sync_files_list(_t: None = _Depends(_check_sync_token)):
    """动态扫描本机 uploads 清单（附件 + auto_runs 产物），两端对称对账。"""
    files = wbsync._local_file_inventory()
    return {"files": [{"rel_path": k, "sha256": v["sha256"], "size": v["size"]}
                      for k, v in files.items()]}


def _safe_rel_path(rel: str) -> str:
    """路径校验：禁止绝对路径/目录穿越/盘符。"""
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/") or ":" in rel:
        raise HTTPException(400, "非法文件路径")
    return rel


@router.post("/sync/files/upload")
async def sync_files_upload(request: _Request, path: str, _t: None = _Depends(_check_sync_token)):
    """产物/附件上传（raw 字节流，path 为 uploads 下相对路径）。"""
    rel = _safe_rel_path(path)
    data = await request.body()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "文件过大")
    target = Path(get_upload_dir()).resolve() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    sha = _hashlib.sha256(data).hexdigest()
    target.write_bytes(data)
    from models.database import create_session
    with create_session() as db:
        if db.bind.dialect.name == "mysql":
            db.execute(_text(
                "INSERT INTO wb_sync_files(rel_path, sha256, size) VALUES(:r, :s, :z) "
                "ON DUPLICATE KEY UPDATE sha256 = :s, size = :z, updated_at = CURRENT_TIMESTAMP"),
                {"r": rel, "s": sha, "z": len(data)})
        else:
            db.execute(_text(
                "INSERT INTO wb_sync_files(rel_path, sha256, size) VALUES(:r, :s, :z) "
                "ON CONFLICT(rel_path) DO UPDATE SET sha256 = :s, size = :z, "
                "updated_at = CURRENT_TIMESTAMP"),
                {"r": rel, "s": sha, "z": len(data)})
        db.commit()
    return {"ok": True, "sha256": sha, "size": len(data)}


@router.get("/sync/files/download")
def sync_files_download(path: str, _t: None = _Depends(_check_sync_token)):
    rel = _safe_rel_path(path)
    target = Path(get_upload_dir()).resolve() / rel
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return _Response(content=target.read_bytes(), media_type="application/octet-stream")