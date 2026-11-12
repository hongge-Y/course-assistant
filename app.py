"""网页与后端的接口框架。启动：python app.py。

页面和会话入口已提供；对话、工作流和业务能力在 course_engine.py 中由学生完成。
"""
from pathlib import Path
import asyncio
import sqlite3
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import course_engine

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'data' / 'course_selection.sqlite3'


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 后台预热：知识库向量化 + 课程 MCP 连接，失败时不影响页面启动
    async def _warm():
        try:
            await course_engine.warmup()
        except Exception as error:
            print(f'[warmup] 预热失败：{error}')

    task = asyncio.create_task(_warm())
    yield
    task.cancel()
    await course_engine.shutdown()


app = FastAPI(title='课程选课智能助手', lifespan=lifespan)
# 教学用内存会话：服务重启后清空，不是正式登录认证。
sessions: dict[str, str] = {}


class CreateSession(BaseModel):
    user_id: str = Field(min_length=1, max_length=30)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    use_web: bool = False


def demo_users() -> list[dict]:
    """读取页面的演示身份；满员账号不显示在身份选择框中。"""
    with sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT user_id, name FROM users "
            "WHERE role=? AND user_id NOT LIKE ? ORDER BY user_id",
            ('student', 'DEMOFULL_%'),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get('/')
async def page():
    return FileResponse(ROOT / 'web' / 'index.html')


@app.get('/api/students')
async def get_students():
    return {'students': demo_users()}


@app.post('/api/sessions')
async def create_session(data: CreateSession):
    if data.user_id not in {user['user_id'] for user in demo_users()}:
        raise HTTPException(status_code=400, detail='演示用户不存在。')
    session_id = uuid.uuid4().hex
    sessions[session_id] = data.user_id
    return {'session_id': session_id, 'user_id': data.user_id}


@app.post('/api/chat')
async def chat(data: ChatRequest):
    user_id = sessions.get(data.session_id)
    if user_id is None:
        raise HTTPException(status_code=404, detail='对话已失效，请重新开始。')
    if not data.message.strip():
        raise HTTPException(status_code=400, detail='请输入对话内容。')
    try:
        return await course_engine.chat(
            message=data.message.strip(), session_id=data.session_id,
            user_id=user_id, use_web=data.use_web,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail='对话功能尚未实现，请完成后端 TODO。') from None


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8765)
