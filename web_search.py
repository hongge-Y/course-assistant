"""Tavily 搜索 MCP 接入：仅用于补充外部学习资料。

实现方式：优先通过 Streamable HTTP 连接 Tavily 官方 MCP 服务
(https://mcp.tavily.com/mcp/?tavilyApiKey=...)，把搜索工具暴露给业务代码；
MCP 不可用（未配置密钥 / 超时 / 服务异常）时退回 Tavily REST 接口，
两者都失败则返回 ok=False，由工作流提示“联网补充不可用”并继续使用本地知识库。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

TAVILY_MCP_URL = 'https://mcp.tavily.com/mcp/'
TAVILY_API_URL = 'https://api.tavily.com/search'
TIMEOUT = 20.0


def _api_key() -> str:
    return (os.getenv('TAVILY_API_KEY') or '').strip()


def _parse_results(payload: dict, max_results: int = 5) -> dict:
    """把 Tavily 的 results 转成统一的 text + links。"""
    results = payload.get('results') or []
    lines, links = [], []
    for item in results[:max_results]:
        title = (item.get('title') or '').strip()
        url = (item.get('url') or '').strip()
        content = (item.get('content') or '').strip().replace('\n', ' ')
        if not url:
            continue
        links.append(url)
        lines.append(f'- {title}（{url}）\n  {content[:220]}')
    text = '\n'.join(lines)
    answer = (payload.get('answer') or '').strip()
    if answer:
        text = f'{answer}\n{text}'.strip()
    return {'text': text, 'links': links}


async def _search_via_mcp(query: str, key: str, max_results: int) -> dict:
    """通过 Tavily MCP（Streamable HTTP）调用搜索工具。"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({
        'tavily': {
            'url': f'{TAVILY_MCP_URL}?tavilyApiKey={key}',
            'transport': 'streamable_http',
        },
    })
    tools = await asyncio.wait_for(client.get_tools(), timeout=TIMEOUT)
    if not tools:
        raise RuntimeError('Tavily MCP 未返回任何工具')
    search_tool = next((t for t in tools if 'search' in t.name.lower()), tools[0])
    raw = await asyncio.wait_for(
        search_tool.ainvoke({'query': query, 'max_results': max_results}), timeout=TIMEOUT,
    )
    if isinstance(raw, list):
        raw = '\n'.join(str(item.get('text', item)) if isinstance(item, dict) else str(item) for item in raw)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {'text': raw.strip()[:2000], 'links': []}
        if isinstance(data, list):
            return _parse_results({'results': data}, max_results)
        if isinstance(data, dict):
            return _parse_results(data, max_results)
    return {'text': str(raw)[:2000], 'links': []}


async def _search_via_rest(query: str, key: str, max_results: int) -> dict:
    """MCP 不可用时的 REST 回退。"""
    payload = {
        'api_key': key, 'query': query, 'max_results': max_results,
        'search_depth': 'basic', 'include_answer': True,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(TAVILY_API_URL, json=payload)
        response.raise_for_status()
        return _parse_results(response.json(), max_results)


async def search_tavily(query: str, max_results: int = 5) -> dict:
    """检索外部资料。

    返回 {'ok': bool, 'text': str, 'links': [...], 'channel': 'mcp'|'rest'|'none', 'note': str}
    """
    key = _api_key()
    if not key:
        return {
            'ok': False, 'channel': 'none', 'text': '', 'links': [],
            'note': '未配置 TAVILY_API_KEY，联网补充不可用，已改用本地课程知识库回答。',
        }

    errors = []
    for name, func in (('mcp', _search_via_mcp), ('rest', _search_via_rest)):
        try:
            result = await func(query, key, max_results)
            if result.get('text'):
                result.update({'ok': True, 'channel': name, 'note': ''})
                return result
            errors.append(f'{name}: 搜索结果为空')
        except Exception as error:  # 超时、网络、鉴权等
            errors.append(f'{name}: {type(error).__name__}: {error}')
    return {
        'ok': False, 'channel': 'none', 'text': '', 'links': [],
        'note': '联网检索暂时不可用（' + '；'.join(errors)[:220] + '），以下回答仅依据本地课程资料。',
    }
