# 图的共享状态
from typing import Optional, TypedDict, Any


class AgentState(TypedDict):
    input_txt: str  # 用户输入
    parsed_input: Optional[dict[str, Any]]  # 输入结构化
    generated_sql: Optional[str]  # 输出的sql
    sql_analyze: Optional[dict[str, Any]]  # sql语句分析
    risk_level: Optional[str]  # 风险等级"red" | "yellow" | "green"
    risk_reason: Optional[str]  # 风险原因
    reject_reason: Optional[str]  # agent生成驳回原因，回写给 generate_sql继续生成
    approval_decision: Optional[
        str
    ]  # 批准结果，"approve" | "reject（修改sql语句，不需要一切重来）|requireModify（修改需求一切重来）"
    final_result: Optional[dict[str, Any]]  # 整个流程最新结果
