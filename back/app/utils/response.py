# -*- coding: utf-8 -*-
"""统一响应封装"""
from flask import jsonify


def success(data=None, message="操作成功", code=200):
    """成功响应"""
    return jsonify({"code": code, "message": message, "data": data}), code


def fail(message="操作失败", code=400, data=None):
    """失败响应"""
    return jsonify({"code": code, "message": message, "data": data}), code


def paginate(query, page, page_size, schema=None, item_mapper=None):
    """分页响应

    :param item_mapper: 可选的可调用对象，接收 pagination.items 返回 list。
                        用于自定义字段拼装（如多表 JOIN 结果）。
                        若未提供，默认按 to_dict 或 schema 序列化。
    """
    pagination = query.paginate(page=page, per_page=page_size, error_out=False)
    if item_mapper is not None:
        items = item_mapper(pagination.items)
    else:
        items = pagination.items
        if schema:
            items = schema.dump(items, many=True)
        else:
            items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items]
    return {
        "items": items,
        "total": pagination.total,
        "page": pagination.page,
        "page_size": pagination.per_page,
        "pages": pagination.pages,
    }
