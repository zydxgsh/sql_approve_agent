from typing import Any
import psycopg
import sqlglot
from app.config import PG_DBNAME, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER


def execute_sql_fun(sql: str | None) -> dict[str, Any]:
    if not sql or not sql.strip():
        return {"rows": 0, "error": "sql语句为空，无法执行", "code": 0}
    sql = sql.strip()
    conn: psycopg.Connection | None = None
    try:
        conn = psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DBNAME,
            user=PG_USER,
            password=PG_PASSWORD,
            options="-c statement_timeout=5000",
        )
        expression = sqlglot.parse(sql, read="postgres")
        if not expression:
            return {"rows": 0, "error": "sql语句为空，无法执行", "code": 0}
        rows = 0
        cursor = conn.cursor()
        for expr in expression:
            if not expr:
                continue
            stmt = expr.sql(dialect="postgres")
            cursor.execute(stmt)
            rows += cursor.rowcount or 0
        conn.commit()
        return {"row_effects": rows, "error": None, "code": 1}
    except psycopg.Error as e:
        if conn:
            conn.rollback()
        print(f"执行数据库出错：{e}")
        return {"row_effects": 0, "error": f"执行数据库出错：{e}", "code": 0}
    finally:
        if conn:
            conn.close()
