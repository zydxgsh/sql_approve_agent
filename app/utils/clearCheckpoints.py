import psycopg
from app.config import CHECKPOINT_DB_PATH


def clear_thread_checkpoints(thread_id: str) -> None:
    """删除指定thread_id下的所有checkpoint，用于需求变更时重置会话轨迹"""
    with psycopg.connect(CHECKPOINT_DB_PATH, autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM checkpoint_writes WHERE thread_id=%s;", (thread_id,)
            )
            cursor.execute("DELETE FROM checkpoints WHERE thread_id=%s;", (thread_id,))
