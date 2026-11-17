# 路由接口参数类型定义
# from typing import TypedDict, Any
from pydantic import BaseModel


# 初始化请求
class StartRequest(BaseModel):
    requirement: str = ""


# 恢复流程
class ResumeRequest(BaseModel):
    id: str = ""  # 会话id
    requireType: (
        str  # approve同意执行/reject拒绝执行，修改sql/requireModify拒绝执行，修改需求
    ) = ""
    reason: str = ""
    editSql: str = ""
    newRequirement: str = ""


# 重试
class RetryRequest(BaseModel):
    id: str = ""
    retryType: str = ""  # 1表示重新执行sql,2表示从头开始执行
