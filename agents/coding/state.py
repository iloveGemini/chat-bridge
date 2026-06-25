import json
from pathlib import Path


class CodingState:
    """
    Coding Agent 的状态机数据结构。
    取代了原先物理的 work_log.md，将任务状态、Todo 列表、当前阶段持久化为 JSON，
    方便前端直接读取并渲染进度条和 Checkbox。
    """

    def __init__(self, workspace_dir):
        self.filepath = Path(workspace_dir) / "state.json"
        self.data = {
            "phase": "plan",
            "todos": [],
            "current_todo_index": 0,
            "context_files": [],
            "plan_text": "",
            "search_text": "",
            "diffs_text": "",
            "last_error": "",
            "cycle": 0,
            "status": "idle",
        }
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                self.data.update(json.loads(self.filepath.read_text(encoding="utf-8")))
            except Exception:
                pass

    def save(self):
        self.filepath.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def update_todos(self, todos):
        self.data["todos"] = todos
        self.save()

    def mark_todo_done(self, index):
        if 0 <= index < len(self.data["todos"]):
            self.data["todos"][index]["done"] = True
            self.save()

    def set_phase(self, phase):
        self.data["phase"] = phase
        self.save()

    def get_todos(self):
        return self.data.get("todos", [])

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **kwargs):
        self.data.update(kwargs)
        self.save()

    def get(self, key, default=None):
        return self.data.get(key, default)
