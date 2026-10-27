# -*- coding: utf-8 -*-
"""
应用启动入口：创建 Flask 应用、注册 RESTX、加载各路由命名空间、解析命令行参数并运行
"""

import argparse  # 解析命令行参数
import os
from flask import Flask  # Flask 框架
from flask_restx import Api  # Flask-RESTX，用于Swagger文档
from flask_cors import CORS  # 跨域支持

# 导入自定义路由（各 namespace 在这些模块里注册）
from backend.api.routes.align import register_align_namespace  # 注册对齐相关API
from backend.api.routes.plot import register_plot_namespace  # 注册绘图上传API
from backend.api.routes.mysql_api import register_mysql_namespace  # 注册MySQL查询API
from backend.api.routes.regions import register_region_namespace  # /regions
from backend.api.routes.gold_curation import register_gold_curation_namespace  # /gold-curation
from backend.api.routes.literature import register_literature_namespace  # /literature（文献检索，只读）
from backend.api.routes.admin import register_admin_routes  # 内部管理台（隐藏）
from backend.infra.config.runtime_settings import initialize_runtime_settings


def create_app() -> Flask:
    """创建并配置 Flask 应用"""  # 函数用途
    app = Flask(__name__)  # 实例化Flask应用
    CORS(app)  # 允许跨域，便于前端/Swagger直连调用
    app.config['ERROR_404_HELP'] = False  # 关闭RESTX对404的多余提示
    app.secret_key = (
        os.getenv("APP_SECRET_KEY")
        or os.getenv("FLASK_SECRET_KEY")
        or os.urandom(32).hex()
    )
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = (
        (os.getenv("SESSION_COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes", "on")
    )

    # 读取并注入白名单运行时配置（若存在 runtime_settings.json）。
    initialize_runtime_settings()

    # 创建 RESTX Api（统一文档入口 /docs）
    api = Api(
        app,  # 绑定到当前app
        version="1.1",  # 版本号
        title="SprinzlMap API",  # 文档标题
        description="A service for tRNA Sprinzl numbering and reference comparison.",  # 描述
        doc="/docs"  # Swagger 文档路径
    )

    # 注册命名空间
    register_align_namespace(api)  # 注册 /align 与 /align/export
    register_plot_namespace(api)   # 注册 /plot/clustermap 与 /plot/tree
    register_mysql_namespace(api)  # 注册 /mysql/query
    register_region_namespace(api)  # 注册 /regions/lookup
    register_gold_curation_namespace(api)  # 注册 /gold-curation
    register_literature_namespace(api)  # 注册 /literature
    register_admin_routes(app)  # 隐藏管理台（仅在 ADMIN_ENABLED=1 且配置完整时启用）

    return app  # 返回应用实例

def main() -> None:
    """CLI entry point for local development."""
    parser = argparse.ArgumentParser(description="tRNA 对齐 Flask 服务")  # 命令行解析器
    parser.add_argument("--port", "-p", type=int, default=5679, help="HTTP 服务端口，默认 5679")  # 端口参数
    parser.add_argument("--host", type=str, default="0.0.0.0", help="HTTP 服务地址，默认 0.0.0.0")  # 主机地址
    args = parser.parse_args()  # 解析入参

    app.run(host=args.host, port=args.port)  # 启动服务


# 创建全局 app 实例，供 gunicorn / flask 直接引用
app = create_app()


if __name__ == "__main__":  # 仅当直接运行本文件时执行
    main()
