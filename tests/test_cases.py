"""端到端测试：通过 course_engine.chat 覆盖任务说明要求的测试场景。

运行：.venv\\Scripts\\python.exe tests/test_cases.py
输出：控制台摘要 + reports/test_results.md（输入 / 预期 / 实际 / 是否通过）
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine

DB = ROOT / 'data' / 'course_selection.sqlite3'
REPORT = ROOT / 'reports' / 'test_results.md'
USERS = ('S001', 'S002', 'S003')


def db_rows(sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def reset_demo_users() -> None:
    conn = sqlite3.connect(DB)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute("DELETE FROM enrollments WHERE user_id IN ('S001','S002','S003')")
        conn.commit()
    finally:
        conn.close()


def enrolled_ids(user_id: str) -> list[str]:
    return [row[0] for row in db_rows(
        'SELECT course_id FROM enrollments WHERE user_id = ? ORDER BY course_id', (user_id,))]


def remaining(course_id: str) -> int:
    row = db_rows(
        'SELECT (SELECT capacity FROM courses WHERE course_id = ?) - '
        'COUNT(*) FROM enrollments WHERE course_id = ?', (course_id, course_id))
    return int(row[0][0])


RESULTS: list[dict] = []


async def ask(session: str, message: str, user: str = 'S001', use_web: bool = False) -> dict:
    started = time.time()
    result = await engine.chat(message, session, user, use_web=use_web)
    result['_elapsed'] = round(time.time() - started, 1)
    print(f'\n[{session}] {user} > {message}\n  < {result["reply"][:400]}'
          f'\n  (waiting={result["waiting_for_confirmation"]}, pending={result["pending_action"]}, {result["_elapsed"]}s)')
    return result


def record(no: int, scenario: str, message: str, expected: str, result: dict, checks: dict[str, bool],
           extra: str = '') -> None:
    passed = all(checks.values())
    reply = str(result.get('reply', ''))
    RESULTS.append({
        'no': no, 'scenario': scenario, 'input': message, 'expected': expected,
        'actual': reply.replace('\n', ' ')[:400],
        'waiting': result.get('waiting_for_confirmation'), 'pending': result.get('pending_action'),
        'checks': checks, 'passed': passed, 'extra': extra, 'elapsed': result.get('_elapsed', 0),
    })
    print(f'  => 测试 {no} {"通过" if passed else "未通过"} {checks}')


async def main() -> None:
    reset_demo_users()
    await engine.warmup()

    # 1 课程咨询
    r = await ask('t1', 'C002 主要学什么？')
    record(1, '课程咨询', 'C002 主要学什么？', '依据 C002 资料回答课程内容',
           r, {'包含C002': 'C002' in r['reply'], '包含内容关键词': any(k in r['reply'] for k in ('模块', '算法', '预处理'))})

    # 2 持续追问（同一会话）
    r = await ask('t1', '这门课需要什么基础？')
    record(2, '持续追问', '这门课需要什么基础？', '结合上下文识别 C002，回答先修要求',
           r, {'先修关键词': any(k in r['reply'] for k in ('Python', '线性代数', '概率'))})

    # 3 比较与推荐
    r = await ask('t3', '比较一下 C001 和 C002 的基础要求和难度，我零基础该先选哪个？')
    record(3, '课程比较与推荐', '比较 C001 和 C002 …零基础该先选哪个', '分别检索两门课程并给出推荐依据',
           r, {'提到C001': 'C001' in r['reply'], '提到C002': 'C002' in r['reply'],
               '给出推荐': any(k in r['reply'] for k in ('建议', '推荐', '先选'))})

    # 4 教室与名额
    r = await ask('t4', 'C003 在哪里上课？还有名额吗？')
    record(4, '教室和名额查询', 'C003 在哪里上课？还有名额吗？', '教室为实验楼 B 座 201，名额来自数据库',
           r, {'教室正确': 'B 座 201' in r['reply'] or 'B座201' in r['reply'],
               '含名额数字': '剩余' in r['reply'] or '名额' in r['reply']})

    # 5 C004 满员
    before = remaining('C004')
    r = await ask('t5', '我想选 C004')
    record(5, 'C004 已满', '我想选 C004', '提示已满并建议其他课程，不进入确认',
           r, {'提示已满': '已满' in r['reply'], '未等待确认': r['waiting_for_confirmation'] is False,
               '未写库': enrolled_ids('S001') == []},
           extra=f'C004 操作前剩余 {before}，操作后剩余 {remaining("C004")}')

    # 6 取消
    r1 = await ask('t6', '我想选 C001')
    r2 = await ask('t6', '取消')
    record(6, '选课请求后取消', '我想选 C001 → 取消', '第一次返回确认提示；取消后数据库不变',
           r2, {'先要求确认': r1['waiting_for_confirmation'] is True and r1['pending_action'] == 'select',
                '取消提示': '取消' in r2['reply'], '未写库': enrolled_ids('S001') == []})

    # 7 确认选课
    r1 = await ask('t7', '我想选 C001')
    r2 = await ask('t7', '确认')
    record(7, '选课请求后确认', '我想选 C001 → 确认', '写入数据库，返回真实名额',
           r2, {'确认后不等待': r2['waiting_for_confirmation'] is False,
                '数据库有记录': 'C001' in enrolled_ids('S001'),
                '真实名额': f'剩余 {remaining("C001")}' in r2['reply'] or '选课成功' in r2['reply']})

    # 8 重复选课
    r = await ask('t8', '我想选 C001')
    record(8, '重复选择同一课程', '我想选 C001（已选）', '拒绝重复选择，不进入确认',
           r, {'拒绝重复': any(k in r['reply'] for k in ('已经选过', '重复', '不能重复')),
               '未进入确认': r['waiting_for_confirmation'] is False})

    # 9 个人上限
    await ask('t9', '我想选 C002')
    await ask('t9', '确认')
    r = await ask('t9', '我想选 C003')
    record(9, '尝试选择第 3 门课', '已选 2 门后再选 C003', '提示达到每人 2 门上限',
           r, {'提示上限': '上限' in r['reply'], '未进入确认': r['waiting_for_confirmation'] is False,
               '记录仍为2门': len(enrolled_ids('S001')) == 2})

    # 10 退课与名额变化
    before = remaining('C001')
    r1 = await ask('t10', '帮我退掉 C001')
    r2 = await ask('t10', '确认')
    after = remaining('C001')
    record(10, '退课及名额变化', '帮我退掉 C001 → 确认', '删除选课记录并释放名额',
           r2, {'退课成功': '退课成功' in r2['reply'], '记录已删除': 'C001' not in enrolled_ids('S001'),
                '名额增加': after == before + 1},
           extra=f'C001 剩余名额 {before} → {after}')

    # 11 知识库没有答案
    r = await ask('t11', 'C002 的任课老师是谁？期末考试具体日期是哪天？')
    record(11, '知识库没有答案', 'C002 的任课老师是谁？期末考试具体日期？', '说明资料未提及，不编造',
           r, {'说明未提及': any(k in r['reply'] for k in ('未提及', '没有', '未收录', '无法')),
               '不编造日期': not any(k in r['reply'] for k in ('年'+'月', '星期', '上午', '下午'))})

    # 12 联网补充（未配置密钥 → 回退）
    r = await ask('t12', '联网查一下机器学习入门的学习资料', use_web=True)
    record(12, 'Tavily 搜索及失败回退', '联网查一下机器学习入门的学习资料（TAVILY_API_KEY 为空）',
           '提示联网不可用，仍用本地知识库回答',
           r, {'提示联网不可用': any(k in r['reply'] for k in ('TAVILY_API_KEY', '联网', '不可用')),
               '仍有课程回答': 'C002' in r['reply'] or '机器学习' in r['reply']})

    # 13 部署与持久化：重新打开数据库验证记录仍在
    await ask('t13', '我想选 C005')
    await ask('t13', '确认')
    rows = db_rows("SELECT user_id, course_id FROM enrollments WHERE user_id='S001' ORDER BY course_id")
    record(13, '部署与数据库持久化', '确认选课后直接读取数据库文件', '选课记录持久化在 SQLite',
           {'reply': f'数据库记录：{rows}', 'waiting_for_confirmation': False, 'pending_action': None},
           {'记录存在': ('S001', 'C005') in rows})

    # 14 我的选课
    r = await ask('t14', '我的选课')
    record(14, '我的选课查询', '我的选课', '列出当前用户已选课程与教室',
           r, {'列出已选': 'C005' in r['reply'], '含教室': '教室' in r['reply'] or '座' in r['reply']})

    # 15 满员失败后继续请求（回归测试：预检失败不应留下待确认状态）
    r1 = await ask('t15', '我想选 C004', user='S002')
    r2 = await ask('t15', '我想选 C001', user='S002')
    record(15, '满员后继续请求（状态回归）', '我想选 C004（已满）→ 我想选 C001',
           '第二条消息应重新进入选课确认，而不是被当作确认/取消',
           r2, {'满员不应等待确认': r1['waiting_for_confirmation'] is False,
                '重新进入确认': r2['waiting_for_confirmation'] is True and r2['pending_action'] == 'select'})
    await ask('t15', '取消', user='S002')

    reset_demo_users()

    passed = sum(1 for item in RESULTS if item['passed'])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = ['# 课程选课智能助手 · 端到端测试记录', '',
             f'- 生成时间：{time.strftime("%Y-%m-%d %H:%M:%S")}',
             f'- 结果：{passed}/{len(RESULTS)} 通过', '',
             '| 编号 | 测试场景 | 输入 | 预期结果 | 实际结果 | 是否通过 |',
             '|---|---|---|---|---|---|']
    for item in RESULTS:
        actual = item['actual'].replace('|', '｜')
        lines.append(
            f"| {item['no']} | {item['scenario']} | {item['input']} | {item['expected']} | {actual}"
            f"（waiting={item['waiting']}, pending={item['pending']}） | {'通过' if item['passed'] else '未通过'} |")
    lines += ['', '## 校验明细', '']
    for item in RESULTS:
        failed = [name for name, ok in item['checks'].items() if not ok]
        lines.append(f"- 测试 {item['no']}：{'全部校验通过' if not failed else '未通过校验：' + '、'.join(failed)}"
                     + (f"；备注：{item['extra']}" if item['extra'] else '')
                     + f"；耗时 {item['elapsed']}s")
    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    (REPORT.parent / 'test_results.json').write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n== {passed}/{len(RESULTS)} 通过，报告：{REPORT}')

    await engine.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
