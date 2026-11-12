"""冒烟测试：环境检查 → 知识库 → MCP 工具 → 一次咨询问答。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine


async def main() -> None:
    print('== warmup ==')
    info = await engine.warmup()
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1500])

    print('\n== knowledge chat ==')
    result = await engine.chat('C002 机器学习入门主要学什么？', 'smoke-1', 'S001', use_web=False)
    print(json.dumps(result, ensure_ascii=False)[:1200])

    print('\n== catalog ==')
    result = await engine.chat('有哪些课程？C004 还有名额吗？', 'smoke-2', 'S001', use_web=False)
    print(json.dumps(result, ensure_ascii=False)[:1200])

    print('\n== select flow (cancel) ==')
    result = await engine.chat('我想选 C001', 'smoke-3', 'S001', use_web=False)
    print(json.dumps(result, ensure_ascii=False)[:800])
    result = await engine.chat('取消', 'smoke-3', 'S001', use_web=False)
    print(json.dumps(result, ensure_ascii=False)[:800])


asyncio.run(main())
