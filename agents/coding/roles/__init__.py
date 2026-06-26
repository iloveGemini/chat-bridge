from .planner import SYSTEM_PROMPT as PLANNER_PROMPT
from .searcher import SYSTEM_PROMPT as SEARCHER_PROMPT
from .coder import SYSTEM_PROMPT as CODER_PROMPT
from .writer import SYSTEM_PROMPT as WRITER_PROMPT
from .developer import SYSTEM_PROMPT as DEVELOPER_PROMPT
from .checker import SYSTEM_PROMPT as CHECKER_PROMPT

ROLE_PROMPTS = {
    "planner": PLANNER_PROMPT,
    "searcher": SEARCHER_PROMPT,
    # coder/writer 已合并为 developer，保留 prompt 仅为兼容旧引用
    "coder": CODER_PROMPT,
    "writer": WRITER_PROMPT,
    "developer": DEVELOPER_PROMPT,
    "checker": CHECKER_PROMPT
}
