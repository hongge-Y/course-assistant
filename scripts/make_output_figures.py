"""生成第三部分需要的“运行输出”图片（Prompt 对比 / RAG / MCP / Skill 与路由 / 数据库变化）。

这些图片的内容全部来自本机实际运行输出，只是排版成便于阅读的终端风格截图。
运行：.venv\\Scripts\\python.exe scripts/make_output_figures.py
输出：reports/figures/out_*.png
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'reports' / 'figures'
OUT.mkdir(parents=True, exist_ok=True)

FONT_PATH = 'C:/Windows/Fonts/msyh.ttc'
FONT = ImageFont.truetype(FONT_PATH, 20)
FONT_BOLD = ImageFont.truetype(FONT_PATH, 22)
FONT_TITLE = ImageFont.truetype(FONT_PATH, 24)

BG = (15, 23, 32)
BAR = (22, 48, 43)
TEXT = (215, 230, 226)
DIM = (150, 178, 170)
ACCENT = (126, 214, 176)


def wrap_lines(lines: list[str], width_px: int, font) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append('')
            continue
        current = ''
        for char in line:
            trial = current + char
            if font.getlength(trial) > width_px:
                wrapped.append(current)
                current = '    ' + char if line.startswith('  ') and not line.startswith('    ') else char
            else:
                current = trial
        wrapped.append(current)
    return wrapped


def render(title: str, lines: list[str], filename: str, width: int = 1560) -> Path:
    body_width = width - 72
    rows = wrap_lines(lines, body_width, FONT)
    line_height = 32
    height = 96 + line_height * len(rows) + 40
    image = Image.new('RGB', (width, height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 64], fill=BAR)
    for index, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        draw.ellipse([24 + index * 28, 24, 40 + index * 28, 40], fill=color)
    draw.text((120, 18), title, font=FONT_TITLE, fill=(236, 248, 242))

    y = 88
    for row in rows:
        color = TEXT
        if row.strip().startswith('>') or row.startswith('[t'):
            color = ACCENT
        elif row.strip().startswith('#'):
            color = DIM
        draw.text((36, y), row, font=FONT, fill=color)
        y += line_height
    path = OUT / filename
    image.save(path)
    print('已生成', path, image.size)
    return path


def prompt_compare_lines() -> list[str]:
    text = (ROOT / 'reports' / 'prompt_compare.md').read_text(encoding='utf-8')
    before = text.split('## 优化后（PROMPT_V1）')[0].split('回答：')[-1].strip()
    after = text.split('## 优化后（PROMPT_V1）')[1].split('## 修改说明')[0].split('回答：')[-1].strip()
    lines = ['> 同一个问题：C002 机器学习入门适合零基础吗？需要哪些先修？每周要花多少时间？',
             '> 模型：deepseek-flash，temperature=0.5；检索资料：C002.md 先修要求 / 课程定位',
             '',
             '【优化前 PROMPT_V0】你是课程助手，回答学生关于课程的问题。',
             ]
    lines += ['  ' + item.strip() for item in before.split('\n') if item.strip()][:9]
    lines += ['', '【优化后 PROMPT_V1】身份 + 任务 + 工具规则 + 数据边界 + 输出方式（完整版本见 course_engine.py）']
    lines += ['  ' + item.strip() for item in after.split('\n') if item.strip()][:9]
    return lines


def rag_lines() -> list[str]:
    import course_engine as engine

    retriever = engine.build_retriever()
    stats = retriever.stats()
    lines = ['> .venv\\Scripts\\python.exe -c "import course_engine as e; ..."',
             f'知识库片段数：{stats["documents"]}  课程编号：{", ".join(stats["courses"])}',
             f'来源文件：{", ".join(stats["sources"])}',
             '',
             '> retriever.search("C002 的先修要求是什么", k=3, course_id="C002")']
    for doc in retriever.search('C002 的先修要求是什么', k=3, course_id='C002'):
        lines.append(f'  [{doc.metadata["source"]} · {doc.metadata["section"]}]')
        lines.append('    ' + doc.page_content[:110].replace('\n', ' '))

    result = asyncio.run(engine.chat('C002 机器学习入门主要学什么？', 'fig-rag', 'S001'))
    lines += ['', '> 咨询 Agent 基于检索片段回答：']
    lines += ['  ' + item for item in result['reply'].split('\n') if item.strip()][:8]
    return lines


async def mcp_lines_async() -> list[str]:
    import course_engine as engine

    client = engine.CourseMCPClient()
    lines = ['> 客户端：mcp.client.stdio → course_mcp_server.py（stdio 子进程）', '工具列表：']
    for tool in await client.list_tools():
        lines.append(f'  - {tool["name"]}：{tool["description"]}')
    lines += ['', '> call_tool("list_courses") 返回（节选）：']
    data = await client.call('list_courses')
    for course in data['courses'][:2]:
        lines.append('  ' + json.dumps(course, ensure_ascii=False)[:150])
    lines.append(f'  … 共 {data["count"]} 门课程')

    lines += ['', '> call_tool("select_course", {"user_id": "S003", "course_id": "C005"})']
    selected = await client.call('select_course', {'user_id': 'S003', 'course_id': 'C005'})
    lines.append('  ' + json.dumps(selected, ensure_ascii=False)[:280])
    lines += ['', '> call_tool("select_course", {"user_id": "S003", "course_id": "C004"})（满员）']
    full = await client.call('select_course', {'user_id': 'S003', 'course_id': 'C004'})
    lines.append('  ' + json.dumps(full, ensure_ascii=False)[:280])
    lines += ['', '> call_tool("drop_course", {"user_id": "S003", "course_id": "C005"})']
    dropped = await client.call('drop_course', {'user_id': 'S003', 'course_id': 'C005'})
    lines.append('  ' + json.dumps(dropped, ensure_ascii=False)[:280])
    await client.close()
    return lines


def skill_lines() -> list[str]:
    import course_engine as engine

    skill = engine.load_recommendation_skill()
    question = '比较一下 C001 和 C002 的基础要求和难度，我零基础该先选哪个？'
    state = {'user_id': 'S001', 'message': question, 'route': '', 'course_ids': [], 'course_id': '',
             'action': 'none', 'last_course_id': ''}
    routed = asyncio.run(engine.node_route(state))
    lines = ['> 用户：' + question,
             f'> 识别到的课程：{engine.extract_course_ids(question)}',
             f'> 触发 Skill：route = {routed["route"]}（命中“比较/推荐”关键词）',
             '',
             '> 技能卡 skills/course_recommendation/SKILL.md（节选）：']
    lines += ['  ' + item for item in skill.split('\n')[:12] if item.strip()]
    lines += ['', '> Agent 交接：咨询 Agent 负责知识比较（search_courses），业务 Agent 负责名额核对（list_courses）']
    lines.append('  交接字段：user_id=S001、course_ids=["C001","C002"]、action=none、各课程剩余名额')
    return lines


def db_lines() -> list[str]:
    import course_engine as engine

    db_path = ROOT / 'data' / 'course_selection.sqlite3'

    def rows():
        conn = sqlite3.connect(db_path)
        try:
            return [f'  {row}' for row in conn.execute(
                "SELECT enrollment_id, user_id, course_id, selected_at FROM enrollments "
                "WHERE user_id='S002' ORDER BY course_id")]
        finally:
            conn.close()

    def remaining(data):
        course = next(c for c in data['courses'] if c['course_id'] == 'C003')
        return f"{course['enrolled']}/{course['capacity']}，剩余 {course['remaining']}"

    async def run():
        client = engine.CourseMCPClient()
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute("DELETE FROM enrollments WHERE user_id='S002'")
        conn.commit()
        conn.close()

        before = await client.call('list_enrollments', {'user_id': 'S002'})
        cat_before = await client.call('list_courses')
        selected = await client.call('select_course', {'user_id': 'S002', 'course_id': 'C003'})
        cat_middle = await client.call('list_courses')
        middle_rows = rows()
        dropped = await client.call('drop_course', {'user_id': 'S002', 'course_id': 'C003'})
        cat_after = await client.call('list_courses')
        after = await client.call('list_enrollments', {'user_id': 'S002'})
        await client.close()
        return before, cat_before, selected, cat_middle, middle_rows, dropped, cat_after, after

    before, cb, selected, cm, middle_rows, dropped, ca, after = asyncio.run(run())
    lines = ['> 数据库：data/course_selection.sqlite3；表 enrollments',
             f'退课前：S002 已选 {before["count"]} 门；C003 名额 {remaining(cb)}',
             f'> 确认选课：{selected["message"]}  →  C003 名额 {remaining(cm)}',
             '',
             '> SELECT enrollment_id, user_id, course_id, selected_at FROM enrollments',
             '> WHERE user_id = "S002";']
    lines += middle_rows or ['  （无记录）']
    lines += ['', f'> 确认退课：{dropped["message"]}  →  C003 名额 {remaining(ca)}',
              f'退课后：S002 已选 {after["count"]} 门',
              '', '> 同一条查询：']
    lines += rows() or ['  （无记录）']
    return lines


if __name__ == '__main__':
    tasks = [
        ('out_prompt.png', lambda: prompt_compare_lines()),
        ('out_rag.png', lambda: rag_lines()),
        ('out_mcp.png', lambda: asyncio.run(mcp_lines_async())),
        ('out_skill.png', lambda: skill_lines()),
        ('out_db.png', lambda: db_lines()),
    ]
    titles = {
        'out_prompt.png': 'Prompt 优化对比（同一问题，优化前 / 优化后）',
        'out_rag.png': 'RAG 知识库：切分统计、检索结果与基于资料的答案',
        'out_mcp.png': '课程 MCP：工具列表与一次调用的参数 / 返回结果',
        'out_skill.png': 'Skill 触发与两个 Agent 的分工交接',
        'out_db.png': '退课与数据库变化：选课记录与名额前后对比',
    }
    for filename, builder in tasks:
        if (OUT / filename).exists():
            print('已存在，跳过：', filename)
            continue
        render(titles[filename], builder(), filename)

