"""生成并执行项目 Notebook（课程选课智能助手.ipynb）。

运行：.venv\\Scripts\\python.exe scripts/build_notebook.py
"""
from __future__ import annotations

from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '课程选课智能助手.ipynb'

CELLS: list[tuple[str, str]] = [
    ('md', """# 课程选课智能助手 · 实现与测试记录

本 Notebook 记录项目的实际实现与运行结果，代码全部位于项目 Python 文件中，Notebook 只做调用与验证，避免出现两套实现：

| 文件 | 作用 |
|---|---|
| `course_mcp_server.py` | 课程业务 MCP Server（4 个工具，SQLite 事务与业务规则） |
| `course_engine.py` | 模型、Prompt、RAG、Skill、两个 Agent、LangGraph 工作流 |
| `web_search.py` | Tavily MCP 接入与失败回退 |
| `app.py` / `web/index.html` | FastAPI 接口与中文对话页面 |

启动服务：`.venv\\Scripts\\python.exe app.py`，浏览器访问 http://127.0.0.1:8765 。
"""),
    ('code', """from pathlib import Path
import sys, json, sqlite3, asyncio, time

ROOT = Path.cwd()
if not (ROOT / 'course_engine.py').exists() and (ROOT / '综合大项目' / 'course_engine.py').exists():
    ROOT = ROOT / '综合大项目'
ROOT = ROOT.resolve()
sys.path.insert(0, str(ROOT))

print('项目目录：', ROOT)
print('Python：', sys.version.split()[0], sys.executable)
print('数据库：', (ROOT / 'data' / 'course_selection.sqlite3').is_file())
print('课程文档：', [p.name for p in sorted((ROOT / 'knowledge' / 'courses').glob('*.md'))])"""),
    ('md', """## 1. 需求分析与数据库

三张表：`users`（演示身份）、`courses`（课程、容量、教室、软硬件）、`enrollments`（选课记录，`user_id + course_id` 唯一）。
业务规则：每门课 20 人、每人 2 门、禁止重复、选退课需确认、写库前事务内再次校验。
初始数据：C004 已满（20/20），其余课程 0/20。"""),
    ('code', """db_path = ROOT / 'data' / 'course_selection.sqlite3'
with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as conn:
    for table in ('users', 'courses', 'enrollments'):
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'== {table}（{count} 行）')
        print(ddl.replace('\\n', ' ')[:260], '\\n')
    print('触发器：', [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")])"""),
    ('md', """## 2. Model 与 Prompt

- 模型：`deepseek-flash`（`temperature=0.5`），通过 `.env` 的 `DEEPSEEK_API_KEY` 调用；
- Prompt 版本：`PROMPT_V0`（简单）与 `PROMPT_V1`（项目使用），`reports/prompt_compare.md` 保存了同一问题的对比结果。"""),
    ('code', """import course_engine as engine

model = engine.create_model()
print('模型：', engine.CHAT_MODEL, '| 温度：', engine.TEMPERATURE)
print('\\nPROMPT_V1 摘要：')
print('\\n'.join(engine.PROMPT_V1.strip().splitlines()[:8]))

answer = model.invoke('用一句话说明课程咨询助手的职责，不要超过 40 字。').content
print('\\n模型自述：', answer)"""),
    ('md', """## 3. RAG 课程知识库

- 资料：`knowledge/courses/课程目录.md` + `C001`—`C005` 五份课程文档；
- 切分：`MarkdownHeaderTextSplitter` 按章节 + `RecursiveCharacterTextSplitter`（420 字，重叠 60）；
- 向量：通义 `text-embedding-v1`（带本地缓存 `data/embedding_cache.json`）；
- 存储与检索：`InMemoryVectorStore` + 按课程编号过滤。"""),
    ('code', """retriever = engine.build_retriever()
print('知识库统计：', json.dumps(retriever.stats(), ensure_ascii=False))

hits = retriever.search('C002 的先修要求是什么', k=3, course_id='C002')
for doc in hits:
    print(f"- {doc.metadata['source']} · {doc.metadata['section']}：{doc.page_content[:80].replace(chr(10), ' ')}")"""),
    ('code', """result = await engine.chat('C002 机器学习入门主要学什么？', 'nb-knowledge', 'S001', use_web=False)
print(result['reply'][:600])"""),
    ('md', """## 4. 课程业务 MCP

`course_mcp_server.py` 以 stdio 启动，注册 `list_courses`、`list_enrollments`、`select_course`、`drop_course`。
`course_engine.CourseMCPClient` 通过 `mcp.client.stdio` 建立会话，工具返回的 JSON 直接进入业务回复。"""),
    ('code', """import course_engine as engine

client = await engine.connect_course_mcp()
print('工具列表：')
for tool in await client.list_tools():
    print(' -', tool['name'], '：', tool['description'])

courses = await client.call('list_courses')
print('\\n课程目录：')
for course in courses['courses']:
    print(f"  {course['course_id']} {course['name']} | {course['classroom']} | {course['enrolled']}/{course['capacity']} 剩余 {course['remaining']}")

print('\\n我的选课（S001）：', json.dumps(await client.call('list_enrollments', {'user_id': 'S001'}), ensure_ascii=False)[:200])"""),
    ('md', """## 5. LangGraph 工作流

节点：`route → knowledge / recommend / catalog / mine / plan → confirm（interrupt）→ commit / reject`。
`InMemorySaver` 以 `thread_id=session_id` 保存会话；确认前不写库，确认后由 `commit` 节点调用 MCP 写入。"""),
    ('code', """graph = await engine.get_runtime()
print('节点：', [n for n in graph.get_graph().nodes])
print('\\nMermaid 结构：')
print(graph.get_graph().draw_mermaid()[:1200])"""),
    ('code', """# 选课 → 确认 → 我的选课 → 退课（使用演示用户 S003，结束后清理）
with sqlite3.connect(ROOT / 'data' / 'course_selection.sqlite3') as conn:
    conn.execute("DELETE FROM enrollments WHERE user_id = 'S003'")
    conn.commit()

for message in ('我想选 C001', '确认', '我的选课', '帮我退掉 C001', '确认'):
    out = await engine.chat(message, 'nb-flow', 'S003', use_web=False)
    print(f"\\n> {message}")
    print('  waiting =', out['waiting_for_confirmation'], '| pending =', out['pending_action'])
    print(' ', out['reply'].replace('\\n', '\\n   ')[:400])"""),
    ('md', """## 6. Skill 与多 Agent

- **课程推荐 Skill**（`skills/course_recommendation/SKILL.md`）：触发条件（比较/推荐/方向）、输入、五维比较步骤、输出格式与边界；
- **课程咨询 Agent**：`create_agent` + `search_courses` 工具，负责知识问答与比较；
- **选课业务 Agent**：封装课程 MCP 工具，负责目录、我的选课、选退课预检与事务提交，回复中的数字直接来自数据库。"""),
    ('code', """print(engine.load_recommendation_skill()[:700])
print('\\n选课业务 Agent：', engine.SelectionAgent.__doc__.strip().splitlines()[0])"""),
    ('md', """## 7. 测试结果

- 端到端 15 个场景：`reports/test_results.md`（课程咨询、追问、比较、名额、满员、取消/确认、重复、上限、退课、无答案、Tavily 回退、持久化、我的选课、状态回归）；
- MCP 与并发抢最后名额：`reports/mcp_and_concurrency.md`；
- Prompt 对比：`reports/prompt_compare.md`。"""),
    ('code', """summary = (ROOT / 'reports' / 'test_results.md').read_text(encoding='utf-8').splitlines()
print('\\n'.join(summary[:6]))
print('...')
print('\\n'.join([line for line in summary if line.startswith('- 测试')][:6]))"""),
    ('md', """## 8. 小结

1. RAG 保证课程介绍有依据（资料未提及会明确说明），MCP + SQLite 保证教室、名额与选退课结果真实；
2. LangGraph 的 `interrupt` / `Command(resume)` 让写操作必须经过人工确认，取消时数据库不变；
3. 事务 + 触发器 + 写前复核解决了并发抢最后名额的问题（并发测试：2 人抢 1 个名额，仅 1 人成功）；
4. 不足：会话记忆存在内存中，服务重启后丢失；演示身份未接入真实教务认证；Tavily 密钥未配置时只能回退本地知识。"""),
]


def build() -> None:
    notebook = new_notebook()
    notebook.cells = [
        new_markdown_cell(source) if kind == 'md' else new_code_cell(source) for kind, source in CELLS
    ]
    notebook.metadata['kernelspec'] = {
        'name': 'python3', 'display_name': 'Python 3 (项目虚拟环境)', 'language': 'python',
    }
    notebook.metadata['language_info'] = {'name': 'python'}
    OUT.write_text(nbformat.writes(notebook), encoding='utf-8')

    client = NotebookClient(notebook, timeout=300, kernel_name='python3', resources={'metadata': {'path': str(ROOT)}})
    client.execute()
    nbformat.write(notebook, OUT)
    print('已生成并执行：', OUT)


if __name__ == '__main__':
    build()
