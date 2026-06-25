import copy
import json
import os


class Rules:
    def __init__(self, core):
        self.core = core
        self.root = self._find_project_root()
        self.rules_dir = os.path.join(self.root, "resources", "rules")
        self._cache = {}

    def _find_project_root(self):
        here = os.path.abspath(__file__)
        root = here
        for _ in range(8):
            root = os.path.dirname(root)
            if os.path.isdir(os.path.join(root, "resources", "rules")):
                return root
        return os.getcwd()

    def _read(self, name):
        if not name.endswith(".json"):
            name = f"{name}.json"
        path = os.path.join(self.rules_dir, name)
        if path not in self._cache:
            with open(path, "r", encoding="utf-8") as f:
                self._cache[path] = json.load(f)
        return copy.deepcopy(self._cache[path])

    def list(self):
        rows = []
        if not os.path.isdir(self.rules_dir):
            return rows
        for filename in sorted(os.listdir(self.rules_dir)):
            if filename.endswith(".json"):
                path = os.path.join(self.rules_dir, filename)
                rows.append(dict(
                    name=filename[:-5],
                    filename=filename,
                    path=os.path.relpath(path, self.root),
                    size=os.path.getsize(path),
                ))
        return rows

    def get(self, name):
        return self._read(name)

    @property
    def exam_scope(self):
        return self._read("exam_scope")

    @property
    def generation_policy(self):
        return self._read("generation_policy")

    @property
    def validation_checklist(self):
        return self._read("validation_checklist")

    @property
    def validation_harness_spec(self):
        return self._read("validation_harness_spec")

    @property
    def validation_agents(self):
        return self._read("validation_agents")

    @property
    def copyright_policy(self):
        return self._read("copyright_policy")

    @property
    def quality_requirements(self):
        return self._read("quality_requirements")

    @property
    def question_language_rulebook(self):
        return self._read("question_language_rulebook")

    @property
    def item_design_rulebook(self):
        return self._read("item_design_rulebook")

    @property
    def scope_generation_strategy(self):
        return self._read("scope_generation_strategy")

    @property
    def difficulty_rubric(self):
        return self._read("difficulty_rubric")

    @property
    def question_type_rules(self):
        return self._read("question_type_rules")


Model = Rules
