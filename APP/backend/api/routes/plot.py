# -*- coding: utf-8 -*-  # UTF-8
"""
绘图上传路由：接收对齐CSV字节并返回聚类热图PNG或Newick树
"""  # 模块说明

import io  # 内存字节流
from pathlib import Path  # 处理文件名
from flask import send_file, request, Response  # 响应文件/获取请求
from flask_restx import Namespace, Resource, fields  # RESTX 组件
from werkzeug.datastructures import FileStorage  # 文件类型
from backend.infra.plotting.clustermap_tool import (
    csv_bytes_to_clustermap_bytes,
    csv_bytes_to_clustermap_svg_bytes,
)  # 绘图函数
from backend.infra.plotting.tree_tool import csv_bytes_to_newick  # 系统发育树 Newick

def register_plot_namespace(api) -> None:
    """注册 /plot/clustermap 命名空间"""  # 函数说明
    ns_plot = Namespace("plot", description="Visualisation utilities")  # 命名空间

    # RESTX 的上传解析器（用于Swagger展示）
    upload_parser = ns_plot.parser()  # 创建解析器
    upload_parser.add_argument(
        "file",  # 字段名
        location="files",  # 从表单文件区读取
        type=FileStorage,  # 类型为文件
        required=True,  # 必需
        help="Alignment CSV file (see /align output)",  # 帮助文本
    )  # 结束字段定义

    error_model = ns_plot.model("PlotError", {
        "message": fields.String(description="Error message"),
    })

    @ns_plot.route("/clustermap", strict_slashes=False)  # POST /plot/clustermap
    class ClusterMap(Resource):  # 资源类
        @ns_plot.doc(consumes=["multipart/form-data"])  # 声明表单类型
        @ns_plot.expect(upload_parser)  # 期望的参数
        @ns_plot.produces(["image/png"])  # 返回类型PNG
        @ns_plot.response(200, "PNG image")  # 成功
        @ns_plot.response(400, "Bad file", error_model)  # 错误
        def post(self):  # 处理POST
            """Upload two-line alignment CSV and return clustermap PNG."""  # 接口说明
            args = upload_parser.parse_args()  # 解析表单
            file_obj = args.get("file")  # 取文件
            if not isinstance(file_obj, FileStorage):  # 类型检查
                return {"message": "No file provided (field name should be 'file')."}, 400  # 返回400
            f: FileStorage = file_obj  # 显式类型收窄
            try:  # 读取字节并绘图
                stem = Path(f.filename or "alignment").stem or "alignment"  # 兜底文件名
                raw = f.read()  # 读字节
                if not raw:  # 空文件
                    return {"message": "Empty file content."}, 400  # 报错
                png_bytes = csv_bytes_to_clustermap_bytes(raw)  # 转换为PNG
            except Exception as e:  # 异常处理
                return {"message": f"Failed to draw clustermap: {e}"}, 400  # 返回错误
            return send_file(  # 以文件流形式返回PNG
                io.BytesIO(png_bytes),  # 字节流
                mimetype="image/png",  # PNG类型
                as_attachment=True,  # 作为附件下载
                download_name=f"{stem}_clustermap.png",  # 下载名
            )  # 结束返回

    @ns_plot.route("/clustermap_svg", strict_slashes=False)  # POST /plot/clustermap_svg
    class ClusterMapSvg(Resource):
        @ns_plot.doc(consumes=["multipart/form-data"])
        @ns_plot.expect(upload_parser)
        @ns_plot.produces(["image/svg+xml"])
        @ns_plot.response(200, "SVG image")
        @ns_plot.response(400, "Bad file", error_model)
        def post(self):
            """Upload alignment CSV and return clustermap SVG."""
            args = upload_parser.parse_args()
            file_obj = args.get("file")
            if not isinstance(file_obj, FileStorage):
                return {"message": "No file provided (field name should be 'file')."}, 400
            f: FileStorage = file_obj
            try:
                stem = Path(f.filename or "alignment").stem or "alignment"
                raw = f.read()
                if not raw:
                    return {"message": "Empty file content."}, 400
                svg_bytes = csv_bytes_to_clustermap_svg_bytes(raw)
            except Exception as e:
                return {"message": f"Failed to draw clustermap: {e}"}, 400
            return send_file(
                io.BytesIO(svg_bytes),
                mimetype="image/svg+xml",
                as_attachment=True,
                download_name=f"{stem}_clustermap.svg",
            )

    @ns_plot.route("/tree", strict_slashes=False)  # POST /plot/tree
    class PhyloTree(Resource):
        @ns_plot.doc(consumes=["multipart/form-data"])
        @ns_plot.expect(upload_parser)
        @ns_plot.produces(["text/plain"])
        @ns_plot.response(200, "Newick tree")
        @ns_plot.response(400, "Bad file", error_model)
        def post(self):
            """Upload alignment CSV and return Newick tree."""
            args = upload_parser.parse_args()
            file_obj = args.get("file")
            if not isinstance(file_obj, FileStorage):
                return {"message": "No file provided (field name should be 'file')."}, 400
            f: FileStorage = file_obj
            try:
                raw = f.read()
                if not raw:
                    return {"message": "Empty file content."}, 400
                newick = csv_bytes_to_newick(raw)
            except Exception as e:
                return {"message": f"Failed to build tree: {e}"}, 400
            return Response(newick, mimetype="text/plain")

    api.add_namespace(ns_plot)  # 挂载命名空间
