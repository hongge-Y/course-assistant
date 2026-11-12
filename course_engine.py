"""课程选课智能助手后端：Model、Prompt、RAG、Skill、多 Agent、MCP 与 LangGraph 工作流。

整体分工：
- 课程咨询 Agent（LangChain create_agent + search_courses 检索工具）：负责课程知识类问答、比较与推荐。
- 选课业务 Agent（SelectionAgent，课程 MCP 工具的封装）：负责目录/我的选课查询、选退课资格预检和事务提交。
- LangGraph 工作流：route（意图与课程识别）→ knowledge / recommend / catalog / mine /
  plan(确认提示) → confirm(interrupt 等待确认) → commit / reject。
- 会话记忆：InMemorySaver + thread_id=session_id；长期选课记录保存在 SQLite。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import web_search

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'data' / 'course_selection.sqlite3'
KNOWLEDGE_DIR = ROOT / 'knowledge' / 'courses'
SKILL_PATH = ROOT / 'skills' / 'course_recommendation' / 'SKILL.md'
MCP_SERVER_PATH = ROOT / 'course_mcp_server.py'
EMBED_CACHE = ROOT / 'data' / 'embedding_cache.json'

load_dotenv(ROOT / '.env')

CHAT_MODEL = os.getenv('CHAT_MODEL', 'deepseek-flash')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
EMBED_MODEL = os.getenv('EMBED_MODEL', 'text-embedding-v1')
DASHSCOPE_BASE_URL = os.getenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
TEMPERATURE = float(os.getenv('TEMPERATURE', '0.5'))

COURSE_NAMES = {
    'C001': 'Python 数据分析实践',
    'C002': '机器学习入门',
    'C003': '物联网传感器实践',
    'C004': '数字媒体设计基础',
    'C005': '网络基础与安全实验',
}
COURSE_KEYWORDS = {
    '数据分析': 'C001', 'python': 'C001', 'pandas': 'C001', '图表': 'C001', '可视化': 'C001',
    '机器学习': 'C002', '算法': 'C002', '模型': 'C002', 'sklearn': 'C002',
    '物联网': 'C003', '传感器': 'C003', 'arduino': 'C003', '开发板': 'C003',
    '数字媒体': 'C004', '设计': 'C004', '图像': 'C004', '视频': 'C004',
    '网络': 'C005', '安全': 'C005', '抓包': 'C005', 'wireshark': 'C005',
}
CONFIRM_WORDS = {'确认', '确定', '确认选课', '确认退课', '是', '好', '好的', '同意', 'yes', 'y', 'ok'}
CANCEL_WORDS = {'取消', '不', '不要', '算了', '不用了', 'no', 'n'}


class CourseState(TypedDict, total=False):
    """工作流状态。

    user_id/message/use_web：入口写入；
    route/course_id/course_ids/action：route 节点读写；
    reply/waiting/pending_action：plan、commit、reject 及各回答节点写入，chat 读取；
    approved：confirm 节点写入，commit/reject 读取；
    history/last_course_id：各回答节点追加与读取（多轮追问）；
    sources/web_note：knowledge、recommend 节点写入。
    """
    user_id: str
    message: str
    use_web: bool
    route: str
    course_id: str
    course_ids: list[str]
    action: str
    reply: str
    waiting: bool
    pending_action: str | None
    approved: bool
    history: list
    last_course_id: str
    sources: list[str]
    web_note: str


class ChatResult(TypedDict):
    reply: str
    waiting_for_confirmation: bool
    pending_action: str | None


# ============================================================ 1. 模型与提示词
def create_model(temperature: float | None = None) -> ChatOpenAI:
    """加载 .env 并创建对话模型（DeepSeek Flash，temperature=0.5）。"""
    api_key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('缺少 DEEPSEEK_API_KEY，请在 .env 中配置后重启服务。')
    return ChatOpenAI(
        model=CHAT_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        temperature=TEMPERATURE if temperature is None else temperature,
        timeout=90,
        max_retries=2,
    )


PROMPT_V0 = """你是课程助手，回答学生关于课程的问题。"""

PROMPT_V1 = """你是“校园课程助手”，服务对象是准备选课的大学生，回答必须简洁、准确、可执行。

【任务】
1. 回答课程定位、教学目标、先修要求、学时、模块、实验环境、软硬件要求、考核方式等问题。
2. 学生同时问多门课时，按课程分别说明再比较，不要混在一起。
3. 支持追问：结合“最近对话”判断“这门课”“它”指的是哪门课。

【工具规则】
4. 回答任何课程内容前，必须先调用 search_courses 检索本地课程资料；检索词包含课程编号或课程名。
5. 名额、教室、已选人数、选退课结果只能由业务工具/数据库给出；本对话中若没有实时数据，明确说明“请查询课程目录获取实时名额”，不要自己编造数字。
6. 外部联网资料只作背景补充，涉及学校课程安排时以本地课程资料为准。

【数据边界】
7. 资料中没有的内容，直接回答“课程资料中未提及”，不要推测学时、教师、考试形式等细节。
8. 引用时标注来源，格式如：来源：C002.md · 先修要求与适合人群。

【输出方式】
9. 默认 150 字以内，用短段落或 3—5 条要点；学生要求详细介绍或比较时再展开。
10. 不重复学生的原话，不输出思考过程，不使用“作为 AI”等套话。
"""

PROMPTS = {'v0': PROMPT_V0, 'v1': PROMPT_V1}

BUSINESS_NOTE = '（业务数据由课程 MCP 工具直接查询数据库生成，未经模型改写，保证与数据库一致。）'


# ============================================================ 2. RAG 知识库
def _embeddings() -> OpenAIEmbeddings:
    api_key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('缺少 DASHSCOPE_API_KEY，无法调用 text-embedding-v1。')
    return OpenAIEmbeddings(
        model=EMBED_MODEL,
        base_url=DASHSCOPE_BASE_URL,
        api_key=api_key,
        check_embedding_ctx_length=False,  # 通义兼容接口只接受纯文本输入
        chunk_size=10,                     # text-embedding-v1 单次最多 10 条
        timeout=60,
        max_retries=2,
    )


def _course_id_of(path: Path) -> str:
    match = re.search(r'(C00\d)', path.stem, re.IGNORECASE)
    return match.group(1).upper() if match else 'ALL'


def _load_and_split() -> list[Document]:
    """按 Markdown 标题切分，再按长度二次切分，保留课程编号与章节元数据。"""
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[('#', 'title'), ('##', 'section'), ('###', 'subsection')],
        strip_headers=False,
    )
    length_splitter = RecursiveCharacterTextSplitter(
        chunk_size=420, chunk_overlap=60,
        separators=['\n\n', '\n', '。', '；', '，', ' '],
    )
    documents: list[Document] = []
    for path in sorted(KNOWLEDGE_DIR.glob('*.md')):
        text = path.read_text(encoding='utf-8')
        for part in header_splitter.split_text(text):
            section = part.metadata.get('section') or part.metadata.get('title') or '正文'
            for chunk in length_splitter.split_text(part.page_content):
                documents.append(Document(
                    page_content=chunk,
                    metadata={'course_id': _course_id_of(path), 'source': path.name, 'section': section},
                ))
    return documents


class CachedEmbeddings(OpenAIEmbeddings):
    """在通义 text-embedding-v1 之上加一层本地缓存（按文本 sha1 命中）。"""

    cache_file: Path = EMBED_CACHE

    def _load(self) -> dict[str, list[float]]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save(self, cache: dict[str, list[float]]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(cache), encoding='utf-8')

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        cache = self._load()
        keys = [hashlib.sha1(text.encode('utf-8')).hexdigest() for text in texts]
        missing = [(text, key) for text, key in zip(texts, keys) if key not in cache]
        if missing:
            vectors = super().embed_documents([text for text, _ in missing])
            for (_, key), vector in zip(missing, vectors):
                cache[key] = vector
            self._save(cache)
        return [cache[key] for key in keys]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class CourseRetriever:
    """课程知识检索器：向量相似度检索 + 课程编号过滤 + 来源信息。"""

    def __init__(self, documents: list[Document], store: InMemoryVectorStore, embeddings: OpenAIEmbeddings):
        self.documents = documents
        self.store = store
        self.embeddings = embeddings

    def search(self, query: str, k: int = 4, course_id: str | None = None) -> list[Document]:
        course_id = (course_id or '').strip().upper() or None
        if course_id:
            hits = self.store.similarity_search(
                query, k=k, filter=lambda doc: doc.metadata.get('course_id') == course_id,
            )
            overview = [
                doc for doc in self.store.similarity_search(query, k=2)
                if doc.metadata.get('course_id') == 'ALL'
            ]
            merged, seen = [], set()
            for doc in hits + overview:
                key = (doc.metadata.get('source'), doc.page_content[:60])
                if key not in seen:
                    seen.add(key)
                    merged.append(doc)
            return merged[:k + 1]
        return self.store.similarity_search(query, k=k)

    def stats(self) -> dict:
        return {
            'documents': len(self.documents),
            'courses': sorted({doc.metadata['course_id'] for doc in self.documents}),
            'sources': sorted({doc.metadata['source'] for doc in self.documents}),
        }


_RETRIEVER: CourseRetriever | None = None


def build_retriever(force_rebuild: bool = False) -> CourseRetriever:
    """加载课程 Markdown、切分、向量化（text-embedding-v1）、建立内存向量库。"""
    global _RETRIEVER
    if _RETRIEVER is not None and not force_rebuild:
        return _RETRIEVER
    embeddings = CachedEmbeddings(
        model=EMBED_MODEL,
        base_url=DASHSCOPE_BASE_URL,
        api_key=os.getenv('DASHSCOPE_API_KEY', '').strip(),
        check_embedding_ctx_length=False,  # 通义兼容接口只接受纯文本输入
        chunk_size=10,                     # text-embedding-v1 单次最多 10 条
        timeout=60,
        max_retries=2,
    )
    documents = _load_and_split()
    store = InMemoryVectorStore(embedding=embeddings)
    store.add_documents(documents)
    _RETRIEVER = CourseRetriever(documents, store, embeddings)
    return _RETRIEVER


def format_context(documents: list[Document], limit: int = 2400) -> str:
    blocks, used = [], 0
    for doc in documents:
        head = f"[{doc.metadata['source']} · {doc.metadata['section']}]"
        block = f'{head}\n{doc.page_content.strip()}'
        if used + len(block) > limit:
            break
        blocks.append(block)
        used += len(block)
    return '\n\n'.join(blocks)


def format_sources(documents: list[Document]) -> list[str]:
    seen, sources = set(), []
    for doc in documents:
        label = f"{doc.metadata['source']} · {doc.metadata['section']}"
        if label not in seen:
            seen.add(label)
            sources.append(label)
    return sources


def _make_search_tool(retriever: CourseRetriever):
    @tool
    def search_courses(query: str, course_id: str = '') -> str:
        """检索本地课程知识库（课程总览与 C001—C005 课程文档），返回带来源的片段。

        参数：query 检索问题；course_id 可选，例如 C002，限定只检索该课程。
        """
        documents = retriever.search(query, k=4, course_id=course_id)
        if not documents:
            return '知识库中没有检索到相关内容。'
        return format_context(documents, limit=3000)

    return search_courses


# ============================================================ 3. 两个 Agent
_AGENT = None


def build_course_agent(model, retriever):
    """课程咨询 Agent：模型 + 课程检索工具 + 提示词。"""
    return create_agent(
        model,
        tools=[_make_search_tool(retriever)],
        system_prompt=PROMPTS['v1'],
        name='course_consultant',
    )


_SKILL_TEXT: str | None = None


def load_recommendation_skill() -> str:
    """加载课程推荐 Skill（触发条件、执行步骤、输出格式）。"""
    global _SKILL_TEXT
    if _SKILL_TEXT is None:
        _SKILL_TEXT = SKILL_PATH.read_text(encoding='utf-8')
    return _SKILL_TEXT


def build_selection_agent(model, course_tools):
    """选课业务 Agent：课程目录、我的选课、选退课预检与提交。"""
    return SelectionAgent(model, course_tools)


class CourseMCPClient:
    """课程 MCP 客户端：以 stdio 启动 course_mcp_server.py 并保持一个会话。"""

    def __init__(self, server_path: Path = MCP_SERVER_PATH):
        self.server_path = server_path
        self._session: ClientSession | None = None
        self._cm = None
        self._session_cm = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> ClientSession:
        if self._session is not None:
            return self._session
        env = dict(os.environ)
        env.setdefault('PYTHONIOENCODING', 'utf-8')
        params = StdioServerParameters(
            command=sys.executable, args=[str(self.server_path)], env=env, cwd=str(ROOT),
        )
        self._cm = stdio_client(params, errlog=_MCP_ERRLOG)
        read_stream, write_stream = await self._cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        return self._session

    async def close(self) -> None:
        for closer in (self._session_cm, self._cm):
            try:
                if closer is not None:
                    await closer.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = self._session_cm = self._cm = None

    async def list_tools(self) -> list[dict]:
        session = await self._connect()
        response = await asyncio.wait_for(session.list_tools(), timeout=20)
        return [{'name': item.name, 'description': (item.description or '').split('\n')[0]}
                for item in response.tools]

    @staticmethod
    def _decode(result) -> dict:
        structured = getattr(result, 'structuredContent', None) or getattr(result, 'structured_content', None)
        if isinstance(structured, dict):
            payload = structured.get('result') if set(structured) == {'result'} else structured
            if isinstance(payload, dict):
                return payload
        texts = []
        for block in getattr(result, 'content', []) or []:
            text = getattr(block, 'text', None)
            if text:
                texts.append(text)
        raw = '\n'.join(texts).strip()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {'ok': False, 'code': 'MCP_PARSE', 'message': raw or 'MCP 工具没有返回内容。'}

    async def call(self, name: str, arguments: dict | None = None, timeout: float = 30) -> dict:
        async with self._lock:
            for attempt in (1, 2):
                try:
                    session = await self._connect()
                    result = await asyncio.wait_for(
                        session.call_tool(name, arguments or {}), timeout=timeout,
                    )
                    return self._decode(result)
                except Exception as error:
                    if attempt == 2:
                        return {'ok': False, 'code': 'MCP_ERROR', 'message': f'课程服务不可用：{error}'}
                    try:
                        await self.close()
                    except Exception:
                        pass


class SelectionAgent:
    """选课业务 Agent。

    职责：查询目录与我的选课、选退课资格预检、事务提交、按自然语言消歧课程编号。
    设计取舍：查询结果直接格式化为回复，不让模型复述数字，避免名额/教室出现幻觉。
    """

    def __init__(self, model, tools: CourseMCPClient):
        self.model = model
        self.tools = tools

    @staticmethod
    def _line(course: dict, mark: bool = False) -> str:
        prefix = '▶ ' if mark else '  '
        return (
            f"{prefix}{course['course_id']} {course['name']} | 教室：{course['classroom']} | "
            f"已选 {course['enrolled']}/{course['capacity']}，剩余 {course['remaining']}"
        )

    async def catalog(self, highlight: str = '') -> tuple[str, dict]:
        data = await self.tools.call('list_courses')
        if not data.get('ok'):
            return data.get('message', '课程目录查询失败。'), data
        courses = data['courses']
        highlight = (highlight or '').upper()
        lines = ['课程目录（数据库实时数据，每门课上限 20 人）：']
        lines += [self._line(course, mark=(course['course_id'] == highlight)) for course in courses]
        if highlight:
            target = next((c for c in courses if c['course_id'] == highlight), None)
            if target is None:
                lines.append(f'\n没有找到课程 {highlight}，请确认课程编号（C001—C005）。')
            else:
                state = '已满，建议改选其他课程' if target['full'] else f"还有 {target['remaining']} 个名额"
                lines.append(
                    f"\n{target['course_id']} {target['name']}：上课地点 {target['classroom']}，"
                    f"当前 {target['enrolled']}/{target['capacity']}，{state}。"
                )
        lines.append('\n' + BUSINESS_NOTE)
        return '\n'.join(lines), data

    async def mine(self, user_id: str) -> tuple[str, dict]:
        data = await self.tools.call('list_enrollments', {'user_id': user_id})
        if not data.get('ok'):
            return data.get('message', '选课记录查询失败。'), data
        if not data['courses']:
            text = f"{data['user_id']} {data.get('name', '')} 当前没有选课记录（上限 {data['limit']} 门）。"
        else:
            lines = [f"{data['user_id']} {data.get('name', '')} 已选 {data['count']}/{data['limit']} 门："]
            lines += [f"  - {c['course_id']} {c['name']}（{c['classroom']}，{c['selected_at']}）"
                      for c in data['courses']]
            lines.append('\n如需更换课程，可以先退课再选新课，两项操作都需要确认。')
            text = '\n'.join(lines)
        return text + '\n' + BUSINESS_NOTE, data

    async def precheck(self, action: str, user_id: str, course_id: str) -> dict:
        """确认前的资格预检（只读）。"""
        catalog = await self.tools.call('list_courses')
        if not catalog.get('ok'):
            return {'ok': False, 'message': catalog.get('message', '课程目录查询失败。')}
        course = next((c for c in catalog['courses'] if c['course_id'] == course_id), None)
        if course is None:
            return {'ok': False, 'message': f'没有找到课程 {course_id}，课程编号为 C001—C005。'}
        mine = await self.tools.call('list_enrollments', {'user_id': user_id})
        if not mine.get('ok'):
            return {'ok': False, 'message': mine.get('message', '选课记录查询失败。')}
        selected = {item['course_id'] for item in mine['courses']}
        if action == 'select':
            if course_id in selected:
                return {'ok': False, 'message': f'你已经选过 {course_id} {course["name"]}，不能重复选择。'}
            if course['full']:
                others = [c for c in catalog['courses'] if not c['full'] and c['course_id'] != course_id][:2]
                tail = '；有名额的可选：' + '、'.join(f"{c['course_id']} {c['name']}" for c in others) if others else ''
                return {'ok': False,
                        'message': f"{course_id} {course['name']} 已满（{course['enrolled']}/{course['capacity']}），无法选课{tail}。"}
            if mine['count'] >= mine['limit']:
                names = '、'.join(f"{c['course_id']} {c['name']}" for c in mine['courses'])
                return {'ok': False,
                        'message': f"你已选 {mine['count']} 门课程（{names}），达到每人最多 {mine['limit']} 门的上限，请先退课再选新课。"}
        else:
            if course_id not in selected:
                return {'ok': False,
                        'message': f"你没有选修 {course_id} {course['name']}，当前选课记录为：{('、'.join(sorted(selected)) or '空')}。"}
        return {'ok': True, 'course': course, 'count': mine['count'], 'limit': mine['limit']}

    async def commit(self, action: str, user_id: str, course_id: str) -> dict:
        tool_name = 'select_course' if action == 'select' else 'drop_course'
        result = await self.tools.call(tool_name, {'user_id': user_id, 'course_id': course_id})
        if not result.get('ok'):
            message = result.get('message', '操作失败。')
            suggestions = result.get('suggestions') or []
            if suggestions:
                message += '\n有名额的可选课程：' + '、'.join(suggestions[:3])
            result['message'] = message
            return result
        tail = f"\n当前 {result['enrolled']}/{result['capacity']}，剩余 {result['remaining']} 个名额；你已选 {result['mine']} 门。"
        result['message'] = result['message'] + tail + '\n' + BUSINESS_NOTE
        return result

    def resolve_course(self, message: str, options: dict[str, str]) -> str:
        """规则识别失败时，让模型在候选课程中做一次消歧（结构化输出）。"""
        if not options:
            return ''
        try:
            prompt = (
                '学生在选课系统里说了下面这句话，请判断他想操作哪门课程。'
                f"候选课程：{json.dumps(options, ensure_ascii=False)}。"
                '只输出课程编号本身（如 C002）；无法判断时输出 NONE。'
                f'\n学生原话：{message}'
            )
            answer = self.model.invoke(prompt).content
            match = re.search(r'C00[1-5]', str(answer).upper())
            return match.group(0) if match else ''
        except Exception:
            return ''


_TOOLS: CourseMCPClient | None = None
_SELECTION: SelectionAgent | None = None
_MODEL = None
# MCP 子进程的 stderr 输出目标：使用真实文件描述符，避免在 Jupyter 内核中
# 继承 ipykernel 的 OutStream（没有 fileno，会导致子进程无法启动）
_MCP_ERRLOG = open(os.devnull, 'w', encoding='utf-8')


async def connect_course_mcp() -> CourseMCPClient:
    """连接课程 MCP Server（stdio），返回客户端。"""
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = CourseMCPClient()
        await _TOOLS.list_tools()  # 提前建连并确认工具可用
    return _TOOLS


# ============================================================ 4. 工作流节点
def extract_course_ids(text: str) -> list[str]:
    """从文本中识别课程编号与课程关键词，按出现顺序去重。"""
    found: list[str] = []
    for match in re.finditer(r'[Cc]\s*0*([1-5])', text):
        found.append(f'C{int(match.group(1)):03d}')
    lowered = text.lower()
    for keyword, course_id in COURSE_KEYWORDS.items():
        if keyword in lowered:
            found.append(course_id)
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _is_confirm(text: str) -> bool:
    return text.strip().lower().rstrip('。!！') in CONFIRM_WORDS


def _is_cancel(text: str) -> bool:
    return text.strip().lower().rstrip('。!！') in CANCEL_WORDS


def _append_history(state: CourseState, reply: str) -> list[dict]:
    history = list(state.get('history') or [])
    history.append({'role': 'user', 'content': state.get('message', '')})
    history.append({'role': 'assistant', 'content': reply})
    return history[-12:]


async def node_route(state: CourseState) -> dict:
    """意图路由：识别咨询 / 比较推荐 / 目录 / 我的选课 / 选课 / 退课。"""
    text = (state.get('message') or '').strip()
    ids = extract_course_ids(text)
    action = 'none'
    if _is_confirm(text) or _is_cancel(text):
        route = 'clarify'
    elif re.search(r'我的选课|我选了什么|已选课程|我的课程|我报了什么|我选了几门|我选了什么课', text):
        route = 'mine'
    elif re.search(r'退课|退选|退掉|不选了|取消选课', text):
        route, action = 'drop', 'drop'
    elif re.search(r'选课规则|怎么选课|如何选课|选课流程|选课时间|选课要求', text):
        route = 'knowledge'
    elif re.search(r'比较|对比|区别|推荐|适合|哪个好|该选|方向|选哪门', text):
        route = 'recommend'
    elif re.search(r'报名|我想选|我要选|帮我选|直接选|^选|选(择|上)?\s*C00[1-5]|选这门|选那门', text):
        route, action = 'select', 'select'
    elif re.search(r'有哪些课程|课程目录|课程列表|开的课|剩余名额|名额|教室|在哪上|哪里上|上课地点|还有位置', text):
        route = 'catalog'
    else:
        route = 'knowledge'

    course_id = ids[0] if ids else ''
    if not course_id and re.search(r'这门课|这个课|它|该课程|刚才那门', text):
        course_id = state.get('last_course_id', '')
    return {'route': route, 'course_ids': ids, 'course_id': course_id, 'action': action}


async def node_clarify(state: CourseState) -> dict:
    if _is_confirm(state.get('message', '')) or _is_cancel(state.get('message', '')):
        reply = '当前没有待确认的选课或退课操作。你可以先说明想选/想退的课程，例如“我想选 C001”。'
    else:
        reply = '请说明课程编号或课程名称，例如“我想选 C001 Python 数据分析实践”。'
    return {'reply': reply, 'waiting': False, 'pending_action': None,
            'history': _append_history(state, reply)}


async def node_knowledge(state: CourseState) -> dict:
    """课程咨询：咨询 Agent 调用检索工具回答，外部资料按需补充。"""
    retriever = build_retriever()
    query = state.get('message', '')
    hint = state.get('course_id') or state.get('last_course_id', '')
    history = state.get('history') or []

    user_content = query
    if hint:
        user_content += f'\n（当前对话涉及的课程：{hint} {COURSE_NAMES.get(hint, "")}）'
    if history:
        recent = '\n'.join(f"{item['role']}：{item['content'][:160]}" for item in history[-4:])
        user_content += f'\n（最近对话）\n{recent}'

    web_note, web_links = '', []
    if state.get('use_web'):
        web = await web_search.search_tavily(query)
        if web.get('ok'):
            user_content += f"\n\n（外部参考，来自 Tavily，仅作背景补充）\n{web.get('text', '')[:1500]}"
            web_links = web.get('links') or []
        else:
            web_note = web.get('note', '联网补充不可用。')

    try:
        result = await asyncio.wait_for(
            _AGENT.ainvoke(
                {'messages': [('user', user_content)]},
                config={'recursion_limit': 8},
            ),
            timeout=120,
        )
        reply = result['messages'][-1].content
        if isinstance(reply, list):
            reply = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in reply)
    except Exception as error:
        documents = retriever.search(query, k=4, course_id=hint or None)
        fallback = format_context(documents)
        reply = f"（检索到课程资料，但模型回答失败：{type(error).__name__}）\n\n{fallback[:1200]}"

    documents = retriever.search(query, k=4, course_id=hint or None)
    sources = format_sources(documents)
    if web_links:
        sources += [f'外部参考：{link}' for link in web_links[:3]]
    if web_note:
        reply += f'\n\n（{web_note}）'

    reply = str(reply).strip()
    return {
        'reply': reply, 'waiting': False, 'pending_action': None,
        'sources': sources, 'web_note': web_note,
        'last_course_id': hint or state.get('last_course_id', ''),
        'history': _append_history(state, reply),
    }


async def node_recommend(state: CourseState) -> dict:
    """课程比较与推荐：加载推荐 Skill，逐门检索后由模型比较。"""
    retriever = build_retriever()
    catalog = await _TOOLS.call('list_courses')
    courses = catalog.get('courses', []) if catalog.get('ok') else []
    by_id = {course['course_id']: course for course in courses}

    ids = [cid for cid in (state.get('course_ids') or []) if cid in by_id]
    if not ids:
        ids = list(COURSE_NAMES)
    ids = ids[:5]

    skill = load_recommendation_skill()
    blocks, documents = [], []
    for course_id in ids:
        docs = retriever.search(state.get('message', ''), k=2, course_id=course_id)
        documents += docs
        blocks.append(f'== {course_id} {COURSE_NAMES.get(course_id, "")} ==\n{format_context(docs, 900)}')

    live = '\n'.join(
        f"- {cid} {COURSE_NAMES.get(cid, '')}：教室 {by_id[cid]['classroom']}，"
        f"已选 {by_id[cid]['enrolled']}/{by_id[cid]['capacity']}，剩余 {by_id[cid]['remaining']}"
        + ('（已满）' if by_id[cid]['full'] else '')
        for cid in ids if cid in by_id
    ) or '（数据库暂无课程数据）'

    prompt = (
        f'【推荐技能说明】\n{skill}\n\n'
        f'【学生问题】{state.get("message", "")}\n\n'
        f'【数据库实时名额】\n{live}\n\n'
        f'【课程资料片段】\n' + '\n\n'.join(blocks)
    )
    try:
        message = await asyncio.wait_for(lora_invoke(prompt), timeout=120)
        reply = message
    except Exception as error:
        reply = f'（模型调用失败：{type(error).__name__}，以下为检索资料）\n\n' + '\n\n'.join(blocks)

    sources = format_sources(documents)
    return {
        'reply': reply.strip(), 'waiting': False, 'pending_action': None, 'sources': sources,
        'last_course_id': ids[0] if len(ids) == 1 else state.get('last_course_id', ''),
        'history': _append_history(state, reply.strip()),
    }


async def lora_invoke(prompt: str) -> str:
    """直接调用对话模型（比较/推荐等一次成型的回答）。"""
    response = await _MODEL.ainvoke(prompt)
    content = response.content
    if isinstance(content, list):
        content = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return str(content)


async def node_catalog(state: CourseState) -> dict:
    reply, _ = await _SELECTION.catalog(state.get('course_id', ''))
    return {'reply': reply, 'waiting': False, 'pending_action': None,
            'last_course_id': state.get('course_id') or state.get('last_course_id', ''),
            'history': _append_history(state, reply)}


async def node_mine(state: CourseState) -> dict:
    reply, _ = await _SELECTION.mine(state['user_id'])
    return {'reply': reply, 'waiting': False, 'pending_action': None,
            'history': _append_history(state, reply)}


async def node_plan(state: CourseState) -> dict:
    """确认提示节点：先做只读预检，再生成确认话术（尚未写库）。"""
    action = state.get('action') or 'select'
    course_id = state.get('course_id', '')
    if not course_id:
        ids = extract_course_ids(state.get('message', ''))
        course_id = ids[0] if ids else ''
    if not course_id:
        options = dict(COURSE_NAMES)
        course_id = _SELECTION.resolve_course(state.get('message', ''), options)
    if not course_id:
        reply = '请告诉我要操作的课程编号（C001—C005）或课程名称，例如“我想选 C002 机器学习入门”。'
        return {'reply': reply, 'waiting': False, 'pending_action': None,
                'history': _append_history(state, reply)}

    check = await _SELECTION.precheck(action, state['user_id'], course_id)
    if not check.get('ok'):
        return {'reply': check.get('message', '无法完成操作。'), 'waiting': False, 'pending_action': None,
                'history': _append_history(state, check.get('message', ''))}

    course = check['course']
    if action == 'select':
        reply = (
            f"将为你选择：{course_id} {course['name']}\n"
            f"上课教室：{course['classroom']}\n"
            f"当前名额：{course['enrolled']}/{course['capacity']}，剩余 {course['remaining']} 个\n"
            f"你已选 {check['count']}/{check['limit']} 门\n\n"
            '点击“确认选课”按钮或回复“确认”才会写入数据库；回复“取消”不做任何修改。'
        )
    else:
        reply = (
            f"将为你退掉：{course_id} {course['name']}\n"
            f"退课后该课程名额会从 {course['enrolled']}/{course['capacity']} 释放 1 个\n\n"
            '点击“确认退课”按钮或回复“确认”才会删除选课记录；回复“取消”不做任何修改。'
        )
    return {'reply': reply, 'waiting': True, 'pending_action': action, 'course_id': course_id,
            'history': _append_history(state, reply)}


async def node_confirm(state: CourseState) -> dict:
    """人工确认节点：interrupt 暂停工作流，等待用户点击确认/取消后恢复。"""
    decision = interrupt({
        'action': state.get('pending_action'),
        'course_id': state.get('course_id'),
        'detail': state.get('reply', ''),
    })
    return {'approved': _is_confirm(str(decision))}


async def node_commit(state: CourseState) -> dict:
    action = state.get('pending_action') or state.get('action') or 'select'
    result = await _SELECTION.commit(action, state['user_id'], state.get('course_id', ''))
    reply = result.get('message', '操作失败。')
    return {'reply': reply, 'waiting': False, 'pending_action': None, 'approved': False,
            'last_course_id': state.get('course_id', ''),
            'history': _append_history(state, reply)}


async def node_reject(state: CourseState) -> dict:
    action = state.get('pending_action') or state.get('action') or 'select'
    word = '选课' if action == 'select' else '退课'
    if _is_cancel(state.get('message', '')) or not _is_confirm(state.get('message', '')):
        reply = f'已取消本次{word}，数据库没有发生任何修改。'
    else:
        reply = f'未确认本次{word}，数据库没有发生任何修改。'
    return {'reply': reply, 'waiting': False, 'pending_action': None, 'approved': False,
            'history': _append_history(state, reply)}


def _route_after_route(state: CourseState) -> str:
    return {
        'knowledge': 'knowledge', 'recommend': 'recommend', 'catalog': 'catalog',
        'mine': 'mine', 'select': 'plan', 'drop': 'plan', 'clarify': 'clarify',
    }.get(state.get('route', 'knowledge'), 'knowledge')


def _route_after_confirm(state: CourseState) -> str:
    return 'commit' if state.get('approved') else 'reject'


def _route_after_plan(state: CourseState) -> str:
    """只有在需要用户确认（预检通过）时才进入 interrupt 节点。"""
    return 'confirm' if state.get('waiting') else END


# ============================================================ 5. 工作流组装
_SAVER = InMemorySaver()
_GRAPH = None
_INIT_LOCK = asyncio.Lock()


def build_workflow(course_agent, course_tools):
    """定义状态、节点、条件路由与会话记忆，编译 LangGraph 工作流。"""
    graph = StateGraph(CourseState)
    graph.add_node('route', node_route)
    graph.add_node('knowledge', node_knowledge)
    graph.add_node('recommend', node_recommend)
    graph.add_node('catalog', node_catalog)
    graph.add_node('mine', node_mine)
    graph.add_node('plan', node_plan)
    graph.add_node('confirm', node_confirm)
    graph.add_node('commit', node_commit)
    graph.add_node('reject', node_reject)
    graph.add_node('clarify', node_clarify)

    graph.add_edge(START, 'route')
    graph.add_conditional_edges('route', _route_after_route, {
        'knowledge': 'knowledge', 'recommend': 'recommend', 'catalog': 'catalog',
        'mine': 'mine', 'plan': 'plan', 'clarify': 'clarify',
    })
    for leaf in ('knowledge', 'recommend', 'catalog', 'mine', 'clarify', 'commit', 'reject'):
        graph.add_edge(leaf, END)
    graph.add_conditional_edges('plan', _route_after_plan, {'confirm': 'confirm', END: END})
    graph.add_conditional_edges('confirm', _route_after_confirm, {'commit': 'commit', 'reject': 'reject'})
    return graph.compile(checkpointer=_SAVER)


async def get_runtime():
    """懒加载：模型、知识库、咨询 Agent、MCP 客户端、业务 Agent、工作流。"""
    global _AGENT, _MODEL, _GRAPH, _SELECTION
    if _GRAPH is not None:
        return _GRAPH
    async with _INIT_LOCK:
        if _GRAPH is not None:
            return _GRAPH
        _MODEL = create_model()
        retriever = await asyncio.to_thread(build_retriever)
        _AGENT = build_course_agent(_MODEL, retriever)
        course_tools = await connect_course_mcp()
        _SELECTION = build_selection_agent(_MODEL, course_tools)
        _GRAPH = build_workflow(_AGENT, course_tools)
        return _GRAPH


async def warmup() -> dict:
    """启动预热并返回运行时信息（供脚本/Notebook 检查环境）。"""
    await get_runtime()
    tools = await _TOOLS.list_tools()
    return {
        'model': CHAT_MODEL,
        'embedding': EMBED_MODEL,
        'retriever': build_retriever().stats(),
        'mcp_tools': tools,
        'nodes': list(_GRAPH.get_graph().nodes.keys()),
    }


async def shutdown() -> None:
    """关闭 MCP 会话（服务退出时调用，避免子进程残留）。"""
    global _TOOLS, _GRAPH, _AGENT, _SELECTION
    if _TOOLS is not None:
        await _TOOLS.close()
    _TOOLS = None
    _GRAPH = None
    _AGENT = None
    _SELECTION = None


# ============================================================ 6. 对话入口
async def chat(message: str, session_id: str, user_id: str, use_web: bool = False) -> ChatResult:
    """运行工作流并返回网页需要的三个字段。

    - 无待确认操作：以新输入运行工作流；
    - 有待确认操作（上一次 interrupt 暂停中）：把“确认/取消”作为 Command(resume=...) 恢复工作流；
    - 只有 commit 节点会调用选课/退课 MCP 工具，写入结果来自数据库。
    """
    graph = await get_runtime()
    config = {'configurable': {'thread_id': session_id}, 'recursion_limit': 25}
    state = await graph.aget_state(config)

    try:
        if state.next:
            decision = '确认' if _is_confirm(message) else ('取消' if _is_cancel(message) else message)
            result = await graph.ainvoke(Command(resume=decision), config)
        else:
            result = await graph.ainvoke({
                'message': message, 'user_id': user_id, 'use_web': bool(use_web),
                'route': '', 'course_id': '', 'course_ids': [], 'action': 'none',
                'reply': '', 'waiting': False, 'pending_action': None, 'approved': False,
                'sources': [], 'web_note': '',
            }, config)
    except Exception:
        (ROOT / 'data' / 'agent_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        result = {'reply': '抱歉，处理这次请求时出现异常，已记录到 data/agent_error.log。请重试或换一种说法。',
                  'waiting': False, 'pending_action': None}

    reply = str(result.get('reply') or '（没有生成回复，请重试。）').strip()
    waiting = bool(result.get('waiting'))
    pending = result.get('pending_action') if waiting else None
    return {'reply': reply, 'waiting_for_confirmation': waiting, 'pending_action': pending or None}
