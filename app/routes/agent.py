# 创建各个接口路由(启动，恢复，状态查询)返回sse流

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sse_starlette import ServerSentEvent
from langgraph.types import Command
from langgraph.graph.state import CompiledStateGraph, RunnableConfig
from starlette.concurrency import run_in_threadpool
from typing import Any, AsyncGenerator
import uuid
import json
import time
import asyncio
from app.routes.routeState import StartRequest, ResumeRequest, RetryRequest
from app.utils.safeToDict import safe_to_dict

router = APIRouter(prefix="/agent", tags=["agent"])


# 统一sse结构(事件名+json字符串)
def _sse(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


# def _sse(event: str, data: dict[str, Any]) -> ServerSentEvent:
#     return ServerSentEvent(data=data, event=event)


# 统一图的节点流式输出（stream_mode="updates" 只输出本次更新的字段，前端不需要完整 state。）
async def _graph_stream(
    graph: CompiledStateGraph, input_or_command: Any, config: RunnableConfig
) -> AsyncGenerator[dict[str, Any], None]:
    # print("进入图的切片流", graph, input_or_command, config)
    # 队列：存放 (event, data)，交给主线程去 _sse + yield
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=10)
    stop_event = asyncio.Event()

    def sync_stream_worker():
        try:
            for chunk in graph.stream(  # 同步生成器循环(graph.astream异步的兼容性太弱)
                input_or_command, config=config, stream_mode="updates"
            ):
                print(f"切片有：{str(chunk.items())}")
                # time.sleep(3)
                for (
                    node_name,
                    node_output,
                ) in chunk.items():  # 用for循环是为了兼容并行节点，但此处应该是不存在的
                    # print(f"每个的解包:{node_name,node_output}")
                    queue.put_nowait(
                        (
                            "node_update",
                            {"node": node_name, "output": safe_to_dict(node_output)},
                        )
                    )
                    # yield _sse(
                    #     event="node_update", data={"node": node_name, "output": node_output}
                    # )
                # 每个节点执行后检查是否停在interrupt，必须在循环内检查
                snapshot = graph.get_state(config=config)
                # print(f"检查是否中断:{snapshot},=======,{snapshot.next}")
                if snapshot.next:
                    payload = {}
                    if snapshot.tasks and snapshot.tasks[0].interrupts:
                        interrupt_obj = snapshot.tasks[0].interrupts[0]
                        payload = safe_to_dict(interrupt_obj.value)
                        print(f"中断的结果:{payload}")
                        queue.put_nowait(  # 个人觉得应该有真正中断时才推入，但是前面也会有一条_interrupt_的推入（在前面遍历其他节点时）
                            (
                                "interrupt",
                                {
                                    "thread_id": config.get("configurable", {}).get(
                                        "thread_id", ""
                                    ),
                                    "payload": payload,
                                },
                            )
                        )
                    # if snapshot.tasks and snapshot.tasks[0].interrupts:
                    #     payload = snapshot.tasks[0].interrupts[0].value
                    # print(f"中断的结果:{payload}")
                    # yield _sse(
                    #     event="interrupt",
                    #     data={
                    #         "thread_id": config.get("configurable", {}).get(
                    #             "thread_id", ""
                    #         ),
                    #         "payload": payload,
                    #     },
                    # )
            # 正常结束
            queue.put_nowait(("done", {}))
        except Exception as e:
            # 流内异常不能抛给HTTP，转成error事件让前端显示
            queue.put_nowait(("error", {"message": str(e)}))
            # yield _sse("error", {"message": str(e)})
        finally:
            # yield _sse("done", {})
            stop_event.set()

    # 包装worker，兜底捕获线程内部异常
    async def worker_wrapper():
        try:
            await run_in_threadpool(sync_stream_worker)
        except Exception as e:
            queue.put_nowait(("error", {"message": f"worker crash: {str(e)}"}))
            stop_event.set()

    worker_task = asyncio.create_task(worker_wrapper())
    # 主线程消费队列
    while not stop_event.is_set() or not queue.empty():
        try:
            event_name, data = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            continue

        yield _sse(event=event_name, data=data)

    await worker_task


# 初始（拿图实例+创建本次图会话id)
@router.post("/start")
async def start_graph(request: Request, req: StartRequest):
    print("进入初始阶段", req.requirement)
    graph = request.app.state.graph
    id = f"th-{uuid.uuid4().hex[:12]}"
    config: RunnableConfig = {"configurable": {"thread_id": id}}
    print("拿到全局graph实例，创建会话id")

    async def generate() -> AsyncGenerator[dict[str, Any], None]:
        # 将会话id推给前端用
        print("开始推id")
        yield _sse(event="started", data={"thread_id": id})
        print(f"推了id:{id}")
        async for event in _graph_stream(
            graph=graph, input_or_command={"input_txt": req.requirement}, config=config
        ):
            yield event

    return EventSourceResponse(
        generate(),
        ping=10,  # 每10s发ping包，强制刷buffer、防代理掐断连接
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Connection": "keep-alive",
        },
    )


@router.post("/resume")
async def resume_graph(request: Request, req: ResumeRequest):
    graph = request.app.state.graph
    config: RunnableConfig = {"configurable": {"thread_id": req.id}}
    decision = {"action": req.requireType, "reason": req.reason}
    if req.newRequirement:
        decision["new_input"] = req.newRequirement

    async def generate() -> AsyncGenerator[dict[str, Any], None]:
        async for event in _graph_stream(graph, Command(resume=decision), config):
            yield event

    return EventSourceResponse(
        generate(),
        ping=10,  # 每10s发ping包，强制刷buffer、防代理掐断连接
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Connection": "keep-alive",
        },
    )


# 刷新恢复：返回停在哪个节点、interrupt payload、当前 state。
@router.get("/state/{id}")
async def state_graph(require: Request, id: str) -> dict[str, Any]:
    graph = require.app.state.graph
    config: RunnableConfig = {"configurable": {"thread_id": id}}
    snapshot = graph.get_state(config)  # 拿到当前会话图流程所存储的状态数据
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="thread not found")
    interrupt_payload = None
    if snapshot.next and snapshot.tasks and snapshot.tasks[0].interrupts:
        interrupt_payload = snapshot.tasks[0].interrupts[0].value
    return {
        "thread_id": id,
        "next": list(snapshot.next),
        "values": snapshot.values,
        "interrupt": interrupt_payload,
    }


# 失败重试
@router.post("/retry")
async def retry_node(require: Request, req: RetryRequest):
    graph = require.app.state.graph
    config: RunnableConfig = {"configurable": {"thread_id": req.id}}
    if req.retryType == "1":  # 重新执行sql
        print("进入这里重新执行")
        graph.update_state(
            config,
            {
                "final_result": None,
            },
            as_node="evalute_node",  # 让图返回到哪个节点
        )
        total = graph.get_state(config=config)
        print(str(total))
    else:  # 直接重新生成
        graph.update_state(
            config,
            {
                "generated_sql": None,
                "risk_level": None,
                "risk_reason": None,
                "reject_reason": None,
                "approvval_decision": None,
                "final_result": None,
            },
            as_node="parse_node-",
        )

    async def generate() -> AsyncGenerator[dict[str, Any], None]:
        async for event in _graph_stream(graph, None, config):
            yield event

    return EventSourceResponse(
        generate(),
        ping=10,  # 每10s发ping包，强制刷buffer、防代理掐断连接
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Connection": "keep-alive",
        },
    )
