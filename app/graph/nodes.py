# langgraph各个节点函数
from app.graph.state import AgentState
from app.graph.llm import get_single_chat
from app.config import PARSE_SYSTEM, SQL_SYSTEM
from app.utils.riskRules import PostgresRiskCheckerAST
from app.db.excuteSql import execute_sql_fun
from app.utils.clearCheckpoints import clear_thread_checkpoints
from langgraph.types import interrupt, Command
from langchain_core.runnables import RunnableConfig
from app.utils.sqlChangeAnalyzer import SqlChangeAnalyzer
from typing import Any
import re
import json


# 结构化用户输入，传入给llm生成sql
def parse_txt(state: AgentState) -> dict[str, Any]:
    parse_chat = get_single_chat(userTxt=state["input_txt"], sysTxt=PARSE_SYSTEM)
    parse_chat = re.sub(r"^```(?:json)?\s*|\s*```$", "", parse_chat, flags=re.MULTILINE)
    try:
        result = json.loads(parse_chat)
        if (result.get("detail", "") or "") == "fail":
            result = {
                "intent": "other",
                "table": "unknown",
                "detail": state["input_txt"],
            }
    except json.JSONDecodeError as e:
        print(f"解析报错:{e}")
        result = {"intent": "other", "table": "unknown", "detail": state["input_txt"]}
    return {"parsed_input": result}


# 根据结构化后的输入生成sql
def generate_sql(state: AgentState) -> dict[str, str | None | dict[str, Any]]:
    # 组成语句
    prompt = f"当前用户需求为：{state['input_txt']}\n结构化描述：{state.get('parsed_input',{})}"
    print(f'审批拒绝原因：{state.get("reject_reason","")}')
    if state.get("reject_reason") and state.get("approval_decision") == "reject":
        prompt += f"上一条被驳回的原因是{state['reject_reason']}\n请根据原因重新生成。"
    sql_chat = get_single_chat(userTxt=prompt, sysTxt=SQL_SYSTEM)
    # 防止markdown嵌入，并去掉开头结尾空字符
    sql_chat = re.sub(
        r"^```(?:sql)?\s*|\s*```$", "", sql_chat, flags=re.MULTILINE
    ).strip()
    # 此时已成功生成sql
    analyzer = SqlChangeAnalyzer()
    res: dict[str, Any] = analyzer.analyze_sql(sql=sql_chat)
    return {"generated_sql": sql_chat, "reject_reason": None, "sql_analyze": res}


# 定义当前风险
def risk_evalute(state: AgentState) -> dict[str, str]:
    sql: str = (
        state.get("generated_sql", "") or ""
    ).upper()  # 这里的默认值只是在generated_sql这个key不存在时才为''
    checker = PostgresRiskCheckerAST(
        business_tables=[
            "demo_business.users",
            "demo_business.orders",
            "demo_business.articles",
        ]
    )
    if not sql:
        level, reason = "yellow", "sql为空，无法进行规则校验"
        return {"risk_level": level, "risk_reason": reason}
    check_result = checker.check(sql)
    check_code = check_result["code"]
    check_data = check_result.get("data", {})

    # 500：解析异常，不走风险等级
    if check_code == 500:
        level, reason = "yellow", "sql语句解析出错"
    else:
        # 解析成功，统一用 overall_risk_level 判定
        overall_level = check_data.get("overall_risk_level", "low")
        risk_desc = check_data.get("risk_desc", "无风险")
        # 映射关系
        level_map = {"high": "red", "medium": "yellow", "low": "green"}
        level = level_map.get(overall_level, "yellow")
        reason = risk_desc

    return {"risk_level": level, "risk_reason": reason}


# 人工审批(interrupt)
def approval_node(state: AgentState, config: RunnableConfig) -> Command[str]:
    decision = interrupt(
        {
            "sql": state.get("generated_sql", ""),
            "risk_level": state.get("risk_level"),
            "risk_reason": state.get("risk_reason"),
            "instruction": '请回复 {"action":"approve"} 或者 {"action":"reject", "reason":"拒绝原因"}',
            "input_txt": state.get("input_txt"),
        }
    )
    decision_action = decision.get("action", "requireModify")
    if decision_action == "approve":  # 审批同意进入执行阶段
        return Command(goto="execute_node", update={"approval_decision": "approve"})
    elif decision_action == "reject":  # 拒绝则重新生成sql
        return Command(
            goto="generate_node",
            update={
                "approval_decision": "reject",
                "reject_reason": decision.get("reason", "未填写原因"),
            },
        )
    else:  # 需求修改则从头开始(要清空之前的checkpoints历史)
        clear_thread_checkpoints(config.get("configurable", {}).get("thread_id", ""))
        new_req = decision.get("new_input", "") or state["input_txt"]
        return Command(
            goto="parse_node",
            update={
                "approval_decision": "requireModify",
                "reject_reason": decision.get("reason", "未填写原因"),
                "input_txt": new_req,
                "parsed_input": None,
                "generated_sql": None,
                "risk_level": None,
                "risk_evalute": None,
                "sql_analyze": None,
            },
        )


# 执行sql(直接执行/审批同意后执行)
def execute_node(state: AgentState) -> dict[str, Any]:
    sql = state.get("generated_sql", "")
    executeResult = execute_sql_fun(sql)
    print(f"拿到的执行sql结果{executeResult}")
    return {
        "final_result": {
            "row_effects": executeResult["row_effects"],
            "error": executeResult["error"],
            "code": executeResult["code"],
        }
    }
