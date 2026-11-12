"""调试：检查每个回合后工作流的 next（是否有待恢复的 interrupt）。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import course_engine as engine


async def main() -> None:
    graph = await engine.get_runtime()
    config = {'configurable': {'thread_id': 'debug-flow'}}
    for message in ('我想选 C004', '我想选 C001', '确认'):
        result = await engine.chat(message, 'debug-flow', 'S003', use_web=False)
        state = await graph.aget_state(config)
        print(f'\n> {message}')
        print('  reply:', result['reply'][:120].replace('\n', ' '))
        print('  waiting:', result['waiting_for_confirmation'], 'pending:', result['pending_action'])
        print('  state.next:', state.next, '| route:', state.values.get('route'),
              '| waiting:', state.values.get('waiting'))
    await engine.shutdown()


asyncio.run(main())
