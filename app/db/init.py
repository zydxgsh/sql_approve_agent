# 一键初始化PG：表空间(可选) → 数据库 → schema demo_business → 数据表 + 种子数据
import psycopg
from psycopg import OperationalError
from app.config import PG_DBNAME, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

# ===================== 配置区（在这里改）=====================
USE_TABLESPACE = False  # 是否启用自定义表空间，False=使用PG默认存储
TABLESPACE_NAME = "demo_tbs"
TABLESPACE_PATH = "E:/software/workPath/postgreSQL/demo_tbs"  # Windows路径用 /
# ============================================================

# 建表SQL，schema: demo_business
SEED_SQL = """
CREATE SCHEMA IF NOT EXISTS demo_business;

CREATE TABLE IF NOT EXISTS demo_business.users(
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    points INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS demo_business.orders(
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_id INTEGER REFERENCES demo_business.users(id),
    amount REAL NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS demo_business.articles(
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    author_id INTEGER REFERENCES demo_business.users(id),
    title TEXT NOT NULL,
    content TEXT,
    is_deleted INTEGER DEFAULT 0,
    published_at TIMESTAMP
)
"""


def create_tablespace_if_not_exists():
    """创建表空间（仅 USE_TABLESPACE=True 时执行）"""
    if not USE_TABLESPACE:
        return
    conn = None
    cur = None
    try:
        # 建表空间必须连 postgres 系统库
        conn = psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname="postgres",
            user=PG_USER,
            password=PG_PASSWORD,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pg_tablespace WHERE spcname = %s", (TABLESPACE_NAME,)
        )
        if not cur.fetchone():
            cur.execute(
                "CREATE TABLESPACE {} LOCATION %s".format(TABLESPACE_NAME),
                (TABLESPACE_PATH,),
            )
            print(f"✅ 表空间 {TABLESPACE_NAME} 已创建，路径：{TABLESPACE_PATH}")
        else:
            print(f"ℹ️ 表空间 {TABLESPACE_NAME} 已存在")
    except Exception as e:
        print(f"❌ 表空间创建失败，检查文件夹权限！err: {e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def create_database_if_not_exists():
    """创建业务数据库，可选指定表空间"""
    conn = None
    cur = None
    try:
        conn = psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname="postgres",
            user=PG_USER,
            password=PG_PASSWORD,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (PG_DBNAME,))
        if not cur.fetchone():
            if USE_TABLESPACE:
                sql = f"CREATE DATABASE {PG_DBNAME} TABLESPACE {TABLESPACE_NAME}"
            else:
                sql = f"CREATE DATABASE {PG_DBNAME}"
            cur.execute(sql)
            print(f"✅ 数据库 {PG_DBNAME} 创建成功")
        else:
            print(f"ℹ️ 数据库 {PG_DBNAME} 已存在")
    except Exception as e:
        print(f"❌ 创建数据库失败：{e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def init_schema_and_seed_data():
    """进入业务库，建schema、表、种子数据"""
    conn = None
    cursor = None
    try:
        conn = psycopg.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DBNAME,
            user=PG_USER,
            password=PG_PASSWORD,
            options="-c statement_timeout=5000",
        )
        cursor = conn.cursor()
        cursor.execute(SEED_SQL)
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM demo_business.users")
        row = cursor.fetchone()
        cnt = row[0] if row is not None else 0

        if cnt == 0:
            cursor.executemany(
                "INSERT INTO demo_business.users (name,email,status,points) VALUES (%s,%s,%s,%s);",
                [
                    (
                        f"user{i}",
                        f"email{i}@email.com",
                        "inactive" if i % 5 == 0 else "active",
                        i * 10,
                    )
                    for i in range(1, 21)
                ],
            )
            cursor.executemany(
                "INSERT INTO demo_business.orders (user_id,amount,status,created_at) VALUES (%s,%s,%s,%s);",
                [
                    (
                        i % 20 + 1,
                        100 + i,
                        "pending" if i % 6 == 0 else "paid",
                        "2026-11-02" if i % 6 == 0 else "2026-11-11",
                    )
                    for i in range(1, 51)
                ],
            )
            cursor.executemany(
                "INSERT INTO demo_business.articles (author_id,title,content,is_deleted) VALUES (%s,%s,%s,%s);",
                [(i % 20 + 1, f"文章{i}", f"内容{i}", 0) for i in range(1, 51)],
            )
            conn.commit()
            print("✅ schema demo_business + 三张表 + 测试数据初始化成功！")
        else:
            print("ℹ️ demo_business.users 已有数据，跳过插入种子数据")

    except OperationalError as e:
        if conn:
            conn.rollback()
        print(f"❌ PG业务库连接报错：{e}")
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 建表/插入数据报错：{e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def init_sql_data() -> None:
    print("======开始初始化数据库=======")
    create_tablespace_if_not_exists()
    create_database_if_not_exists()
    init_schema_and_seed_data()
    print("======初始化数据库完成=======")
