"""课程 MCP 工具与并发安全测试。

运行：.venv\\Scripts\\python.exe tests/test_mcp_and_concurrency.py

覆盖：
1. 直接调用四个 MCP 业务函数（课程目录、我的选课、选课、退课、满员、重复、上限）；
2. 通过 MCP stdio 客户端列出工具并调用 list_courses（证明 MCP 接入可用）；
3. 两个用户同时抢最后一个名额，验证事务 + 触发器保证人数不超过 20。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_mcp_server as server

DB = ROOT / 'data' / 'course_selection.sqlite3'
REPORT = ROOT / 'reports' / 'mcp_and_concurrency.md'
LINES: list[str] = []


def log(text: str = '') -> None:
    print(text)
    LINES.append(text)


def db(sql: str, args: tuple = ()):
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def reset() -> None:
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute("DELETE FROM enrollments WHERE user_id LIKE 'S00%'")
        conn.execute("DELETE FROM enrollments WHERE course_id = 'C005'")
        conn.commit()
    finally:
        conn.close()


def test_tools() -> None:
    log('## 1. 课程 MCP 业务工具（直接调用）\n')
    courses = server.list_courses()
    log(f'`list_courses()` → ok={courses["ok"]}, count={courses["count"]}')
    for course in courses['courses']:
        log(f'  {course["course_id"]} {course["name"]} 教室={course["classroom"]} '
            f'{course["enrolled"]}/{course["capacity"]} 剩余={course["remaining"]}')

    log('')
    mine = server.list_enrollments('S001')
    log(f'`list_enrollments("S001")` → ok={mine["ok"]}, 已选={mine["count"]}/{mine["limit"]}')

    log('')
    full = server.select_course('S001', 'C004')
    log(f'`select_course("S001","C004")`（满员）→ ok={full["ok"]}, code={full["code"]}, 建议={full.get("suggestions")}')

    ok1 = server.select_course('S001', 'C001')
    log(f'`select_course("S001","C001")` → ok={ok1["ok"]}, {ok1["message"]} 剩余={ok1.get("remaining")}')

    dup = server.select_course('S001', 'C001')
    log(f'重复选择 → ok={dup["ok"]}, code={dup["code"]}, {dup["message"]}')

    server.select_course('S001', 'C002')
    limit = server.select_course('S001', 'C003')
    log(f'第 3 门课 → ok={limit["ok"]}, code={limit["code"]}, {limit["message"]}')

    drop = server.drop_course('S001', 'C001')
    log(f'`drop_course("S001","C001")` → ok={drop["ok"]}, {drop["message"]} 剩余={drop.get("remaining")}')

    not_mine = server.drop_course('S001', 'C005')
    log(f'退未选课程 → ok={not_mine["ok"]}, code={not_mine["code"]}, {not_mine["message"]}')


async def test_mcp_stdio() -> None:
    log('\n## 2. MCP stdio 客户端\n')
    import course_engine as engine
    client = engine.CourseMCPClient()
    try:
        tools = await client.list_tools()
        log(f'连接 {server.__file__}，工具列表：')
        for tool in tools:
            log(f'  - {tool["name"]}：{tool["description"]}')
        result = await client.call('list_courses')
        log(f'\n`call_tool("list_courses")` → ok={result["ok"]}, 返回 {result["count"]} 门课程')
    finally:
        await client.close()


def test_concurrency() -> None:
    log('\n## 3. 并发抢最后一个名额\n')
    reset()
    # 用 19 个满员演示账号占住 C001 的 19 个名额
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        for index in range(1, 20):
            conn.execute('INSERT INTO enrollments (user_id, course_id) VALUES (?, ?)',
                         (f'DEMOFULL_{index:02d}', 'C001'))
        conn.commit()
    finally:
        conn.close()
    log(f'初始化：C001 已选 19/20，剩余 1 个名额')

    results: dict[str, dict] = {}
    barrier = threading.Barrier(2)

    def worker(user_id: str) -> None:
        barrier.wait()
        results[user_id] = server.select_course(user_id, 'C001')

    threads = [threading.Thread(target=worker, args=(uid,)) for uid in ('S002', 'S003')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for user_id, result in results.items():
        log(f'{user_id} 选课结果：ok={result["ok"]}, code={result["code"]}, {result["message"][:80]}')
    winners = [uid for uid, result in results.items() if result['ok']]
    total = db("SELECT COUNT(*) FROM enrollments WHERE course_id='C001'")[0][0]
    log(f'\n成功人数={len(winners)}（期望 1），C001 总人数={total}（上限 20，期望 20）')
    log(f'结论：{"并发安全校验通过" if len(winners) == 1 and total == 20 else "并发校验未通过"}')

    # 清理
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute("DELETE FROM enrollments WHERE course_id='C001'")
        conn.commit()
    finally:
        conn.close()
    log(f'清理后 C001 人数={db("SELECT COUNT(*) FROM enrollments WHERE course_id=\'C001\'")[0][0]}')


def main() -> None:
    reset()
    test_tools()
    asyncio.run(test_mcp_stdio())
    test_concurrency()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text('# 课程 MCP 工具与并发测试\n\n```text\n' + '\n'.join(LINES) + '\n```\n', encoding='utf-8')
    print(f'\n报告：{REPORT}')


if __name__ == '__main__':
    main()
