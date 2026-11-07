"""Contracts: model-exposed tools are real, unique and safely typed."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from purah_agent.tool_specs import AGENT_TOOLS


class ToolSpecTests(unittest.TestCase):
    def test_all_advertised_tools_have_dispatchers(self):
        source = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        dispatcher = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                          and n.name == "execute_agent_tool")
        branches = set()
        for node in ast.walk(dispatcher):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name) or node.left.id != "name":
                continue
            if isinstance(node.ops[0], ast.Eq) and len(node.comparators) == 1:
                value = node.comparators[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    branches.add(value.value)
        exposed = {item["function"]["name"] for item in AGENT_TOOLS}
        self.assertEqual(len(exposed), len(AGENT_TOOLS), "Duplicate tool names")
        self.assertEqual(exposed, branches, "Tool schema/dispatcher drift")

    def test_tool_argument_contracts_are_complete(self):
        for item in AGENT_TOOLS:
            self.assertEqual(item["type"], "function")
            obj = item["function"]
            self.assertTrue(obj["description"], obj["name"])
            parameters = obj["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])
            self.assertTrue(set(parameters["required"]) <= set(parameters["properties"]))


if __name__ == "__main__":
    unittest.main()
