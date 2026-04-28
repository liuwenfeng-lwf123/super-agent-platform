import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import tools as tools_module


class TestAgentTools(unittest.TestCase):
    def test_extract_validation_result(self):
        passed = tools_module.extract_validation_result("File written\nValidation: Python syntax OK")
        failed = tools_module.extract_validation_result("Validation failed: JSON parse error at line 1")
        skipped = tools_module.extract_validation_result("Validation skipped: TypeScript compiler not available")
        latest = tools_module.extract_validation_result("Validation: Python syntax OK\nValidation skipped: no Python test cases found in Python test file")
        with_strategy = tools_module.extract_validation_result("Validation: Python tests OK (pytest)")
        failed_with_strategy = tools_module.extract_validation_result("Validation failed: TypeScript project check failed (tsc): missing config")
        missing = tools_module.extract_validation_result("File written only")

        self.assertEqual(passed, {"status": "passed", "message": "Validation: Python syntax OK"})
        self.assertEqual(failed, {"status": "failed", "message": "Validation failed: JSON parse error at line 1"})
        self.assertEqual(skipped, {"status": "skipped", "message": "Validation skipped: TypeScript compiler not available"})
        self.assertEqual(latest, {"status": "skipped", "message": "Validation skipped: no Python test cases found in Python test file"})
        self.assertEqual(with_strategy, {"status": "passed", "message": "Validation: Python tests OK (pytest)", "strategy": "pytest"})
        self.assertEqual(failed_with_strategy, {"status": "failed", "message": "Validation failed: TypeScript project check failed (tsc): missing config", "strategy": "tsc"})
        self.assertIsNone(missing)

    def test_format_validation_command_result_includes_strategy(self):
        passed = tools_module._format_validation_command_result(
            "Python tests",
            {"success": True, "output": "__VALIDATION_STRATEGY__:pytest\n", "error": "", "exit_code": 0},
        )
        failed = tools_module._format_validation_command_result(
            "TypeScript project",
            {
                "success": False,
                "output": "__VALIDATION_STRATEGY__:tsc\nsrc/app/page.tsx(3,1): error TS1005: ';' expected",
                "error": "",
                "exit_code": 2,
            },
        )

        self.assertEqual(passed, "Validation: Python tests OK (pytest)")
        self.assertIn("Validation failed: TypeScript project check failed (tsc):", failed)
        self.assertNotIn("__VALIDATION_STRATEGY__", failed)

    def test_related_python_test_candidates_include_common_patterns(self):
        candidates = tools_module._related_python_test_candidates("app/demo.py")

        self.assertEqual(
            candidates,
            [
                "tests/test_demo.py",
                "tests/demo_test.py",
                "test_demo.py",
                "demo_test.py",
                "app/test_demo.py",
                "app/demo_test.py",
            ],
        )

    def test_related_node_test_candidates_include_common_patterns(self):
        candidates = tools_module._related_node_test_candidates("frontend/src/lib/math.ts")

        self.assertEqual(
            candidates[:8],
            [
                "frontend/src/lib/math.test.ts",
                "frontend/src/lib/math.spec.ts",
                "frontend/src/lib/math.test.tsx",
                "frontend/src/lib/math.spec.tsx",
                "frontend/src/lib/math.test.js",
                "frontend/src/lib/math.spec.js",
                "frontend/src/lib/math.test.jsx",
                "frontend/src/lib/math.spec.jsx",
            ],
        )
        self.assertIn("frontend/src/lib/__tests__/math.test.ts", candidates)
        self.assertIn("frontend/tests/lib/math.test.ts", candidates)

    def test_related_node_test_candidates_return_test_file_itself(self):
        candidates = tools_module._related_node_test_candidates("frontend/src/lib/math.test.ts")

        self.assertEqual(candidates, ["frontend/src/lib/math.test.ts"])

    def test_python_repo_test_command_prefers_pytest_with_unittest_fallback(self):
        command = tools_module._python_repo_test_command("tests/test_demo.py")

        self.assertIn('pytest.ini', command)
        self.assertIn('conftest.py', command)
        self.assertIn('python3 -m pytest -q "$TARGET_PATH"', command)
        self.assertIn('unittest.TextTestRunner', command)

    def test_node_related_test_validation_command_targets_related_test_file(self):
        command = tools_module._node_related_test_validation_command("frontend/src/lib/math.ts")

        self.assertIn("frontend/src/lib/math.test.ts", command)
        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'test', 'test:ci'):", command)
        self.assertIn("npm run \"$SCRIPT_NAME\" -- \"$TEST_TARGET\"", command)
        self.assertIn("pnpm run \"$SCRIPT_NAME\" -- \"$TEST_TARGET\"", command)
        self.assertIn("yarn \"$SCRIPT_NAME\" \"$TEST_TARGET\"", command)

    def test_javascript_repo_validation_command_prefers_unit_test_strategies_before_build(self):
        command = tools_module._javascript_repo_validation_command("frontend/src/app/page.jsx")

        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci', 'build'):", command)
        self.assertIn("elif lowered_name in {'test', 'test:ci'}:", command)
        self.assertLess(command.index("'test:unit'"), command.index("'typecheck'"))
        self.assertLess(command.index("'vitest'"), command.index("'build'"))

    def test_typescript_repo_validation_command_prefers_typecheck_before_tsc(self):
        command = tools_module._typescript_repo_validation_command("frontend/src/app/page.tsx")

        self.assertIn("for name in ('typecheck',):", command)
        self.assertIn("--noEmit --pretty false --skipLibCheck", command)
        self.assertLess(command.index("for name in ('typecheck',):"), command.index("--noEmit --pretty false --skipLibCheck"))

    def test_typescript_source_validation_command_prefers_unit_test_strategies_before_tsc(self):
        command = tools_module._typescript_source_validation_command("frontend/src/app/page.tsx")

        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci'):", command)
        self.assertIn("--noEmit --pretty false --skipLibCheck", command)
        self.assertLess(command.index("'vitest'"), command.index("'typecheck'"))
        self.assertLess(command.index("'typecheck'"), command.index("--noEmit --pretty false --skipLibCheck"))

    def test_probable_node_source_path_detection(self):
        self.assertTrue(tools_module._is_probable_node_source_path("frontend/src/app/page.js"))
        self.assertTrue(tools_module._is_probable_node_source_path("components/Button.jsx"))
        self.assertFalse(tools_module._is_probable_node_source_path("scripts/build.js"))

    def test_write_file_returns_python_validation_success(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 12, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": False, "output": "", "error": "", "exit_code": 124}),
            ) as execute_bash:
                result = asyncio.run(tools_module.write_file.ainvoke({"path": "demo.py", "content": "print('ok')\n"}))

        self.assertIn("File written: demo.py", result)
        self.assertIn("Validation: Python syntax OK", result)
        self.assertIn("Validation skipped: no matching Python test file found", result)
        execute_bash.assert_awaited_once()

    def test_write_file_returns_python_validation_failure(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 12, "error": ""})):
            with patch.object(tools_module.runtime_manager, "execute_bash", AsyncMock()) as execute_bash:
                result = asyncio.run(tools_module.write_file.ainvoke({"path": "demo.py", "content": "def broken(:\n"}))

        self.assertIn("File written: demo.py", result)
        self.assertIn("Validation failed: Python syntax error", result)
        execute_bash.assert_not_awaited()

    def test_write_file_returns_json_validation_failure(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 10, "error": ""})):
            result = asyncio.run(tools_module.write_file.ainvoke({"path": "demo.json", "content": '{"x": }'}))

        self.assertIn("File written: demo.json", result)
        self.assertIn("Validation failed: JSON parse error", result)

    def test_write_file_returns_toml_validation_failure(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 18, "error": ""})):
            result = asyncio.run(tools_module.write_file.ainvoke({"path": "pyproject.toml", "content": "[project\nname='demo'"}))

        self.assertIn("File written: pyproject.toml", result)
        self.assertIn("Validation failed: TOML parse error", result)

    def test_write_file_runs_javascript_validation_command(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 20, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(tools_module.write_file.ainvoke({"path": "demo.js", "content": "console.log('ok')\n"}))

        self.assertIn("Validation: JavaScript syntax OK", result)
        execute_bash.assert_awaited_once()
        self.assertIn("node --check demo.js", execute_bash.await_args.args[0])

    def test_write_file_runs_javascript_repo_validation_for_app_js_source(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 56, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:vitest\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/src/app/page.js",
                        "content": "export default function Page() { return <div />; }\n",
                    })
                )

        self.assertIn("Validation: JavaScript project OK (vitest)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("frontend/src/app/page.test.js", execute_bash.await_args.args[0])
        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci', 'build'):", execute_bash.await_args.args[0])
        self.assertNotIn("node --check", execute_bash.await_args.args[0])

    def test_write_file_runs_javascript_repo_validation_for_jsx_source(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 58, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:jest\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/components/Button.jsx",
                        "content": "export function Button() { return <button />; }\n",
                    })
                )

        self.assertIn("Validation: JavaScript project OK (jest)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("frontend/components/Button.test.jsx", execute_bash.await_args.args[0])
        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci', 'build'):", execute_bash.await_args.args[0])
        self.assertNotIn("--noEmit", execute_bash.await_args.args[0])

    def test_write_file_runs_node_project_script_validation_for_package_json(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 80, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:build\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/package.json",
                        "content": '{"name":"demo","scripts":{"build":"next build"}}',
                    })
                )

        self.assertIn("Validation: JSON parse OK", result)
        self.assertIn("Validation: Node project script OK (build)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("package.json", execute_bash.await_args.args[0])
        self.assertIn("npm run \"$SCRIPT_NAME\"", execute_bash.await_args.args[0])

    def test_write_file_runs_shell_validation_command(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 18, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(tools_module.write_file.ainvoke({"path": "script.sh", "content": "echo hello\n"}))

        self.assertIn("Validation: Bash syntax OK", result)
        execute_bash.assert_awaited_once()
        self.assertIn("bash -n script.sh", execute_bash.await_args.args[0])

    def test_write_file_runs_typescript_project_validation_for_tsconfig(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 36, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:tsc\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/tsconfig.json",
                        "content": '{"compilerOptions":{"target":"ES2022"}}',
                    })
                )

        self.assertIn("Validation: JSON parse OK", result)
        self.assertIn("Validation: TypeScript project OK (tsc)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("tsconfig.json", execute_bash.await_args.args[0])
        self.assertIn("for name in ('typecheck',):", execute_bash.await_args.args[0])
        self.assertIn("--noEmit", execute_bash.await_args.args[0])

    def test_write_file_runs_typescript_repo_validation_for_tsx_source(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 52, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:vitest\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/src/app/page.tsx",
                        "content": "export default function Page() { return <div />; }\n",
                    })
                )

        self.assertIn("Validation: TypeScript project OK (vitest)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("frontend/src/app/page.test.tsx", execute_bash.await_args.args[0])
        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci'):", execute_bash.await_args.args[0])
        self.assertIn("--noEmit", execute_bash.await_args.args[0])

    def test_write_file_runs_typescript_repo_validation_for_ts_source(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 38, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:jest\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({
                        "path": "frontend/src/lib/math.ts",
                        "content": "export const sum = (a: number, b: number) => a + b;\n",
                    })
                )

        self.assertIn("Validation: TypeScript project OK (jest)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("frontend/src/lib/math.test.ts", execute_bash.await_args.args[0])
        self.assertIn("for name in ('test:unit', 'test:unit:ci', 'vitest', 'jest', 'typecheck', 'test', 'test:ci'):", execute_bash.await_args.args[0])
        self.assertIn("--noEmit", execute_bash.await_args.args[0])

    def test_write_file_skips_typescript_validation_without_tsconfig(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 24, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": False, "output": "", "error": "", "exit_code": 126}),
            ) as execute_bash:
                result = asyncio.run(tools_module.write_file.ainvoke({"path": "src/demo.ts", "content": "export const x = 1;\n"}))

        self.assertIn("Validation skipped: tsconfig.json not found", result)
        execute_bash.assert_awaited_once()

    def test_write_file_runs_repo_aware_python_test_validation(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 42, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": True, "output": "__VALIDATION_STRATEGY__:pytest\n", "error": "", "exit_code": 0}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({"path": "tests/test_demo.py", "content": "def test_ok():\n    assert True\n"})
                )

        self.assertIn("Validation: Python syntax OK", result)
        self.assertIn("Validation: Python tests OK (pytest)", result)
        execute_bash.assert_awaited_once()
        self.assertIn("python3 -m pytest -q \"$TARGET_PATH\"", execute_bash.await_args.args[0])
        self.assertIn("unittest", execute_bash.await_args.args[0])

    def test_write_file_runs_related_python_test_validation_for_source_file(self):
        with patch.object(tools_module.runtime_manager, "write_file", AsyncMock(return_value={"success": True, "size": 38, "error": ""})):
            with patch.object(
                tools_module.runtime_manager,
                "execute_bash",
                AsyncMock(return_value={"success": False, "output": "", "error": "", "exit_code": 124}),
            ) as execute_bash:
                result = asyncio.run(
                    tools_module.write_file.ainvoke({"path": "app/demo.py", "content": "def demo():\n    return 1\n"})
                )

        self.assertIn("Validation: Python syntax OK", result)
        self.assertIn("Validation skipped: no matching Python test file found", result)
        execute_bash.assert_awaited_once()
        self.assertIn("tests/test_demo.py", execute_bash.await_args.args[0])
        self.assertIn("tests/demo_test.py", execute_bash.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
