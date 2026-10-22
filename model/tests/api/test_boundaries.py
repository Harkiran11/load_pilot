"""Enforce inference's dependency boundary against operational/backend modules."""

import ast
import unittest

from loadpilot_model.paths import PROJECT_ROOT


class InferenceDependencyTests(unittest.TestCase):
    def test_model_and_api_modules_do_not_import_dispatch_or_submission_dependencies(self):
        forbidden = {"service", "controller", "schema", "sqlalchemy", "httpx", "requests"}
        for domain in ("api", "decision", "inference", "features", "models", "baselines"):
            for path in (PROJECT_ROOT / "loadpilot_model" / domain).glob("*.py"):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        modules = [node.module]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(path=path.name, module=module):
                            self.assertNotIn(module.split(".")[0], forbidden)
