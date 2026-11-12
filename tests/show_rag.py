"""打印 RAG 知识库的切分统计、检索结果与基于资料的回答，用于截图（图 3）。

用法：.venv\\Scripts\\python.exe tests\\show_rag.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine


async def main() -> None:
    retriever = engine.build_retriever()
    stats = retriever.stats()
    print('===== RAG 知识库 =====')
    print(f'片段数：{stats["documents"]}')
    print(f'课程编号：{", ".join(stats["courses"])}')
    print(f'来源文件：{", ".join(stats["sources"])}')
    print()

    query = 'C002 的先修要求是什么'
    print(f'-- 检索：query = "{query}"，course_id = C002，k = 3')
    for index, doc in enumerate(retriever.search(query, k=3, course_id='C002'), start=1):
        snippet = doc.page_content.strip().replace('\n', ' ')[:100]
        print(f'  [{index}] {doc.metadata["source"]} · {doc.metadata["section"]}')
        print(f'      {snippet}…')
    print()

    print('-- 咨询 Agent 基于检索片段回答（网页同一问题）：')
    result = await engine.chat('C002 机器学习入门主要学什么？', 'fig-rag-2', 'S001')
    for line in result['reply'].split('\n'):
        if line.strip():
            print('  ' + line.strip())
    await engine.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
