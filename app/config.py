# 公共环境变量的集中读取
import os
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus

load_dotenv()

origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

# 首尾去除空格后存在的话就放入数组中
ALLOWED_ORIGINS = [origin.strip() for origin in origins if origin.strip()]

# 大模型
API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL_NAME = os.getenv("OPENAI_MODEL")

# PostgreSQL
PG_HOST = os.getenv("PG_HOST")
PG_PORT = os.getenv("PG_PORT")
PG_DBNAME = os.getenv("PG_DBNAME")
PG_USER = os.getenv("PG_USER")
PG_PASSWORD = os.getenv("PG_PASSWORD")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)  # 不存在时就创建

DEMO_DB_PATH = os.getenv("DEMO_DB_PATH", str(DATA_DIR / "demo.db"))  # 业务库

CHECKPOINT_DB_PATH = f"postgresql://{PG_USER}:{quote_plus(PG_PASSWORD)}@{PG_HOST}:{PG_PORT}/{PG_DBNAME}"  # Checkpointer 库：只存图状态，独立于业务数据

PARSE_SYSTEM = """
你是一个得力的数据库变更助手，当前可用的数据表有：
- demo_business.users(id, name, email, status, points, created_at, last_login_at)
- demo_business.orders(id, user_id, amount, status, created_at)
- demo_business.articles(id, author_id, title, content, is_deleted, published_at)
根据用户的输入，将其解析成JSON数据，不要有任何其他多余的输出，其数据结构严格按下面的格式形式输出：
{"intent": "add_column|update|select|delete|other", "table": "表名", "detail": "简述"}。
如果无法判别和解析用户输入，则严格返回下面的数据，不要有其他的变更：
{"intent": "", "table": "", "detail": "","detail":"fail"}。
所有的输出内容均严格要求：
只输出纯JSON，**禁止任何markdown代码块、```、解释文字、前言后语、注释**。
不要包裹 ```json 和 ```，不要增加任何说明，输出必须是可直接json.loads的字符串。
"""

SQL_SYSTEM = """
你是一个得力的PostgreSQL专家，负责生成sql语句。
当前可用的表有：
- demo_business.users(id, name, email, status, points, created_at, last_login_at)
- demo_business.orders(id, user_id, amount, status, created_at)
- demo_business.articles(id, author_id, title, content, is_deleted, published_at)

根据用户要求和结构化后的描述，只生成符合PostgreSQL语法的sql语句，不要有多余的操作和输出，禁止任何markdown。
如果有多步操作，用分号分隔。表名必须带 demo_business 前缀。
"""
