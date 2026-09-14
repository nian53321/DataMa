# -*- coding: utf-8 -*-
"""系统管理接口：用户、权限、规范、脱敏配置、密钥管理

路由层职责：
- 解析 request 参数
- 获取 JWT 身份
- 调用 service 层处理业务
- 构造 response

业务逻辑全部委托给 app.services 下的 service 类：
- UserService           用户 CRUD
- StandardService       规范 + 受试者模板
- DesensitizeService    脱敏配置
- RoleService           角色菜单管理
- EncryptionService     密钥管理
- ScanConfigService     扫描配置
"""
import io
from flask import request, current_app, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import system_bp
from app.models import Role
from app.services import (
    UserService, StandardService, DesensitizeService, RoleService,
    EncryptionService, ScanConfigService, ExternalKeyService,
)
from app.utils.response import success, fail
from app.utils.decorators import role_required
from app.utils.audit import current_role


def _svc_user():
    return UserService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_standard():
    return StandardService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_desensitize():
    return DesensitizeService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_role():
    return RoleService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_encryption():
    return EncryptionService(
        operator_id=int(get_jwt_identity()),
        operator_role=current_role(),
        data_lake_dir=current_app.config["DATA_LAKE_DIR"],
    )


def _svc_scan_config():
    return ScanConfigService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_external_key():
    return ExternalKeyService(
        operator_id=int(get_jwt_identity()),
        operator_role=current_role(),
        data_lake_dir=current_app.config["DATA_LAKE_DIR"],
    )


# ====================== 用户管理 ======================

@system_bp.route("/users", methods=["GET"])
@role_required(Role.ADMIN)
def list_users():
    """用户列表（支持按用户名/姓名/角色搜索）"""
    result = _svc_user().list_users(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        keyword=(request.args.get("keyword") or "").strip(),
        role=(request.args.get("role") or "").strip(),
    )
    return success(result)


@system_bp.route("/users", methods=["POST"])
@role_required(Role.ADMIN)
def create_user():
    """管理员创建用户"""
    data = request.get_json(silent=True) or {}
    user = _svc_user().create_user(data)
    return success(user.to_dict(), message="创建成功", code=201)


@system_bp.route("/users/<int:user_id>", methods=["PUT"])
@role_required(Role.ADMIN)
def update_user(user_id):
    """管理员更新用户信息（可改密码）"""
    data = request.get_json(silent=True) or {}
    user = _svc_user().update_user(user_id, data)
    return success(user.to_dict(), message="用户信息已更新")


@system_bp.route("/users/<int:user_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_user(user_id):
    """管理员删除用户"""
    username = _svc_user().delete_user(user_id)
    return success(message=f"用户 {username} 已删除")


# ====================== 规范管理 ======================

@system_bp.route("/standards", methods=["GET"])
@jwt_required()
def list_standards():
    """规范列表（命名规范/数据字典/元数据模板）"""
    items = _svc_standard().list_standards(
        standard_type=request.args.get("type", "").strip()
    )
    return success(items)


@system_bp.route("/standards", methods=["POST"])
@role_required(Role.ADMIN, Role.ENGINEER)
def create_standard():
    """创建规范"""
    data = request.get_json(silent=True) or {}
    standard = _svc_standard().create_standard(data)
    return success(standard.to_dict(), message="创建成功", code=201)


@system_bp.route("/standards/<int:standard_id>", methods=["PUT"])
@role_required(Role.ADMIN, Role.ENGINEER)
def update_standard(standard_id):
    """更新规范"""
    data = request.get_json(silent=True) or {}
    standard = _svc_standard().update_standard(standard_id, data)
    return success(standard.to_dict(), message="更新成功")


@system_bp.route("/standards/<int:standard_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_standard(standard_id):
    """删除规范"""
    name = _svc_standard().delete_standard(standard_id)
    return success(message=f"规范 {name} 已删除")


# ====================== 脱敏配置 ======================

@system_bp.route("/desensitize/config", methods=["GET"])
@jwt_required()
def get_desensitize_config():
    """获取脱敏配置（总开关 + 规则列表）"""
    return success(_svc_desensitize().get_config())


@system_bp.route("/desensitize/config", methods=["PUT"])
@role_required(Role.ADMIN)
def update_desensitize_config():
    """保存脱敏配置（管理员）

    请求体：{ enabled: bool, rules: [{id?, field_key, field_label, algorithm,
              keep_head, keep_tail, mask_char, is_active, sort_order?}] }
    """
    data = request.get_json(silent=True) or {}
    result = _svc_desensitize().update_config(data)
    return success(result, message="脱敏配置已保存")


@system_bp.route("/desensitize/preview", methods=["POST"])
@jwt_required()
def desensitize_preview():
    """脱敏效果预览：基于当前配置规则对样例数据做脱敏对比"""
    data = request.get_json(silent=True) or {}
    result = _svc_desensitize().preview(data.get("sample"))
    return success(result, message="预览成功")


# ====================== 角色菜单权限管理 ======================

@system_bp.route("/menus", methods=["GET"])
@jwt_required()
def list_menus():
    """返回系统菜单定义 + 当前用户可访问的菜单 key 列表"""
    uid = int(get_jwt_identity())
    return success(_svc_role().list_menus(uid))


@system_bp.route("/role-menus", methods=["GET"])
@role_required(Role.ADMIN)
def list_role_menus():
    """返回所有角色的菜单授权配置（管理员查看）"""
    return success(_svc_role().list_role_menus())


@system_bp.route("/roles", methods=["GET"])
@jwt_required()
def list_roles_simple():
    """返回启用的角色列表（供下拉选择使用，所有登录用户可访问）"""
    only_active = request.args.get("only_active", "1") != "0"
    return success(_svc_role().list_roles_simple(only_active))


@system_bp.route("/role-menus", methods=["POST"])
@role_required(Role.ADMIN)
def create_role_def():
    """创建自定义角色（含菜单授权）"""
    data = request.get_json(silent=True) or {}
    result = _svc_role().create_role_def(data)
    return success(result, message="角色创建成功", code=201)


@system_bp.route("/role-menus/<role_key>/info", methods=["PUT"])
@role_required(Role.ADMIN)
def update_role_info(role_key):
    """更新角色基本信息（label/description/is_active），不影响菜单授权"""
    data = request.get_json(silent=True) or {}
    role_def = _svc_role().update_role_info(role_key, data)
    return success(role_def.to_dict(), message="角色信息已更新")


@system_bp.route("/role-menus/<role_key>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_role_def(role_key):
    """删除自定义角色（系统内置角色不可删除）"""
    label = _svc_role().delete_role_def(role_key)
    return success(message=f"角色【{label}】已删除")


@system_bp.route("/role-menus/<role_key>", methods=["PUT"])
@role_required(Role.ADMIN)
def update_role_menus(role_key):
    """更新某角色的菜单授权（admin 角色不可修改）"""
    data = request.get_json(silent=True) or {}
    result = _svc_role().update_role_menus(role_key, data)
    return success(result, message="权限已更新")


# ==================== 密钥管理 ====================

@system_bp.route("/encryption/info", methods=["GET"])
@role_required(Role.ADMIN)
def encryption_info():
    """获取加密系统信息：主密钥指纹、创建时间、加密文件统计"""
    return success(_svc_encryption().get_info())


@system_bp.route("/encryption/rotate", methods=["POST"])
@role_required(Role.ADMIN)
def encryption_rotate():
    """轮换主密钥：生成新密钥并重新加密所有已加密文件（需二次确认密码）"""
    data = request.get_json(silent=True) or {}
    result = _svc_encryption().rotate(data.get("confirm_password", ""))
    return success(result, message=f"密钥轮换成功，已重新加密 {result['reencrypted_count']} 个文件")


@system_bp.route("/encryption/verify", methods=["POST"])
@role_required(Role.ADMIN)
def encryption_verify():
    """验证主密钥完整性：尝试解密样本文件"""
    result = _svc_encryption().verify()
    return success(result, message=result["message"])


@system_bp.route("/encryption/backup", methods=["POST"])
@role_required(Role.ADMIN)
def encryption_backup():
    """下载主密钥备份文件（仅管理员，需二次确认密码）

    返回二进制密钥文件，文件名包含指纹和时间戳
    """
    data = request.get_json(silent=True) or {}
    key_bytes, filename = _svc_encryption().backup(data.get("confirm_password", ""))
    buf = io.BytesIO(key_bytes)
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


@system_bp.route("/encryption/import", methods=["POST"])
@role_required(Role.ADMIN)
def encryption_import():
    """上传/替换主密钥：上传 .key 文件，用新密钥重新加密所有已加密文件（需二次密码确认）"""
    data = request.form
    confirm_password = data.get("confirm_password", "")
    key_file = request.files.get("key_file")
    if not key_file:
        return fail("请选择密钥文件", 422)
    key_bytes = key_file.read()
    result = _svc_encryption().import_key(key_bytes, confirm_password)
    return success(result, message=f"密钥替换成功，已重新加密 {result['reencrypted_count']} 个文件")


# ==================== 外部密钥管理（AES-256-CBC） ====================

@system_bp.route("/external-keys", methods=["GET"])
@role_required(Role.ADMIN)
def list_external_keys():
    """外部密钥列表（不返回 key/iv 明文，仅指纹）"""
    return success(_svc_external_key().list_keys())


@system_bp.route("/external-keys/<int:key_id>", methods=["GET"])
@role_required(Role.ADMIN)
def get_external_key(key_id):
    """获取单个外部密钥详情（默认不返回明文，?include_secret=1 返回 key/iv 用于编辑）"""
    include_secret = request.args.get("include_secret", "0") == "1"
    return success(_svc_external_key().get_key(key_id, include_secret=include_secret))


@system_bp.route("/external-keys", methods=["POST"])
@role_required(Role.ADMIN)
def create_external_key():
    """创建外部密钥

    请求体：{name, description?, key_b64, iv_b64, is_active?}
    """
    data = request.get_json(silent=True) or {}
    key = _svc_external_key().create_key(data)
    return success(key.to_dict(), message="外部密钥创建成功", code=201)


@system_bp.route("/external-keys/<int:key_id>", methods=["PUT"])
@role_required(Role.ADMIN)
def update_external_key(key_id):
    """更新外部密钥（name/description/key_b64/iv_b64/is_active 任意字段）

    请求体：{name?, description?, key_b64?, iv_b64?, is_active?}
    更新 key/iv 时必须同时提供两者
    """
    data = request.get_json(silent=True) or {}
    key = _svc_external_key().update_key(key_id, data)
    return success(key.to_dict(), message="外部密钥已更新")


@system_bp.route("/external-keys/<int:key_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_external_key(key_id):
    """删除外部密钥"""
    name = _svc_external_key().delete_key(key_id)
    return success(message=f"外部密钥「{name}」已删除")


@system_bp.route("/external-keys/<int:key_id>/download", methods=["GET"])
@role_required(Role.ADMIN)
def download_external_key(key_id):
    """下载外部密钥的 密钥.txt 文件（与导入格式互逆，可直接再导入）

    仅管理员；导出记审计日志
    """
    content, filename = _svc_external_key().export_keyfile(key_id)
    return send_file(
        io.BytesIO(content),
        as_attachment=True,
        download_name=filename,
        mimetype="text/plain",
    )


@system_bp.route("/external-keys/import", methods=["POST"])
@role_required(Role.ADMIN)
def import_external_key():
    """从 密钥.txt 文件导入外部密钥

    表单字段：
      key_file: 密钥.txt 文件（CRYPTO_AES_KEY=xxx / CRYPTO_AES_IV=xxx）
      name: 密钥名称（必填）
      description: 描述（可选）
    """
    key_file = request.files.get("key_file")
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not key_file:
        return fail("请选择密钥文件", 422)
    if not name:
        return fail("密钥名称不能为空", 422)

    # 保存上传的密钥文件到临时路径，复用 load_external_key 解析
    import tempfile, os
    fd, tmp_path = tempfile.mkstemp(suffix=".txt")
    try:
        os.write(fd, key_file.read())
        os.close(fd)
        key = _svc_external_key().import_from_keyfile(tmp_path, name, description)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return success(key.to_dict(), message=f"外部密钥「{name}」导入成功", code=201)


@system_bp.route("/external-keys/<int:key_id>/verify", methods=["POST"])
@role_required(Role.ADMIN)
def verify_external_key(key_id):
    """验证指定外部密钥能否解密指定加密文件

    请求体：{file_path: 绝对路径或相对 DATA_LAKE_DIR 的路径}
    """
    import os
    data = request.get_json(silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        return fail("待验证的文件路径不能为空", 422)
    # 支持相对路径（相对于 DATA_LAKE_DIR）
    if not os.path.isabs(file_path):
        file_path = os.path.join(current_app.config["DATA_LAKE_DIR"], file_path)
    # 路径安全：仅允许 DATA_LAKE_DIR 内的 .enc 文件（realpath 前缀校验），
    # 防止伪造路径读取服务器任意文件
    _lake_dir = os.path.realpath(current_app.config["DATA_LAKE_DIR"])
    _real_path = os.path.realpath(file_path)
    if not (_real_path == _lake_dir or _real_path.startswith(_lake_dir + os.sep)):
        return fail("无效的验证路径（仅允许数据湖内文件）", 422)
    if not _real_path.lower().endswith(".enc"):
        return fail("无效的验证路径（仅支持 .enc 加密文件）", 422)
    result = _svc_external_key().verify_key_with_file(key_id, _real_path)
    return success(result, message=result["message"])


@system_bp.route("/external-keys/enc-files", methods=["GET"])
@role_required(Role.ADMIN)
def list_enc_files():
    """列出 DATA_LAKE_DIR 下所有 .enc 文件（用于验证密钥弹窗的文件选择器）

    查询参数：
      keyword: 路径子串过滤（不区分大小写，可选）
      limit:   最多返回条目数，默认 500
    """
    keyword = (request.args.get("keyword") or "").strip()
    limit = request.args.get("limit", 500, type=int)
    return success(_svc_external_key().list_enc_files(keyword=keyword, limit=limit))



# ==================== 受试者信息模板 ====================

@system_bp.route("/subject-template", methods=["GET"])
@jwt_required()
def get_subject_template():
    """获取受试者信息模板（含字段列表与可用字段类型）"""
    return success(_svc_standard().get_subject_template())


@system_bp.route("/subject-template", methods=["PUT"])
@role_required(Role.ADMIN)
def update_subject_template():
    """保存受试者信息模板（管理员）"""
    data = request.get_json(silent=True) or {}
    result = _svc_standard().update_subject_template(data.get("fields", []))
    return success(result, message="受试者信息模板已保存")


# ==================== 受试者文件夹自动扫描 ====================

@system_bp.route("/scan-configs", methods=["GET"])
@role_required(Role.ADMIN, Role.ENGINEER)
def list_scan_configs():
    """获取所有扫描配置"""
    return success(_svc_scan_config().list_scan_configs())


@system_bp.route("/scan-configs", methods=["POST"])
@role_required(Role.ADMIN, Role.ENGINEER)
def create_scan_config():
    """新增扫描配置"""
    data = request.get_json(silent=True) or {}
    config = _svc_scan_config().create_scan_config(data)
    # 重启调度器
    from app.utils.scanner import _schedule_next
    _schedule_next(current_app._get_current_object())
    return success(config.to_dict(), message="扫描配置已创建")


@system_bp.route("/scan-configs/<int:config_id>", methods=["PUT"])
@role_required(Role.ADMIN, Role.ENGINEER)
def update_scan_config(config_id):
    """更新扫描配置"""
    data = request.get_json(silent=True) or {}
    config = _svc_scan_config().update_scan_config(config_id, data)
    from app.utils.scanner import _schedule_next
    _schedule_next(current_app._get_current_object())
    return success(config.to_dict(), message="扫描配置已更新")


@system_bp.route("/scan-configs/<int:config_id>", methods=["DELETE"])
@role_required(Role.ADMIN, Role.ENGINEER)
def delete_scan_config(config_id):
    """删除扫描配置"""
    _svc_scan_config().delete_scan_config(config_id)
    from app.utils.scanner import _schedule_next
    _schedule_next(current_app._get_current_object())
    return success(message="扫描配置已删除")


@system_bp.route("/scan-configs/<int:config_id>/run", methods=["POST"])
@role_required(Role.ADMIN, Role.ENGINEER)
def run_scan_now(config_id):
    """立即执行一次扫描

    返回 {new_count, failures} —— failures 非空时前端应提示用户
    到「系统设置 → 外部密钥管理」上传对应密钥
    """
    result = _svc_scan_config().run_scan_now(config_id)
    msg = f"扫描完成，新增 {result['new_count']} 个受试者"
    if result.get("failures"):
        msg += f"；{len(result['failures'])} 个文件解密失败，请到「外部密钥管理」上传对应密钥"
    return success(result, message=msg)
