from app.config import ALLOWED_ORIGINS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from typing import Any
import sys
from app.graph.builder import create_graph
from app.db.init import init_sql_data
from app.routes.agent import router


# lifespan签名标准：async def lifespan(app: FastAPI):
@asynccontextmanager
async def life_span(app: FastAPI):
    # 初始化数据库--同步(run_in_threadpool同步阻塞操作丢到线程池，不阻塞事件循环)
    await run_in_threadpool(init_sql_data)
    # 初始化图实例--同步
    graph, saver_ctx = await run_in_threadpool(create_graph)
    app.state.graph = graph
    app.state.saver_ctx = saver_ctx
    try:
        yield
    finally:
        # 释放postgersaver的连接上下文--__exit__为同步
        exc_type, exc, tb = sys.exc_info()
        await run_in_threadpool(app.state.saver_ctx.__exit__, exc_type, exc, tb)


app = FastAPI(
    title="approve_api",
    description="sql审批接口注册",
    debug=True,
    version="1.0",
    lifespan=life_span,  # `lifespan` 是 FastAPI 的**应用生命周期上下文管理器**，用来管理：**应用启动时初始化资源、应用关闭时优雅释放资源**，替代旧的 `@app.on_event("startup")` / `@app.on_event("shutdown")`
)

# *号表示任意都通行
app.add_middleware(
    CORSMiddleware,  # FastAPI 封装的跨域中间件，底层是 starlette 的 CORSMiddleware，用来**解决浏览器同源策略的跨域请求拦截**（前端域名和后端域名 / 端口不一样时报 CORS 报错）
    allow_origins=ALLOWED_ORIGINS,
    allow_headers=["*"],  # 允许跨域请求携带哪些请求头（X-Token、Authorization等）
    allow_methods=["*"],  # 允许跨域请求使用哪些 HTTP 请求方法（POST，GET，OPTIONS等）
    allow_credentials=True,  # 是否允许跨域请求携带凭证(Cookie、HTTP Authorization 头部、客户端 SSL 证书等)
)

app.include_router(router=router)


@app.get(path="/test", description="测试")
def test() -> Any:
    return {"code": 0, "msg": "success"}


# if __name__ == "__main__":  # 当前模板作为主模块运行时
#     import uvicorn

#     uvicorn.run(
#         app="api_fastapi:app", host="127.0.0.1", port=8000, reload=True
#     )  # app+reload实现热更新
