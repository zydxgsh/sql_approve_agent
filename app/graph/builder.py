# 组装成图
from langgraph.graph import START, END, StateGraph

# from langgraph.checkpoint.memory import MemorySaver
# from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
import signal
from app.graph.state import AgentState
from app.graph.nodes import (
    parse_txt,
    generate_sql,
    risk_evalute,
    approval_node,
    execute_node,
)
from app.config import (
    CHECKPOINT_DB_PATH,
    PG_HOST,
    PG_PORT,
    PG_DBNAME,
    PG_USER,
    PG_PASSWORD,
)


def to_which_node(state: AgentState):
    risk = state.get("risk_level", "yellow") or "yellow"
    if risk == "green":
        return "execute_node"
    return "approval_node"


# 创建图
def build_graph(checkpointer):
    builder = StateGraph(AgentState)

    builder.add_node("parse_node", parse_txt)
    builder.add_node("generate_node", generate_sql)
    builder.add_node("evalute_node", risk_evalute)
    builder.add_node("approval_node", approval_node)
    builder.add_node("execute_node", execute_node)

    # memory = MemorySaver()

    builder.add_edge(START, "parse_node")
    builder.add_edge("parse_node", "generate_node")
    builder.add_edge("generate_node", "evalute_node")
    builder.add_conditional_edges(
        "evalute_node",
        to_which_node,
        # {"execute_node": execute_node, "approval_node": approval_node},
    )
    builder.add_edge("execute_node", END)
    # graph = builder.compile(checkpointer=memory, interrupt_before=["approval_node"])#用了这个，node节点中就不能用Command
    graph = builder.compile(checkpointer=checkpointer)
    return graph


def create_graph():
    """创建postger图实例，
    postgerSaver需要连接池，contextmanager负责关闭
    由fastapi lifespan调用，生命周期和进程一致
    """
    conn = PostgresSaver.from_conn_string(CHECKPOINT_DB_PATH)
    checkpoint = conn.__enter__()  # 手动加入上下文，保持连接
    checkpoint.setup()  # 建checkpoint表，幂等
    return build_graph(checkpointer=checkpoint), conn
