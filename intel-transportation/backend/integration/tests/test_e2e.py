"""把 e2e 套件（unittest）接进 pytest 收集。

`python -m backend.integration.e2e_test_suite` 照常可用（打印结果表），
pytest 侧通过 Test 前缀的子类收集同七条用例，纳入 `pytest backend` 的统一口径。
pytest 8 会收集模块命名空间里任何 TestCase 子类（不看名字），所以子类建完后
立刻 del 掉基类引用，避免同七条用例被收集两遍。
"""

from backend.integration.e2e_test_suite import EndToEndCases as _EndToEndCases


class TestEndToEnd(_EndToEndCases):
    """用例本体与结果表脚本共享同一实现，这里只负责让 pytest 收集到。"""


del _EndToEndCases
