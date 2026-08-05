# -*- coding: utf-8 -*-
"""标注接口：预标注、人工标注、医生复核、质量看板、标签管理

路由层职责：
- 解析 request 参数
- 获取 JWT 身份
- 调用 service 层处理业务
- 构造 response

业务逻辑全部委托给 app.services 下的 service 类：
- AnnotationService  标注任务 CRUD + 状态流转 + 质量看板
- LabelService       标签库 CRUD + 使用统计 + 样本标签查询
"""
from flask import request
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import annotation_bp
from app.models import Role
from app.services import AnnotationService, LabelService
from app.utils.response import success, fail
from app.utils.decorators import role_required
from app.utils.audit import current_role


def _svc_annotation():
    return AnnotationService(
        operator_id=int(get_jwt_identity()),
        operator_role=current_role(),
    )


def _svc_label():
    return LabelService(
        operator_id=int(get_jwt_identity()),
        operator_role=current_role(),
    )


# ==================== 标签查询（标注页面使用） ====================

@annotation_bp.route("/labels", methods=["GET"])
@jwt_required()
def preset_labels():
    """获取标签体系（优先从数据库读取，数据库为空时回退到硬编码）"""
    data_type = request.args.get("data_type", "").strip()
    return success(_svc_label().preset_labels(data_type))


# ==================== 标注任务查询 ====================

@annotation_bp.route("/tasks", methods=["GET"])
@jwt_required()
def list_tasks():
    """标注任务列表（支持按状态/标注员/数据资产/数据类型/分组/关键字过滤）"""
    result = _svc_annotation().list_tasks(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        status=request.args.get("status", "").strip(),
        annotator_id=request.args.get("annotator_id", type=int),
        reviewer_id=request.args.get("reviewer_id", type=int),
        data_asset_id=request.args.get("data_asset_id", type=int),
        data_type=request.args.get("data_type", "").strip(),
        group_id=request.args.get("group_id", type=int),
        mine=request.args.get("mine", type=int),
        ids_only=request.args.get("ids_only", "false").lower() == "true",
    )
    return success(result)


@annotation_bp.route("/annotators", methods=["GET"])
@jwt_required()
def list_annotators():
    """获取可分配的人员列表（admin/annotator/doctor）"""
    return success(_svc_annotation().list_annotators())


@annotation_bp.route("/tasks/<int:task_id>", methods=["GET"])
@jwt_required()
def task_detail(task_id):
    """任务详情：含数据资产信息、已标注内容"""
    return success(_svc_annotation().task_detail(task_id))


@annotation_bp.route("/available-assets", methods=["GET"])
@jwt_required()
def available_assets():
    """获取可用于创建标注任务的数据资产（排除已有非驳回状态任务的资产）"""
    result = _svc_annotation().available_assets(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 200, type=int),
        data_type=request.args.get("data_type", "").strip(),
        subject_id=request.args.get("subject_id", type=int),
        keyword=request.args.get("keyword", "").strip(),
    )
    return success(result)


@annotation_bp.route("/tasks/group/<int:group_id>", methods=["GET"])
@jwt_required()
def task_group(group_id):
    """获取任务分组中的所有任务（用于工作台翻页导航）"""
    return success(_svc_annotation().task_group(group_id))


@annotation_bp.route("/my-tasks", methods=["GET"])
@jwt_required()
def my_tasks():
    """获取当前用户的所有标注任务（用于工作台全量翻页标注）"""
    result = _svc_annotation().my_tasks(
        status_param=request.args.get("status", "").strip(),
        data_type=request.args.get("data_type", "").strip(),
    )
    return success(result)


@annotation_bp.route("/quality/dashboard", methods=["GET"])
@jwt_required()
def quality_dashboard():
    """质量看板：完成率/合格率/复核通过率"""
    return success(_svc_annotation().quality_dashboard())


# ==================== 标注任务 CRUD ====================

@annotation_bp.route("/tasks", methods=["POST"])
@role_required(Role.ADMIN, Role.ANNOTATOR)
def create_task():
    """创建标注任务（关联数据资产）"""
    data = request.get_json(silent=True) or {}
    task = _svc_annotation().create_task(data)
    return success(task.to_dict(), message="任务创建成功", code=201)


@annotation_bp.route("/tasks/batch", methods=["POST"])
@role_required(Role.ADMIN, Role.ANNOTATOR)
def batch_create_tasks():
    """批量创建标注任务（同一批共享 group_id，便于工作台翻页标注）"""
    data = request.get_json(silent=True) or {}
    result = _svc_annotation().batch_create_tasks(data)
    skipped = result["skipped"]
    return success(
        result,
        message=f"成功创建 {result['count']} 个标注任务"
                + (f"，跳过 {len(skipped)} 个" if skipped else ""),
        code=201,
    )


@annotation_bp.route("/tasks/<int:task_id>", methods=["PUT"])
@role_required(Role.ADMIN, Role.ANNOTATOR, Role.DOCTOR)
def update_task(task_id):
    """编辑标注任务（可改标注员/医生/备注）"""
    data = request.get_json(silent=True) or {}
    task = _svc_annotation().update_task(task_id, data)
    return success(task.to_dict(), message="任务已更新")


@annotation_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_task(task_id):
    """删除标注任务（级联删除其标注与版本记录）"""
    _svc_annotation().delete_task(task_id)
    return success(message="任务已删除")


@annotation_bp.route("/tasks/batch-delete", methods=["POST"])
@role_required(Role.ADMIN)
def batch_delete_tasks():
    """批量删除标注任务（级联删除标注结果与版本记录）"""
    data = request.get_json(silent=True) or {}
    result = _svc_annotation().batch_delete_tasks(data.get("task_ids", []))
    return success(
        result,
        message=f"成功删除 {result['deleted_count']} 个任务",
    )


# ==================== 任务状态流转 ====================

@annotation_bp.route("/tasks/<int:task_id>/pre-annotate", methods=["POST"])
@role_required(Role.ADMIN, Role.ANNOTATOR)
def pre_annotate(task_id):
    """触发自动预标注"""
    task = _svc_annotation().pre_annotate(task_id)
    return success(task.to_dict(), message="预标注完成")


@annotation_bp.route("/tasks/<int:task_id>/assign", methods=["POST"])
@role_required(Role.ADMIN, Role.ANNOTATOR)
def assign_task(task_id):
    """分配标注任务（不传 annotator_id 则分配给当前用户）"""
    data = request.get_json(silent=True) or {}
    task = _svc_annotation().assign_task(task_id, data.get("annotator_id"))
    return success(task.to_dict(), message="已分配，开始标注")


@annotation_bp.route("/tasks/batch-assign", methods=["POST"])
@role_required(Role.ADMIN)
def batch_assign_tasks():
    """批量分配标注任务"""
    data = request.get_json(silent=True) or {}
    result = _svc_annotation().batch_assign_tasks(
        data.get("task_ids", []), data.get("annotator_id"),
    )
    skipped = result["skipped"]
    return success(
        result,
        message=f"成功分配 {result['assigned_count']} 个任务"
                + (f"，跳过 {len(skipped)} 个" if skipped else ""),
    )


@annotation_bp.route("/tasks/<int:task_id>/annotate", methods=["POST"])
@role_required(Role.ADMIN, Role.ANNOTATOR)
def submit_annotation(task_id):
    """提交人工标注结果（覆盖式保存标签列表）"""
    data = request.get_json(silent=True) or {}
    task = _svc_annotation().submit_annotation(task_id, data.get("annotations", []))
    return success(task.to_dict(), message="标注已提交")


@annotation_bp.route("/tasks/<int:task_id>/review", methods=["POST"])
@role_required(Role.DOCTOR, Role.ADMIN)
def review_task(task_id):
    """医生复核：确认/驳回 + 电子签字"""
    data = request.get_json(silent=True) or {}
    task = _svc_annotation().review_task(task_id, data)
    return success(task.to_dict(), message="复核完成")


# ==================== 标签管理 ====================

@annotation_bp.route("/labels-manage", methods=["GET"])
@jwt_required()
def list_labels():
    """标签库列表（支持按模态筛选、关键词搜索、启用状态筛选）"""
    items = _svc_label().list_labels(
        data_type=request.args.get("data_type", "").strip(),
        keyword=request.args.get("keyword", "").strip(),
        is_active=request.args.get("is_active", "").strip(),
    )
    return success(items)


@annotation_bp.route("/labels-manage", methods=["POST"])
@role_required(Role.ADMIN)
def create_label():
    """新增标签"""
    data = request.get_json(silent=True) or {}
    label = _svc_label().create_label(data)
    return success(label.to_dict(), message="标签已创建")


@annotation_bp.route("/labels-manage/<int:label_id>", methods=["PUT"])
@role_required(Role.ADMIN)
def update_label(label_id):
    """修改标签（value 变更时同步更新所有标注记录）"""
    data = request.get_json(silent=True) or {}
    label = _svc_label().update_label(label_id, data)
    return success(label.to_dict(), message="标签已更新")


@annotation_bp.route("/labels-manage/<int:label_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_label(label_id):
    """删除标签（已被标注使用时禁止删除，需先替换）"""
    _svc_label().delete_label(label_id)
    return success(message="标签已删除")


@annotation_bp.route("/labels-manage/usage", methods=["GET"])
@jwt_required()
def label_usage():
    """标签使用统计：每个标签被多少标注使用"""
    return success(_svc_label().label_usage())


@annotation_bp.route("/labels-manage/<int:label_id>/replace", methods=["POST"])
@role_required(Role.ADMIN)
def replace_label(label_id):
    """批量替换标签：将所有使用该标签的标注替换为目标标签"""
    data = request.get_json(silent=True) or {}
    result = _svc_label().replace_label(label_id, data)
    return success(result, message=f"已替换 {result['affected']} 条标注")


@annotation_bp.route("/sample-labels", methods=["GET"])
@jwt_required()
def sample_labels():
    """样本标签情况：按数据资产/受试者聚合查看标注标签分布"""
    result = _svc_label().sample_labels(
        subject_id=request.args.get("subject_id", type=int),
        data_type=request.args.get("data_type", "").strip(),
        label_value=request.args.get("label", "").strip(),
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
    )
    return success(result)
