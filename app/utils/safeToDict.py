from typing import Any, Union
from pydantic import BaseModel


def safe_to_dict(
    obj: Any,
) -> Union[str, int, float, bool, None, dict[str, Any], list[Any]]:
    if isinstance(obj, BaseModel):
        return obj.model_dump(
            mode="json"
        )  # `mode="json"`：直接按 json 序列化规则导出，日期、UUID 等 Pydantic 字段自动转字符串
    elif isinstance(obj, dict):
        return {k: safe_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [safe_to_dict(i) for i in obj]
    elif hasattr(obj, "value"):
        return safe_to_dict(obj.value)
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)
