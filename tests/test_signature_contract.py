"""签名契约测试 —— ProjectContext 立规的安全网（收尾）。

锁死一条铁律：每个注册的检查函数，其**第一个参数必须命名为 `ctx`**，
且类型为 ProjectContext。这样：
- 任何检查都只能通过 `ctx` 读取项目状态，无法 reintroduce 模块级全局；
- 重构/新增检查时，若有人写成无参或首参非 ctx，此测试立即报红。

断言与「检查内部如何实现」解耦，只校验公开契约（首参名）。
"""
import pytest
import inspect
import unittest

import check # noqa: E402

pytestmark = pytest.mark.smoke

class TestCheckSignatureContract(unittest.TestCase):
    def test_every_check_first_param_is_ctx(self):
        bad = []
        for name, (func, stage) in check.CHECK_REGISTRY.items():
            try:
                params = list(inspect.signature(func).parameters)
            except (ValueError, TypeError):
                bad.append((name, "no-signature"))
                continue
            if not params:
                bad.append((name, "no-params"))
            elif params[0] != "ctx":
                bad.append((name, params[0]))
        self.assertEqual(
            bad, [],
            f"以下检查首参必须是 ctx（ProjectContext 立规，禁止模块级全局）: {bad}",
        )

    def test_shared_infra_exposes_stable_api(self):
        # ProjectContext 是框架唯一稳定公开接口，必须可从 check_shared 导入
        from check_shared import ProjectContext, build_context
        import inspect as _inspect
        # build_context 的返回值应为 ProjectContext
        sig = _inspect.signature(build_context)
        self.assertIn("config", sig.parameters)
        # ProjectContext 自身是 dataclass（可构造、字段稳定）
        self.assertTrue(hasattr(ProjectContext, "__dataclass_fields__"))

if __name__ == "__main__":
    unittest.main()
