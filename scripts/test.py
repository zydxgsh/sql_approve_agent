import uuid
from app.graph.builder import get_complied_graph
from app.db.init import init_sql_data
from langgraph.types import Command


def main():
    # 初始化数据库
    init_sql_data()
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4().hex[:8]}"}}
    graph = get_complied_graph()
    query = "给 users 表加 test 字段，类型为TEXT"
    print(f"config={str(config)}")
    print(f"需求={query}\n")
    result = graph.invoke(
        input={"input_txt": query},
        config=config,
    )
    # 第一次 invoke：跑到 interrupt 停下
    print("图已经暂停")
    print(f'生成的sql={result.get("generated_sql","")}')
    print(f'风险={result.get("risk_level","")}，原因是{result.get("risk_reason","")}\n')

    # 恢复
    print('继续运行，传入指令Command(resume={"action": "approve"})]')
    final = graph.invoke(Command(resume={"action": "approve"}), config=config)
    print(f"  执行结果: {final.get('final_result')}\n")


if __name__ == "__main__":
    main()
