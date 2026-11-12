"""打印推荐 Skill 的技能卡、触发路由、两个 Agent 的交接与实际推荐结果，用于截图（图 6）。

用法：.venv\\Scripts\\python.exe tests\\show_skill.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine

QUESTION = '比较一下 C001 和 C002 的基础要求和难度，我零基础该先选哪个？'


async def main() -> None:
    await engine.warmup()

    print('===== 技能卡 skills/course_recommendation/SKILL.md（节选）=====')
    for line in engine.load_recommendation_skill().split('\n'):
        if line.strip() and not line.startswith('#'):
            print('  ' + line.strip()[:70])
        elif line.startswith('#'):
            print(line)

    state = {'user_id': 'S001', 'message': QUESTION, 'route': '', 'course_ids': [],
             'course_id': '', 'action': 'none', 'last_course_id': '', 'history': []}
    routed = await engine.node_route(state)
    print()
    print('===== 触发与路由 =====')
    print(f'用户输入：{QUESTION}')
    print(f'识别到的课程编号：{engine.extract_course_ids(QUESTION)}')
    print(f'路由结果：route = {routed["route"]}，action = {routed["action"]}')

    catalog = await engine.connect_course_mcp()
    catalog_data = await catalog.call('list_courses')
    print()
    print('===== 两个 Agent 的分工与交接 =====')
    print('课程咨询 Agent：create_agent + search_courses，负责知识解释与比较')
    print('选课业务 Agent：SelectionAgent + 课程 MCP 工具，负责名额、预检与提交')
    print('交接字段：user_id=S001、course_ids=["C001","C002"]、action=none')
    for course in catalog_data['courses']:
        if course['course_id'] in ('C001', 'C002'):
            print(f"  业务 Agent 提供名额：{course['course_id']} 剩余 {course['remaining']}")
    print()
    print('===== 实际推荐结果（recommend 节点）=====')
    result = await engine.chat(QUESTION, 'fig-skill', 'S001')
    for line in result['reply'].split('\n'):
        if line.strip():
            print('  ' + line.strip())
    await engine.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
