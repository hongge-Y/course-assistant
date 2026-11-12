"""查看数据库当前状态，用于截图（图 8：选退课前后 enrollments 与名额变化）。

用法：
    .venv\\Scripts\\python.exe tests\\show_db.py 退课前
    .venv\\Scripts\\python.exe tests\\show_db.py 选课后
    .venv\\Scripts\\python.exe tests\\show_db.py 退课后
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / 'data' / 'course_selection.sqlite3'
label = sys.argv[1] if len(sys.argv) > 1 else '当前状态'

conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
print(f'===== {label}：{DB.name} =====')
print('\n-- courses（容量 / 已选 / 剩余）')
for row in conn.execute(
        'SELECT c.course_id, c.name, c.capacity, COUNT(e.enrollment_id) AS enrolled, '
        'c.classroom FROM courses c LEFT JOIN enrollments e ON e.course_id = c.course_id '
        'GROUP BY c.course_id ORDER BY c.course_id'):
    course_id, name, capacity, enrolled, classroom = row
    print(f'  {course_id} {name:<12} {enrolled}/{capacity} 剩余 {capacity - enrolled}  {classroom}')

print('\n-- enrollments（S001/S002/S003 的选课记录）')
rows = conn.execute(
    "SELECT enrollment_id, user_id, course_id, selected_at FROM enrollments "
    "WHERE user_id IN ('S001','S002','S003') ORDER BY user_id, course_id").fetchall()
if not rows:
    print('  （无记录）')
for row in rows:
    print(f'  {row}')
print(f'\n-- 记录总数（含满员演示账号）：{conn.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0]}')
conn.close()
