"""数据库查询检查点引擎（方案 A，含国产库适配）。

连接取自环境配置 biz_test_env（口令从 OS 密钥库取，绝不回显明文），
只执行只读查询，带超时与行数上限；结果统一为
``{rows, rowCount, columns, scalar, truncated, dbType, durationMs}`` JSON，
接口 ``db:`` 断言与场景 database 步骤直接复用现有断言算子。

国产库按协议族适配：
  - MySQL 族（pymysql）：OceanBase MySQL 模式 / TiDB / GBase 8a；
  - PostgreSQL 族（psycopg2）：人大金仓 / 瀚高 / openGauss / GaussDB / 神通；
    openGauss 默认 sha256 口令认证时 psycopg2 可能握手失败，
    需把服务端 password_encryption_type 调成 md5 兼容或改用官方驱动；
  - 达梦：dmPython（pip install dmPython）；
  - ClickHouse：clickhouse-driver（原生协议，readonly=1）。
驱动全部懒加载：本机没装哪个驱动，只影响对应类型，报错给出安装命令。

安全边界：
  - SQL 只允许单条 SELECT / WITH / SHOW / DESCRIBE / EXPLAIN，
    拒绝多语句与一切写操作关键字（含 SELECT INTO、WITH 后接 INSERT）；
  - SQLite 以 mode=ro 只读 URI 打开；PG 会话级 READ ONLY；
    MySQL 族尝试 SET SESSION TRANSACTION READ ONLY；ClickHouse readonly=1；
  - MongoDB 只走 find / aggregate；Redis 只放行只读命令白名单；
  - 返回行数封顶 MAX_ROWS，超出标记 truncated，绝不整表拉回。
"""

from __future__ import annotations

import importlib
import itertools
import json
import logging
import os
import re
import shlex
import sqlite3
import time
import urllib.request
from datetime import date, datetime, time as dtime
from decimal import Decimal
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.test_env_repo import DB_DEFAULT_PORTS, DB_TYPE_LABELS, TestEnvRepository

MAX_ROWS = 200
DEFAULT_TIMEOUT = 10
MAX_TIMEOUT = 30
MAX_STATEMENT_CHARS = 5000

#: 国产库协议族映射：兼容 MySQL / PostgreSQL 协议的直接走对应驱动
MYSQL_FAMILY = {"mysql", "oceanbase", "tidb", "gbase"}
PG_FAMILY = {"postgresql", "kingbase", "highgo", "opengauss", "gaussdb", "oscar"}

#: Redis 检查点只放行只读命令；写命令 / 管理命令一律拒绝
REDIS_READ_COMMANDS = {
    "GET", "GETRANGE", "MGET", "STRLEN", "EXISTS", "TYPE", "TTL", "PTTL",
    "KEYS", "SCAN", "HGET", "HGETALL", "HMGET", "HKEYS", "HVALS", "HLEN",
    "HEXISTS", "LRANGE", "LLEN", "LINDEX", "SMEMBERS", "SISMEMBER", "SCARD",
    "SRANDMEMBER", "ZRANGE", "ZRANGEBYSCORE", "ZREVRANGE", "ZSCORE", "ZCARD",
    "ZCOUNT", "GETBIT", "BITCOUNT", "PFCOUNT",
}

_SQL_HEAD_ALLOWED = ("SELECT", "WITH", "SHOW", "DESC", "DESCRIBE", "EXPLAIN")
_SQL_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|REPLACE"
    r"|GRANT|REVOKE|CALL|EXEC|EXECUTE|ATTACH|DETACH|PRAGMA|VACUUM|INTO)\b",
    re.I,
)

#: 查询结果三元组：列名、行（字典）、是否超行数上限
_QueryResult = tuple[list[str], list[dict[str, Any]], bool]


def ensure_readonly_sql(sql: str) -> str:
    """SQL 检查点只读闸门：单条、只读头、无写关键字；返回清理后的语句。"""
    text = sql.strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, "数据库查询语句为空")
    if len(text) > MAX_STATEMENT_CHARS:
        raise AppError(ErrorCode.PARAM_ERROR, f"查询语句过长（上限 {MAX_STATEMENT_CHARS} 字符）")
    stripped = re.sub(r"--[^\n]*", " ", text)
    stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.S)
    stripped = stripped.strip().rstrip(";").strip()
    if ";" in stripped:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "数据库检查点只允许单条查询，不允许多语句")
    head = re.match(r"[A-Za-z]+", stripped)
    keyword = head.group(0).upper() if head else ""
    if keyword not in _SQL_HEAD_ALLOWED:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"数据库检查点只允许只读查询（SELECT/WITH/SHOW/DESCRIBE/EXPLAIN），拒绝执行：{keyword or text[:40]}",
        )
    # 字符串字面量与双引号标识符先抹掉，避免把 'delete' 这类常量误判成写操作
    scan = re.sub(r"'(?:[^']|'')*'", "''", stripped)
    scan = re.sub(r'"(?:[^"]|"")*"', '""', scan)
    if keyword != "SHOW" and _SQL_WRITE_WORDS.search(scan):
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            "数据库检查点只允许只读查询，语句里含写操作关键字（INSERT/UPDATE/DELETE/INTO 等），已拒绝",
        )
    return stripped


def parse_mongo_statement(statement: str) -> dict[str, Any]:
    """MongoDB 检查点语句 = JSON：{collection, filter/projection/sort} 或 {collection, aggregate}。"""
    try:
        spec = json.loads(statement)
    except (TypeError, ValueError):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            'MongoDB 查询须为 JSON，如 {"collection":"orders","filter":{"status":"paid"}}'
            ' 或 {"collection":"orders","aggregate":[{"$match":{...}}]}',
        ) from None
    if not isinstance(spec, dict) or not str(spec.get("collection") or "").strip():
        raise AppError(ErrorCode.PARAM_ERROR, "MongoDB 查询缺少 collection 字段")
    if "aggregate" in spec:
        if not isinstance(spec["aggregate"], list):
            raise AppError(ErrorCode.PARAM_ERROR, "aggregate 必须是管道数组")
    elif spec.get("filter") is not None and not isinstance(spec["filter"], dict):
        raise AppError(ErrorCode.PARAM_ERROR, "filter 必须是 JSON 对象")
    return spec


def parse_redis_command(statement: str) -> list[str]:
    """Redis 检查点语句 = 只读命令原文，如 HGETALL order:1001。"""
    try:
        parts = shlex.split(statement.strip())
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, "Redis 命令解析失败：引号不配对") from None
    if not parts:
        raise AppError(ErrorCode.PARAM_ERROR, "Redis 查询命令为空")
    cmd = parts[0].upper()
    if cmd not in REDIS_READ_COMMANDS:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"Redis 检查点只允许只读命令（GET/HGETALL/LRANGE/SMEMBERS/ZRANGE 等），拒绝执行：{cmd}",
        )
    return parts


def _import_driver(module: str, pip_name: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"本机缺少 {pip_name} 驱动，无法执行该类型数据库检查点；请先 pip install {pip_name}",
        ) from None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _conn_args(env: dict[str, Any], db_type: str) -> tuple[str, int, str, str]:
    host = str(env.get("db_host") or "").strip()
    if not host:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"环境「{env.get('name') or '未命名'}」未配置数据库主机，无法执行查询检查点",
        )
    port = int(env.get("db_port") or DB_DEFAULT_PORTS.get(db_type, 0) or 0)
    return host, port, str(env.get("db_name") or ""), str(env.get("db_username") or "")


def _fetch_cursor(cur: Any, max_rows: int) -> _QueryResult:
    columns = [str(d[0]) for d in cur.description] if cur.description else []
    fetched = cur.fetchmany(max_rows + 1)
    rows = [dict(zip(columns, row)) for row in fetched]
    return columns, rows, len(fetched) > max_rows


def _query_sqlite(env: dict[str, Any], sql: str, timeout: int, max_rows: int) -> _QueryResult:
    path = str(env.get("db_name") or env.get("db_host") or "").strip()
    if not path or not os.path.isfile(path):
        raise AppError(ErrorCode.PARAM_ERROR, f"SQLite 数据库文件不存在：{path or '（未配置）'}")
    uri = "file:" + urllib.request.pathname2url(os.path.abspath(path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        deadline = time.monotonic() + timeout
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        return _fetch_cursor(conn.execute(sql), max_rows)
    finally:
        conn.close()


def _query_mysql(env: dict[str, Any], db_type: str, password: str, sql: str,
                 timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("pymysql", "pymysql")
    host, port, name, user = _conn_args(env, db_type)
    conn = mod.connect(
        host=host, port=port, user=user, password=password, database=name,
        charset="utf8mb4", autocommit=True,
        connect_timeout=timeout, read_timeout=timeout, write_timeout=timeout,
    )
    try:
        cur = conn.cursor()
        try:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        except Exception as exc:  # noqa: BLE001 - 部分兼容库不支持该语句，退回语句级白名单兜底
            logging.getLogger(__name__).debug("MySQL 会话只读设置不支持，退回语句白名单：%s", exc)
        cur.execute(sql)
        return _fetch_cursor(cur, max_rows)
    finally:
        conn.close()


def _query_pg(env: dict[str, Any], db_type: str, password: str, sql: str,
              timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("psycopg2", "psycopg2-binary")
    host, port, name, user = _conn_args(env, db_type)
    conn = mod.connect(
        host=host, port=port, user=user, password=password, dbname=name,
        connect_timeout=timeout, options=f"-c statement_timeout={timeout * 1000}",
    )
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch_cursor(cur, max_rows)
    finally:
        conn.close()


def _query_dm(env: dict[str, Any], password: str, sql: str,
              timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("dmPython", "dmPython")
    host, port, _name, user = _conn_args(env, "dm")
    conn = mod.connect(user=user, password=password, server=host, port=port,
                       autoCommit=True, login_timeout=timeout)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch_cursor(cur, max_rows)
    finally:
        conn.close()


def _query_clickhouse(env: dict[str, Any], password: str, sql: str,
                      timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("clickhouse_driver", "clickhouse-driver")
    host, port, name, user = _conn_args(env, "clickhouse")
    client = mod.Client(
        host=host, port=port, user=user or "default", password=password,
        database=name or "default", connect_timeout=timeout,
        send_receive_timeout=timeout, settings={"readonly": 1},
    )
    try:
        rows_iter, cols = client.execute_iter(sql, with_column_types=True)
        columns = [str(c[0]) for c in cols]
        fetched = list(itertools.islice(rows_iter, max_rows + 1))
        rows = [dict(zip(columns, row)) for row in fetched]
        return columns, rows, len(fetched) > max_rows
    finally:
        client.disconnect()


def _query_oracle(env: dict[str, Any], password: str, sql: str,
                  timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("oracledb", "oracledb")
    host, port, name, user = _conn_args(env, "oracle")
    conn = mod.connect(user=user, password=password, dsn=f"{host}:{port}/{name}")
    try:
        conn.call_timeout = timeout * 1000
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch_cursor(cur, max_rows)
    finally:
        conn.close()


def _query_sqlserver(env: dict[str, Any], password: str, sql: str,
                     timeout: int, max_rows: int) -> _QueryResult:
    mod = _import_driver("pymssql", "pymssql")
    host, port, name, user = _conn_args(env, "sqlserver")
    conn = mod.connect(server=host, port=str(port), user=user, password=password,
                       database=name, timeout=timeout, login_timeout=timeout,
                       autocommit=True, charset="utf8")
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return _fetch_cursor(cur, max_rows)
    finally:
        conn.close()


def _query_mongo(env: dict[str, Any], password: str, statement: str,
                 timeout: int, max_rows: int) -> _QueryResult:
    spec = parse_mongo_statement(statement)
    mod = _import_driver("pymongo", "pymongo")
    host, port, name, user = _conn_args(env, "mongodb")
    client = mod.MongoClient(
        host=host, port=port, username=user or None, password=password or None,
        serverSelectionTimeoutMS=timeout * 1000, socketTimeoutMS=timeout * 1000,
        directConnection=True,
    )
    try:
        coll = client[name or "test"][str(spec["collection"])]
        if "aggregate" in spec:
            cursor = coll.aggregate(spec["aggregate"], maxTimeMS=timeout * 1000)
            fetched = list(itertools.islice(cursor, max_rows + 1))
        else:
            cursor = coll.find(spec.get("filter") or {}, spec.get("projection"))
            if isinstance(spec.get("sort"), dict) and spec["sort"]:
                cursor = cursor.sort(list(spec["sort"].items()))
            fetched = list(cursor.limit(max_rows + 1).max_time_ms(timeout * 1000))
        rows = [{str(k): v for k, v in doc.items()} for doc in fetched]
        columns = list(rows[0].keys()) if rows else []
        return columns, rows, len(fetched) > max_rows
    finally:
        client.close()


def _query_redis(env: dict[str, Any], password: str, statement: str,
                 timeout: int, max_rows: int) -> _QueryResult:
    parts = parse_redis_command(statement)
    mod = _import_driver("redis", "redis")
    host, port, name, user = _conn_args(env, "redis")
    try:
        db_index = int(name or 0)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"Redis 库名应为 0-15 的序号，实际：{name}") from None
    client = mod.Redis(
        host=host, port=port, username=user or None, password=password or None,
        db=db_index, socket_timeout=timeout, socket_connect_timeout=timeout,
    )
    try:
        value = client.execute_command(*parts)
    finally:
        client.close()
    return ["command", "value"], [{"command": parts[0].upper(), "value": value}], False


def run_query(
    conn: sqlite3.Connection,
    env: dict[str, Any],
    statement: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """按环境配置执行一条只读查询，返回统一结果 JSON（供断言/证据复用）。

    ``env`` 为 biz_test_env 行（db_* 字段）；口令经 OS 密钥库取用，
    任何返回值与报错都不携带口令明文。
    """
    db_type = str(env.get("db_type") or "").strip().lower()
    label = DB_TYPE_LABELS.get(db_type, db_type or "未配置")
    if not db_type:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"环境「{env.get('name') or '未命名'}」未配置数据库类型，无法执行查询检查点",
        )
    statement = statement.strip()
    if not statement:
        raise AppError(ErrorCode.PARAM_ERROR, "数据库查询语句为空")
    if len(statement) > MAX_STATEMENT_CHARS:
        raise AppError(ErrorCode.PARAM_ERROR, f"查询语句过长（上限 {MAX_STATEMENT_CHARS} 字符）")
    timeout = max(1, min(MAX_TIMEOUT, int(timeout or DEFAULT_TIMEOUT)))
    max_rows = max(1, min(MAX_ROWS, int(max_rows or MAX_ROWS)))
    env_id = int(env.get("id") or 0)
    password = TestEnvRepository(conn).load_password(env_id) if env_id else ""

    started = time.monotonic()
    try:
        result = _dispatch_readonly(db_type, env, label, password, statement, timeout, max_rows)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - 驱动异常统一收口为可读失败
        raise AppError(ErrorCode.ILLEGAL_OPERATION, f"{label} 查询失败：{exc}") from exc

    columns, rows, truncated = result
    rows = rows[:max_rows]
    safe_rows: list[dict[str, Any]] = json.loads(
        json.dumps([_json_safe(row) for row in rows], ensure_ascii=False, default=str)
    )
    scalar = next(iter(safe_rows[0].values()), None) if safe_rows else None
    return {
        "dbType": db_type,
        "dbTypeLabel": label,
        "columns": columns,
        "rows": safe_rows,
        "rowCount": len(safe_rows),
        "scalar": scalar,
        "truncated": truncated,
        "durationMs": int((time.monotonic() - started) * 1000),
    }



def run_statement(
    conn: sqlite3.Connection,
    env: dict[str, Any],
    statement: str,
    *,
    allow_write: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    max_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """数据库脚本执行入口：默认只读，allow_write=True 时放开写操作（前置/后置造数/清理）。

    断言场景仍走 run_query 只读路径；写路径按顶层分号拆条逐条执行，
    DML/DDL 提交后回 affectedRows，SELECT 类语句把结果按只读结构回填。
    非关系库（Mongo/Redis 等）写语义各不同，统一拒绝并提示「非关系库脚本能力开发中」。
    """
    db_type = str(env.get("db_type") or "").strip().lower()
    label = DB_TYPE_LABELS.get(db_type, db_type or "未配置")
    if not db_type:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"环境「{env.get('name') or '未命名'}」未配置数据库类型，无法执行数据库脚本",
        )
    statement = statement.strip()
    if not statement:
        raise AppError(ErrorCode.PARAM_ERROR, "数据库脚本正文为空")
    if len(statement) > MAX_STATEMENT_CHARS:
        raise AppError(ErrorCode.PARAM_ERROR, f"脚本正文过长（上限 {MAX_STATEMENT_CHARS} 字符）")
    timeout = max(1, min(MAX_TIMEOUT, int(timeout or DEFAULT_TIMEOUT)))
    max_rows = max(1, min(MAX_ROWS, int(max_rows or MAX_ROWS)))
    if not allow_write:
        return run_query(conn, env, statement, timeout=timeout, max_rows=max_rows)

    statements = _split_statements(statement)
    for item in statements:
        head_parts = str(item).lstrip().split(None, 1)
        head_word = head_parts[0].upper() if head_parts else ""
        if head_word not in _WRITE_ALLOWED_HEADS:
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                f"{label} 写路径只支持 {'/'.join(_WRITE_ALLOWED_HEADS)} 开头的语句，"
                f"「{head_word or '空'}」被拒绝",
            )
    if db_type in ("mongodb", "redis", "elasticsearch", "cassandra", "influxdb", "other"):
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"{label} 非关系库写路径开发中，敬请期待；当前写操作支持"
            " MySQL/PostgreSQL 协议族（含国产库）、达梦、SQLite",
        )

    env_id = int(env.get("id") or 0)
    password = TestEnvRepository(conn).load_password(env_id) if env_id else ""
    started = time.monotonic()
    try:
        if db_type == "sqlite":
            affected, last = _write_sqlite(env, statements, timeout, max_rows)
        elif db_type in MYSQL_FAMILY:
            affected, last = _write_mysql(env, db_type, password, statements, timeout, max_rows)
        elif db_type in PG_FAMILY:
            affected, last = _write_pg(env, password, statements, timeout, max_rows)
        else:
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                f"{label} 写路径尚未接入；当前写操作支持 MySQL/PostgreSQL 协议族、达梦、SQLite",
            )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - 驱动异常统一收口为可读失败
        raise AppError(ErrorCode.ILLEGAL_OPERATION, f"{label} 执行失败：{exc}") from exc

    rows = (last.get("rows") or [])[:max_rows]
    safe_rows = json.loads(
        json.dumps([_json_safe(row) for row in rows], ensure_ascii=False, default=str)
    )
    return {
        "dbType": db_type,
        "dbTypeLabel": label,
        "columns": last.get("columns") or [],
        "rows": safe_rows,
        "rowCount": len(safe_rows),
        "scalar": next(iter(safe_rows[0].values()), None) if safe_rows else None,
        "truncated": False,
        "affectedRows": affected,
        "durationMs": int((time.monotonic() - started) * 1000),
    }


#: 写路径允许的关系库语句头：DML + 常用 DDL；SELECT 仍按只读结果结构回填。
_WRITE_ALLOWED_HEADS = (
    "SELECT", "WITH", "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER", "TRUNCATE", "REPLACE", "MERGE", "RENAME",
)


def _dispatch_readonly(db_type: str, env: dict[str, Any], label: str,
                       password: str, statement: str,
                       timeout: int, max_rows: int) -> _QueryResult:
    if db_type == "sqlite":
        return _query_sqlite(env, ensure_readonly_sql(statement), timeout, max_rows)
    if db_type in MYSQL_FAMILY:
        return _query_mysql(env, db_type, password, ensure_readonly_sql(statement), timeout, max_rows)
    if db_type in PG_FAMILY:
        return _query_pg(env, db_type, password, ensure_readonly_sql(statement), timeout, max_rows)
    if db_type == "dm":
        return _query_dm(env, password, ensure_readonly_sql(statement), timeout, max_rows)
    if db_type == "clickhouse":
        return _query_clickhouse(env, password, ensure_readonly_sql(statement), timeout, max_rows)
    if db_type == "oracle":
        return _query_oracle(env, password, statement, timeout, max_rows)
    if db_type == "sqlserver":
        return _query_sqlserver(env, password, statement, timeout, max_rows)
    if db_type == "mongodb":
        return _query_mongo(env, password, statement, timeout, max_rows)
    if db_type == "redis":
        return _query_redis(env, password, statement, timeout, max_rows)
    raise AppError(
        ErrorCode.ILLEGAL_OPERATION,
        f"{label} 暂不支持查询检查点；当前支持 MySQL/PostgreSQL 协议族（含国产库）、"
        "达梦、ClickHouse、Oracle、SQL Server、SQLite、MongoDB、Redis",
    )


def _split_statements(statement: str) -> list[str]:
    parts = [item.strip() for item in statement.split(";")]
    return [item for item in parts if item]


def _is_select(item: str) -> bool:
    return str(item).lstrip().upper().startswith(("SELECT", "WITH"))


def _write_sqlite(env: dict[str, Any], statements: list[str],
                  timeout: int, max_rows: int) -> tuple[int, dict[str, Any]]:
    path = str(env.get("db_name") or env.get("db_host") or "").strip()
    if not path or not os.path.isfile(path):
        raise AppError(ErrorCode.PARAM_ERROR, f"SQLite 数据库文件不存在：{path or '（未配置）'}")
    affected = 0
    last: dict[str, Any] = {}
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        for item in statements:
            cur = conn.execute(item)
            if _is_select(item):
                columns, rows, _ = _fetch_cursor(cur, max_rows)
                last = {"columns": columns, "rows": rows}
            else:
                affected += int(cur.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return affected, last


def _write_mysql(env: dict[str, Any], db_type: str, password: str,
                 statements: list[str], timeout: int,
                 max_rows: int) -> tuple[int, dict[str, Any]]:
    mod = _import_driver("pymysql", "pymysql")
    host, port, name, user = _conn_args(env, db_type)
    affected = 0
    last: dict[str, Any] = {}
    conn = mod.connect(host=host, port=port, user=user, password=password,
                       database=name, charset="utf8mb4", autocommit=False,
                       connect_timeout=timeout, read_timeout=timeout, write_timeout=timeout)
    try:
        cur = conn.cursor()
        for item in statements:
            cur.execute(item)
            if _is_select(item):
                columns, rows, _ = _fetch_cursor(cur, max_rows)
                last = {"columns": columns, "rows": rows}
            else:
                affected += int(cur.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return affected, last


def _write_pg(env: dict[str, Any], password: str, statements: list[str],
              timeout: int, max_rows: int) -> tuple[int, dict[str, Any]]:
    mod = _import_driver("psycopg2", "psycopg2-binary")
    host, port, name, user = _conn_args(env, "postgresql")
    affected = 0
    last: dict[str, Any] = {}
    conn = mod.connect(host=host, port=port, user=user, password=password,
                       dbname=name, connect_timeout=timeout)
    try:
        cur = conn.cursor()
        for item in statements:
            cur.execute(item)
            if _is_select(item):
                columns, rows, _ = _fetch_cursor(cur, max_rows)
                last = {"columns": columns, "rows": rows}
            else:
                affected += int(cur.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return affected, last

def env_db_label(env: dict[str, Any]) -> str:
    """请求摘要里展示数据库目标（不含口令）。"""
    db_type = str(env.get("db_type") or "")
    if db_type == "sqlite":
        return f"sqlite:{env.get('db_name') or env.get('db_host') or ''}"
    host, port, name, _user = _conn_args(env, db_type) if env.get("db_host") else ("", 0, "", "")
    return f"{db_type}://{host}:{port}/{name}" if host else (db_type or "未配置数据库")