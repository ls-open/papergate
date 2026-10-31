"""统一日志配置（ 基础设施）。

替代散落在各处的诊断性 print()，让库模块使用标准 logging，便于测试与输出重定向。

约定（关键，避免破坏 stdout 契约）：
  - 诊断 / 进度 / 警告日志走 **stderr**，不污染 stdout。
    check.py 的最终报告与 `--json` 输出必须留在 stdout（人 / AI 消费通道），
    绝不能被诊断日志干扰。
  - 顶层 CLI 的最终报告、JSON 输出仍用 print 写到 stdout，不在本模块职责内。
"""
import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO", *, force: bool = False) -> logging.Logger:
    """初始化根 logger：stderr handler + 统一格式。幂等（只配一次，除非 force）。"""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return logging.getLogger("papergate")

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    _CONFIGURED = True
    return logging.getLogger("papergate")


def get_logger(name: str = "papergate") -> logging.Logger:
    """获取模块级 logger（库模块用 get_logger(__name__)）。"""
    return logging.getLogger(name)
