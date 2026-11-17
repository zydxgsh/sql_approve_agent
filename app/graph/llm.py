from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from app.config import API_KEY, BASE_URL, MODEL_NAME

if not all([MODEL_NAME, API_KEY, BASE_URL]):
    raise ValueError("模型环境变量未配置完整")

_client = None  # _client被实例化后替代None


# 单例化_client（约定式私有变量）
def get_llm():
    global _client
    if _client is None:
        _client = ChatOpenAI(
            model=MODEL_NAME, api_key=API_KEY, base_url=BASE_URL, temperature=0
        )
    return _client


# 单轮对话
def get_single_chat(userTxt: str, sysTxt: str) -> str:
    chat: AIMessage = get_llm().invoke(input=[("system", sysTxt), ("user", userTxt)])
    return chat.content  # content可能为多模态，但这里不涉及到图片什么的
