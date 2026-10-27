# -*- coding: utf-8 -*-  # UTF-8 编码
"""
MySQL 查询路由：/mysql/query
- 默认表：trna_records
- 默认返回：该表的全部列（SELECT *）
- 支持：POST 里传 table、filters、limit
- 安全：表名格式校验 + information_schema 校验 + 过滤列存在性校验 + 参数化占位
- 新增：/mysql/get_species 查询物种信息（dbname, name, domain）
- 新增：/mysql/get_trna_filters 查询特定物种的isotype和anticodon选项
"""  # 模块说明

from flask import request  # 获取请求体
from flask_restx import Namespace, Resource, fields  # RESTX 组件
import pymysql  # MySQL 客户端
import re  # 表名正则校验
from typing import Dict, List, Tuple, Any, Optional  # 类型注解

from backend.infra.config.mysql_settings import load_shared_mysql_config

MYSQL_CONFIG: Optional[Dict[str, Any]] = load_shared_mysql_config()

# =========================
# 工具函数
# =========================

def _sanitize_identifier(name: str) -> str:
    """严格校验 SQL 标识符（仅允许字母/数字/下划线）"""  # 防止 SQL 注入
    if not isinstance(name, str):  # 必须是字符串
        raise ValueError("Identifier must be a string.")  # 抛错
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):  # 仅允许 A-Za-z0-9_
        raise ValueError("Invalid identifier (only letters, numbers, underscore).")  # 非法表名
    return name  # 返回合法表名

def _table_exists(conn, schema: str, table: str) -> bool:
    """在 information_schema 校验表是否存在"""  # 校验存在性
    sql = """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema=%s AND table_name=%s
        LIMIT 1
    """  # 查询语句
    with conn.cursor() as cur:  # 打开游标
        cur.execute(sql, (schema, table))  # 参数化执行
        return cur.fetchone() is not None  # 有结果即存在

def _get_table_columns(conn, schema: str, table: str) -> List[str]:
    """获取指定表全部列名（用别名 col 统一大小写，避免 'column_name' KeyError）"""  # 说明
    sql = """
        SELECT column_name AS col
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
    """  # 使用 AS col 统一键名
    with conn.cursor() as cur:  # 打开游标
        cur.execute(sql, (schema, table))  # 参数化执行
        rows = cur.fetchall()  # 取全部
    return [r["col"] for r in rows]  # 返回列名列表（保证键为 'col'）

def _build_where_clause(filters: Dict[str, Any], valid_cols: List[str]) -> Tuple[str, List[Any], List[str]]:
    """
    根据 filters 构建 WHERE 与参数：
      - 仅对存在于 valid_cols 的列生效
      - 标量 -> `col = %s`
      - list/tuple -> `col IN (%s, %s, ...)`
    返回：(where_sql, params, ignored_keys)
    """  # 说明
    where_parts: List[str] = []  # 条件片段
    params: List[Any] = []  # 参数值
    ignored: List[str] = []  # 被忽略的过滤键
    for key, val in (filters or {}).items():  # 遍历过滤器
        if key not in valid_cols:  # 列不存在
            ignored.append(key)  # 记录被忽略的键
            continue  # 跳过
        if isinstance(val, (list, tuple)) and len(val) > 0:  # 序列值 -> IN
            placeholders = ", ".join(["%s"] * len(val))  # 生成占位符
            where_parts.append(f"`{key}` IN ({placeholders})")  # 拼 IN 子句
            params.extend(list(val))  # 扩展参数
        elif isinstance(val, (str, int, float)):  # 标量值 -> 等值
            where_parts.append(f"`{key}` = %s")  # 拼等值条件
            params.append(val)  # 追加参数
        # 其他复杂类型（如范围/LIKE）可按需扩展，这里先忽略
    if not where_parts:  # 没有有效条件
        return "", [], ignored  # 返回空 WHERE
    return " WHERE " + " AND ".join(where_parts), params, ignored  # 返回结果

# =========================
# 路由注册
# =========================

def register_mysql_namespace(api) -> None:
    """注册 /mysql 命名空间"""  # 说明
    if MYSQL_CONFIG is None:
        print("[mysql] disabled: missing MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DATABASE")
        return

    ns_mysql = Namespace("mysql", description="Query MySQL table (dynamic table + full columns)")  # 命名空间

    # Swagger：请求/响应模型
    mysql_query_req = ns_mysql.model("MySQLQueryRequest", {
        "table": fields.String(  # 可选表名
            required=False,
            default="trna_records",  # ✅ Swagger 层明确默认值
            description="Table name to query (default: trna_records). Only letters/numbers/underscore.",
            example="trna_records",
        ),
        "filters": fields.Raw(  # 过滤字典
            description="Filter dict: {column: value or [values,...]}. Only existing columns are used.",
            required=False,
            example={"dbname": "acidHosp1", "isotype": "Leu"}
        ),
        "limit": fields.Integer(  # 返回上限
            default=20,  # 默认 20
            description="Max rows to return (1..1000)",
            example=20,
        ),
    })  # 结束请求模型

    mysql_query_res = ns_mysql.model("MySQLQueryResponse", {
        "table": fields.String(description="Queried table name"),  # 返回表名
        "count": fields.Integer(description="Row count"),  # 行数
        "columns": fields.List(fields.String, description="All column names of the table"),  # 全部列名
        "rows": fields.List(fields.Raw, description="Rows as JSON (SELECT *)"),  # 全量数据
        "ignored_filters": fields.List(fields.String, description="Filters ignored due to unknown columns"),  # 被忽略过滤键
    })  # 结束响应模型

    # 新增物种信息模型
    species_model = ns_mysql.model("Species", {
        "dbname": fields.String(description="Species dbname (abbreviation)", required=True),
        "name": fields.String(description="Full species name", required=True),
        "domain": fields.Integer(description="Domain of the species (2157: Archaea, 2: Bacteria, 2759: Eukarya)", required=True),
    })

    species_response_model = ns_mysql.model("SpeciesResponse", {
        "species": fields.List(fields.Nested(species_model), description="List of species information")
    })

    # 新增：tRNA过滤器响应模型
    trna_filters_response_model = ns_mysql.model("TrnaFiltersResponse", {
        "isotypes": fields.List(fields.String, description="Available isotype options for the species"),
        "anticodons": fields.List(fields.String, description="Available anticodon options for the species"),
    })

    @ns_mysql.route("/query")  # 注册路由
    class MySQLQuery(Resource):  # 资源类
        @ns_mysql.expect(mysql_query_req, validate=True)  # 校验入参
        @ns_mysql.response(200, "Success", mysql_query_res)  # 成功返回
        @ns_mysql.response(400, "Bad Request")  # 参数错误
        @ns_mysql.response(404, "Not Found")  # 表不存在
        @ns_mysql.response(500, "Internal Server Error")  # 服务器错误
        def post(self):  # POST 处理
            """Query specified table (default trna_records), return all columns."""  # 接口说明
            data = request.get_json(force=True) or {}  # 解析 JSON 体
            raw_table = data.get("table") or "trna_records"  # ✅ 代码层默认表名
            try:
                table = _sanitize_identifier(raw_table)  # 校验表名安全
            except ValueError as e:
                ns_mysql.abort(400, f"Invalid table name: {e}")  # 非法表名

            filters = data.get("filters", {})  # 读取过滤条件
            if not isinstance(filters, dict):  # filters 必须为对象
                ns_mysql.abort(400, "filters must be a JSON object.")  # 抛 400

            # 解析 limit，设置安全边界
            try:
                limit = int(data.get("limit", 20))  # 取 limit
            except Exception:
                limit = 20  # 非法回退默认
            if limit < 1:
                limit = 1  # 下限保护
            if limit > 1000:
                limit = 1000  # 上限保护

            # 建立连接
            try:
                conn = pymysql.connect(**MYSQL_CONFIG)  # type: ignore # 连接数据库
            except Exception as e:
                ns_mysql.abort(500, f"DB connection failed: {e}")  # 连接失败

            schema = MYSQL_CONFIG.get("database")  # 获取当前库名

            try:
                # 校验表是否存在
                if not _table_exists(conn, schema, table):  # 不存在
                    ns_mysql.abort(404, f"Table not found: {schema}.{table}")  # 抛 404

                # 获取列清单（已用别名 col，避免 'column_name' 大小写问题）
                all_cols = _get_table_columns(conn, schema, table)  # 列名列表
                if not all_cols:  # 没有列（异常）
                    ns_mysql.abort(500, f"Table has no columns: {schema}.{table}")  # 抛 500

                # 构建 WHERE 子句
                where_sql, params, ignored = _build_where_clause(filters, all_cols)  # 条件+参数+忽略键

                # 组装最终 SQL（注意：表名/库名用反引号+白名单校验，值用占位符）
                sql = f"SELECT * FROM `{schema}`.`{table}`"  # 全列查询
                if where_sql:  # 若有条件
                    sql += where_sql  # 拼接 WHERE
                sql += f" LIMIT {limit}"  # 限制返回数量

                # 执行查询
                with conn.cursor() as cur:  # 打开游标
                    cur.execute(sql, params)  # 执行 SQL
                    rows = cur.fetchall()  # 取所有行

            except Exception as e:  # 捕获异常
                ns_mysql.abort(500, f"MySQL query failed: {e}")  # 返回 500
            finally:
                try:
                    conn.close()  # 关闭连接
                except Exception:
                    pass  # 忽略关闭异常

            # 返回统一结构
            return {
                "table": table,  # 实际查询表
                "count": len(rows),  # 行数
                "columns": all_cols,  # 该表全部列名
                "rows": rows,  # 全量记录
                "ignored_filters": ignored,  # 被忽略的过滤键
            }, 200  # HTTP 200

    # =========================
    # 新增：查询物种信息 /mysql/get_species
    # =========================

    @ns_mysql.route("/get_species")  # 新子路由
    class MySQLGetSpecies(Resource):  # 查询物种信息
        @ns_mysql.response(200, "Success", species_response_model)  # 使用定义好的响应模型
        @ns_mysql.response(500, "Internal Server Error")  # 服务器错误
        def get(self):
            """Query species information from genomes table"""  # 接口说明
            try:
                conn = pymysql.connect(**MYSQL_CONFIG)  # type: ignore # 连接数据库
            except Exception as e:
                ns_mysql.abort(500, f"DB connection failed: {e}")  # 连接失败

            try:
                # 查询物种信息（dbname, name, domain）
                sql = "SELECT dbname, name, domain FROM genomes"
                with conn.cursor() as cur:
                    cur.execute(sql)
                    species_data = cur.fetchall()
            except Exception as e:
                ns_mysql.abort(500, f"Query failed: {e}")  # 查询失败
            finally:
                try:
                    conn.close()  # 关闭连接
                except Exception:
                    pass  # 忽略关闭异常

            return {"species": species_data}, 200  # 返回物种数据

    # =========================
    # 新增：查询tRNA过滤器选项 /mysql/get_trna_filters
    # =========================

    @ns_mysql.route("/get_trna_filters")  # 新子路由
    class MySQLGetTrnaFilters(Resource):  # 查询tRNA过滤器选项
        @ns_mysql.doc(
            params={
                "species": {
                    "description": "Species dbname (optional; omit to return empty lists).",
                    "example": "acidHosp1",
                }
            }
        )
        @ns_mysql.response(200, "Success", trna_filters_response_model)  # 使用定义好的响应模型
        @ns_mysql.response(500, "Internal Server Error")  # 服务器错误
        def get(self):
            """Query isotype and anticodon options for a specific species"""  # 接口说明
            species = request.args.get("species")  # 获取物种参数
            
            if not species:
                return {"isotypes": [], "anticodons": []}, 200  # 无物种参数返回空

            try:
                conn = pymysql.connect(**MYSQL_CONFIG)  # type: ignore # 连接数据库
            except Exception as e:
                ns_mysql.abort(500, f"DB connection failed: {e}")  # 连接失败

            try:
                # 查询该物种的所有isotype选项（去重）
                sql_isotype = (
                    "SELECT DISTINCT isotype FROM trna_records "
                    "WHERE dbname = %s "
                    "AND isotype IS NOT NULL AND isotype != '' "
                    "AND anticodon IS NOT NULL AND anticodon != '' "
                    "ORDER BY isotype"
                )
                # 查询该物种的所有anticodon选项（去重）
                sql_anticodon = "SELECT DISTINCT anticodon FROM trna_records WHERE dbname = %s AND anticodon IS NOT NULL AND anticodon != '' ORDER BY anticodon"
                
                with conn.cursor() as cur:
                    cur.execute(sql_isotype, (species,))
                    isotype_data = cur.fetchall()
                    isotypes = [row["isotype"] for row in isotype_data]
                    
                    cur.execute(sql_anticodon, (species,))
                    anticodon_data = cur.fetchall()
                    anticodons = [row["anticodon"] for row in anticodon_data]
                    
            except Exception as e:
                ns_mysql.abort(500, f"Query failed: {e}")  # 查询失败
            finally:
                try:
                    conn.close()  # 关闭连接
                except Exception:
                    pass  # 忽略关闭异常

            return {
                "isotypes": isotypes,  # 返回isotype选项列表
                "anticodons": anticodons  # 返回anticodon选项列表
            }, 200  # HTTP 200

    api.add_namespace(ns_mysql)  # 挂载命名空间
