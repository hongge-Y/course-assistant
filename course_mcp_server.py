"""课程业务 MCP Server：课程目录、我的选课、选课、退课四个工具。

由 course_engine.py 以 stdio 方式启动（MCP ClientSession），也可单独运行：
    python course_mcp_server.py

业务约束（与项目任务说明一致）：
- 每门课程最多 20 人（courses.capacity）；
- 每位学生最多选 2 门；
- 同一学生不能重复选择同一门课程（enrollments 唯一约束）；
- 写操作在 BEGIN IMMEDIATE 事务内重新校验名额与选课记录，防止并发抢最后名额；
- 每个连接都开启 PRAGMA foreign_keys = ON。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).resolve().parent / 'data' / 'course_selection.sqlite3'
COURSE_LIMIT = 20
USER_LIMIT = 2

mcp = FastMCP('course-selection-tools')


# ---------------------------------------------------------------- 数据库工具
def _connect() -> sqlite3.Connection:
    """建立连接：开启外键约束、设置忙等待，使用手动事务。"""
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 15000')
    return conn


def _begin(conn: sqlite3.Connection, attempts: int = 5) -> None:
    """获取写锁（IMMEDIATE）；数据库繁忙时短暂重试。"""
    for index in range(attempts):
        try:
            conn.execute('BEGIN IMMEDIATE')
            return
        except sqlite3.OperationalError:
            if index == attempts - 1:
                raise
            time.sleep(0.2 * (index + 1))


def _course_row(conn: sqlite3.Connection, course_id: str) -> sqlite3.Row | None:
    return conn.execute(
        'SELECT course_id, name, capacity, equipment, software, classroom '
        'FROM courses WHERE course_id = ?', (course_id,),
    ).fetchone()


def _enrolled_count(conn: sqlite3.Connection, course_id: str) -> int:
    return conn.execute(
        'SELECT COUNT(*) FROM enrollments WHERE course_id = ?', (course_id,),
    ).fetchone()[0]


def _user_count(conn: sqlite3.Connection, user_id: str) -> int:
    return conn.execute(
        'SELECT COUNT(*) FROM enrollments WHERE user_id = ?', (user_id,),
    ).fetchone()[0]


def _available_courses(conn: sqlite3.Connection, exclude: str = '') -> list[str]:
    rows = conn.execute(
        'SELECT c.course_id, c.name, c.capacity, COUNT(e.enrollment_id) AS n '
        'FROM courses c LEFT JOIN enrollments e ON e.course_id = c.course_id '
        'GROUP BY c.course_id ORDER BY c.course_id'
    ).fetchall()
    return [
        f'{row["course_id"]} {row["name"]}'
        for row in rows
        if row['course_id'] != exclude and row['n'] < row['capacity']
    ]


# ------------------------------------------------------------------- 四个工具
@mcp.tool()
def list_courses() -> dict:
    """查询课程目录：课程名称、教室、已选人数与剩余名额。"""
    with _connect() as conn:
        rows = conn.execute(
            'SELECT c.course_id, c.name, c.capacity, c.equipment, c.software, c.classroom, '
            'COUNT(e.enrollment_id) AS enrolled '
            'FROM courses c LEFT JOIN enrollments e ON e.course_id = c.course_id '
            'GROUP BY c.course_id ORDER BY c.course_id'
        ).fetchall()
    courses = []
    for row in rows:
        enrolled = int(row['enrolled'])
        courses.append({
            'course_id': row['course_id'],
            'name': row['name'],
            'classroom': row['classroom'],
            'equipment': row['equipment'],
            'software': row['software'],
            'capacity': int(row['capacity']),
            'enrolled': enrolled,
            'remaining': max(int(row['capacity']) - enrolled, 0),
            'full': enrolled >= int(row['capacity']),
        })
    return {'ok': True, 'count': len(courses), 'courses': courses}


@mcp.tool()
def list_enrollments(user_id: str) -> dict:
    """查询指定用户当前已选课程、教室与选课时间。"""
    with _connect() as conn:
        user = conn.execute(
            'SELECT user_id, name FROM users WHERE user_id = ?', (user_id,),
        ).fetchone()
        if user is None:
            return {'ok': False, 'code': 'NO_USER', 'message': f'用户 {user_id} 不存在。'}
        rows = conn.execute(
            'SELECT e.enrollment_id, e.course_id, c.name, c.classroom, e.selected_at '
            'FROM enrollments e JOIN courses c ON c.course_id = e.course_id '
            'WHERE e.user_id = ? ORDER BY e.selected_at, e.enrollment_id', (user_id,),
        ).fetchall()
    return {
        'ok': True, 'user_id': user['user_id'], 'name': user['name'],
        'limit': USER_LIMIT,
        'count': len(rows),
        'courses': [dict(row) for row in rows],
    }


@mcp.tool()
def select_course(user_id: str, course_id: str) -> dict:
    """选课（需用户确认后调用）。事务内校验课程存在、重复、满员与个人上限。"""
    course_id = (course_id or '').strip().upper()
    conn = _connect()
    try:
        _begin(conn)
        user = conn.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if user is None:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'NO_USER', 'message': f'用户 {user_id} 不存在。'}
        course = _course_row(conn, course_id)
        if course is None:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'NO_COURSE', 'message': f'课程 {course_id} 不存在。'}
        duplicate = conn.execute(
            'SELECT 1 FROM enrollments WHERE user_id = ? AND course_id = ?',
            (user_id, course_id),
        ).fetchone()
        if duplicate is not None:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'DUPLICATE',
                    'message': f'你已经选过 {course_id} {course["name"]}，不能重复选择。'}
        enrolled = _enrolled_count(conn, course_id)
        if enrolled >= int(course['capacity']):
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'FULL',
                    'message': f'{course_id} {course["name"]} 已满（{enrolled}/{course["capacity"]}）。',
                    'suggestions': _available_courses(conn, exclude=course_id)}
        mine = _user_count(conn, user_id)
        if mine >= USER_LIMIT:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'USER_LIMIT',
                    'message': f'你已选 {mine} 门课程，达到每人最多 {USER_LIMIT} 门的上限，请先退课。'}
        conn.execute(
            'INSERT INTO enrollments (user_id, course_id) VALUES (?, ?)', (user_id, course_id),
        )
        enrolled += 1
        conn.execute('COMMIT')
        return {
            'ok': True, 'code': 'OK',
            'message': f'选课成功：{course_id} {course["name"]}。',
            'course_id': course_id, 'name': course['name'],
            'classroom': course['classroom'],
            'enrolled': enrolled, 'capacity': int(course['capacity']),
            'remaining': max(int(course['capacity']) - enrolled, 0),
            'mine': mine + 1,
        }
    except sqlite3.IntegrityError as error:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        # 触发器或唯一约束兜底：并发下仍可能有人抢先占满名额
        return {'ok': False, 'code': 'INTEGRITY', 'message': f'选课失败（数据库校验未通过）：{error}'}
    except sqlite3.OperationalError as error:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        return {'ok': False, 'code': 'BUSY', 'message': f'数据库繁忙，请稍后重试：{error}'}
    finally:
        conn.close()


@mcp.tool()
def drop_course(user_id: str, course_id: str) -> dict:
    """退课（需用户确认后调用）：删除本人选课记录并释放名额。"""
    course_id = (course_id or '').strip().upper()
    conn = _connect()
    try:
        _begin(conn)
        course = _course_row(conn, course_id)
        if course is None:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'NO_COURSE', 'message': f'课程 {course_id} 不存在。'}
        row = conn.execute(
            'SELECT enrollment_id FROM enrollments WHERE user_id = ? AND course_id = ?',
            (user_id, course_id),
        ).fetchone()
        if row is None:
            conn.execute('ROLLBACK')
            return {'ok': False, 'code': 'NOT_ENROLLED',
                    'message': f'你没有选修 {course_id} {course["name"]}，无需退课。'}
        conn.execute('DELETE FROM enrollments WHERE enrollment_id = ?', (row['enrollment_id'],))
        conn.execute('COMMIT')
        enrolled = _enrolled_count(conn, course_id)
        return {
            'ok': True, 'code': 'OK',
            'message': f'退课成功：{course_id} {course["name"]}，名额已释放。',
            'course_id': course_id, 'name': course['name'],
            'enrolled': enrolled, 'capacity': int(course['capacity']),
            'remaining': max(int(course['capacity']) - enrolled, 0),
            'mine': _user_count(conn, user_id),
        }
    except sqlite3.OperationalError as error:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        return {'ok': False, 'code': 'BUSY', 'message': f'数据库繁忙，请稍后重试：{error}'}
    finally:
        conn.close()


if __name__ == '__main__':
    # 使用 stdio 启动，供 MCP 客户端（course_engine.py）连接
    mcp.run()
