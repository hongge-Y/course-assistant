"""生成实验报告用的系统架构图与业务流程图（matplotlib 绘制，无需外部服务）。"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'reports' / 'figures'
OUT.mkdir(parents=True, exist_ok=True)

for candidate in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf'):
    if Path(candidate).exists():
        font_manager.fontManager.addfont(candidate)
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=candidate).get_name()
        break
plt.rcParams['axes.unicode_minus'] = False

INK = '#18312e'
ACCENT = '#196c5a'
PALE = '#eef6f2'
LINE = '#7aa79a'


def box(ax, x, y, w, h, title, lines=(), color=PALE, edge=ACCENT, title_size=10.5,
        body_size=8.2, gap=0.030):
    ax.add_patch(patches.FancyBboxPatch(
        (x, y), w, h, boxstyle='round,pad=0.008,rounding_size=0.014',
        facecolor=color, edgecolor=edge, linewidth=1.1))
    ax.text(x + w / 2, y + h - 0.022, title, ha='center', va='top',
            fontsize=title_size, color=INK, fontweight='bold')
    for index, line in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.055 - index * gap, line, ha='center', va='top',
                fontsize=body_size, color='#33524c')


def arrow(ax, start, end, text='', rad=0.0, dx=0.0, dy=0.012, size=8):
    ax.annotate('', xy=end, xytext=start,
                arrowprops=dict(arrowstyle='-|>', color=LINE, lw=1.2,
                                connectionstyle=f'arc3,rad={rad}'))
    if text:
        ax.text((start[0] + end[0]) / 2 + dx, (start[1] + end[1]) / 2 + dy, text,
                ha='center', va='bottom', fontsize=size, color=ACCENT,
                bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none'))


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(10.4, 7.4), dpi=200)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    ax.text(0.5, 0.985, '课程咨询与选课智能助手 · 系统架构', ha='center', fontsize=14.5,
            color=INK, fontweight='bold')

    box(ax, 0.06, 0.855, 0.88, 0.105, '① 用户界面层：web/index.html + FastAPI',
        ('学生身份选择 · 对话输入 · 确认/取消按钮 · Tavily 联网开关',
         'POST /api/sessions（建会话）   POST /api/chat（对话与确认）'),
        color='#f4f8f6', gap=0.033)

    box(ax, 0.06, 0.640, 0.42, 0.180, '② LangGraph 工作流',
        ('route：意图 + 课程编号识别',
         'knowledge / recommend / catalog / mine',
         'plan → confirm（interrupt 暂停）',
         'commit / reject；thread_id 记忆'), gap=0.034)

    box(ax, 0.52, 0.640, 0.42, 0.180, '③ 两个 Agent 分工',
        ('咨询 Agent：create_agent + 检索工具',
         '业务 Agent：课程 MCP + 事务校验',
         '推荐 Skill：触发条件与比较维度',
         '联网补充：Tavily 失败自动回退'), gap=0.034)

    box(ax, 0.06, 0.435, 0.28, 0.150, '④ 对话模型',
        ('DeepSeek Flash', 'temperature = 0.5', '工具调用 + 中文回答'), gap=0.036)
    box(ax, 0.36, 0.435, 0.28, 0.150, '⑤ RAG 知识库',
        ('标题切分 45 个片段', 'text-embedding-v1 向量', 'InMemoryVectorStore'), gap=0.036)
    box(ax, 0.66, 0.435, 0.28, 0.150, '⑥ 课程 MCP 服务',
        ('stdio 独立进程', 'list_courses / list_enrollments', 'select_course / drop_course'), gap=0.036)

    box(ax, 0.06, 0.215, 0.42, 0.150, '⑦ 课程知识库（knowledge/courses）',
        ('课程目录.md + C001—C005.md', '定位 / 先修 / 模块 / 环境 / 考核'), color='#f7f9f8', gap=0.038)
    box(ax, 0.52, 0.215, 0.42, 0.150, '⑧ SQLite 业务数据库（data/）',
        ('users / courses / enrollments', '外键 + 唯一约束 + 人数触发器 + 事务'), color='#f7f9f8', gap=0.038)

    ax.text(0.5, 0.115, '数据来源边界：课程内容来自⑦，教室 / 名额 / 选退课结果来自⑧；'
                        '写操作只发生在 commit 节点，确认后由⑥在事务内再次校验。',
            ha='center', fontsize=8.6, color=ACCENT,
            bbox=dict(boxstyle='round,pad=0.45', fc='#f4f8f6', ec=LINE, lw=0.8))
    ax.text(0.5, 0.045, '会话记忆：InMemorySaver（服务重启后清空）· 选课记录长期保存在 SQLite · 演示身份，未接入真实教务认证',
            ha='center', fontsize=8.2, color='#6b837c')

    arrow(ax, (0.5, 0.855), (0.5, 0.820), 'HTTP / JSON')
    arrow(ax, (0.40, 0.640), (0.20, 0.585))
    arrow(ax, (0.60, 0.640), (0.50, 0.585))
    arrow(ax, (0.70, 0.640), (0.80, 0.585), '')
    arrow(ax, (0.20, 0.435), (0.27, 0.365))
    arrow(ax, (0.50, 0.435), (0.28, 0.365))
    arrow(ax, (0.80, 0.435), (0.73, 0.365))

    fig.savefig(OUT / 'architecture.png', bbox_inches='tight', facecolor='white')
    plt.close(fig)


def workflow() -> None:
    fig, ax = plt.subplots(figsize=(10.4, 6.2), dpi=200)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    ax.text(0.5, 0.985, 'LangGraph 工作流：咨询 / 查询 / 选退课人工确认', ha='center', fontsize=14,
            color=INK, fontweight='bold')

    box(ax, 0.40, 0.83, 0.20, 0.105, 'route', ('意图 + 课程编号', '（规则与上下文）'), title_size=10, gap=0.033)
    box(ax, 0.04, 0.60, 0.21, 0.130, 'knowledge', ('咨询 Agent + RAG', '外部资料按需补充'), title_size=9.6, gap=0.033)
    box(ax, 0.28, 0.60, 0.21, 0.130, 'recommend', ('推荐 Skill + 逐门检索', '给出比较与建议'), title_size=9.6, gap=0.033)
    box(ax, 0.52, 0.60, 0.21, 0.130, 'catalog / mine', ('课程 MCP 查询', '名额 / 教室 / 已选'), title_size=9.6, gap=0.033)
    box(ax, 0.76, 0.60, 0.20, 0.130, 'clarify', ('信息不足', '提示补充编号'), title_size=9.6, gap=0.033)
    box(ax, 0.52, 0.385, 0.21, 0.130, 'plan', ('只读预检 + 确认提示', 'waiting = true'), title_size=9.6, gap=0.033)
    box(ax, 0.52, 0.170, 0.21, 0.130, 'confirm（interrupt）', ('暂停，等待确认 / 取消', 'Command(resume)'), title_size=8.8, gap=0.033)
    box(ax, 0.22, 0.170, 0.21, 0.130, 'commit', ('调用选课 / 退课工具', '事务内再次校验'), title_size=9.6, gap=0.033)
    box(ax, 0.02, 0.170, 0.16, 0.130, 'reject', ('取消：不写库', '返回提示'), title_size=9.6, gap=0.033)

    arrow(ax, (0.44, 0.83), (0.14, 0.735), '课程咨询')
    arrow(ax, (0.47, 0.83), (0.38, 0.735), '比较 / 推荐')
    arrow(ax, (0.53, 0.83), (0.62, 0.735), '目录 / 我的')
    arrow(ax, (0.56, 0.83), (0.86, 0.735), '不明确')
    arrow(ax, (0.625, 0.60), (0.625, 0.520), '选课 / 退课')
    arrow(ax, (0.625, 0.385), (0.625, 0.305))
    arrow(ax, (0.52, 0.235), (0.435, 0.235), '确认')
    arrow(ax, (0.52, 0.205), (0.435, 0.205), '取消')

    ax.text(0.5, 0.075, '各回答节点写回 reply / waiting / pending_action，并追加 history 与 last_course_id，'
                        '使“这门课”等追问可关联上一轮课程。',
            ha='center', fontsize=8.6, color=ACCENT,
            bbox=dict(boxstyle='round,pad=0.45', fc='#f4f8f6', ec=LINE, lw=0.8))
    fig.savefig(OUT / 'workflow.png', bbox_inches='tight', facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    architecture()
    workflow()
    print('已生成：', OUT / 'architecture.png', OUT / 'workflow.png')
