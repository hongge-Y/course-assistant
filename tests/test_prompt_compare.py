"""Prompt 优化对比：同一问题在 PROMPT_V0（简单提示词）与 PROMPT_V1（项目提示词）下的回答。

运行：.venv\\Scripts\\python.exe tests/test_prompt_compare.py
输出：reports/prompt_compare.md
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine

QUESTION = 'C002 机器学习入门适合零基础吗？需要哪些先修？每周要花多少时间？'
OUT = ROOT / 'reports' / 'prompt_compare.md'


def ask_with_prompt(model, prompt: str, context: str) -> str:
    message = (
        f'{prompt}\n\n【课程资料片段（检索结果）】\n{context}\n\n'
        f'【学生问题】{QUESTION}'
    )
    response = model.invoke(message)
    content = response.content
    if isinstance(content, list):
        content = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return str(content).strip()


def main() -> None:
    retriever = engine.build_retriever()
    docs = retriever.search(QUESTION, k=4, course_id='C002')
    context = engine.format_context(docs)

    model_v0 = engine.create_model(temperature=0.5)
    answer_v0 = ask_with_prompt(model_v0, engine.PROMPT_V0, context)
    time.sleep(1)
    answer_v1 = ask_with_prompt(model_v0, engine.PROMPT_V1, context)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        '# Prompt 优化前后对比\n\n'
        f'- 模型：{engine.CHAT_MODEL}，temperature=0.5\n'
        f'- 测试问题：{QUESTION}\n'
        f'- 检索到的资料：{", ".join(engine.format_sources(docs))}\n\n'
        '## 优化前（PROMPT_V0）\n\n'
        '```text\n' + engine.PROMPT_V0 + '\n```\n\n'
        f'回答：\n\n{answer_v0}\n\n'
        '## 优化后（PROMPT_V1）\n\n'
        '```text\n' + engine.PROMPT_V1 + '\n```\n\n'
        f'回答：\n\n{answer_v1}\n\n'
        '## 修改说明\n'
        '1. 增加身份与任务边界，明确“课程内容先检索、名额与选退课结果以数据库为准”，避免模型编造数字；\n'
        '2. 要求资料未提及时明确说明，减少“每周课时”这类未收录信息被臆测；\n'
        '3. 规定输出长度与结构（要点式、标注来源），便于网页展示与核对。\n',
        encoding='utf-8',
    )
    print(f'已生成 {OUT}')
    print('\n--- V0 ---\n', answer_v0[:600])
    print('\n--- V1 ---\n', answer_v1[:600])


if __name__ == '__main__':
    main()
