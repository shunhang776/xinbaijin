# 白槿 v2 — Phase 1 详细修复方案

> 文档类型：Phase 1 Foundation 整改执行方案  
> 适用范围：`core/`、`version.py`、`scripts/ops/lint_imports.py`、`requirements/`、Phase 1 测试套件  
> 制定依据：Phase 1 执行方案、执行进度及十五项交付物审查结果  
> 当前判定：**15/15 文件已产出，262/262 现有测试通过，但 Phase 1 设计验收未通过**  
> 修复目标：关闭所有 P0/P1 缺陷，补齐设计合同测试，使 Phase 1 达到可作为 Phase 2 稳定地基的标准

---

## 1. 文档目标

本修复方案用于解决 Phase 1 当前存在的以下核心问题：

1. 文件已经生成，但公共接口、生命周期、异常、信号、lint 和依赖合同不完整。
2. 现有 262 项测试主要验证当前实现，未完整覆盖 Phase 1 设计指标。
3. 执行进度中的“Phase 1 完成”与实际验收结果不一致。
4. 如果继续扩展 Phase 2，后续模块可能建立在不稳定或错误的基础合同上。
5. 部分问题属于“测试全部通过但功能实际上缺失”的假绿灯问题，必须增加独立验收门禁。

本轮整改完成后，应满足：

- `core` 公共门面唯一、稳定、可导入；
- DTO、Protocol、异常、容器和生命周期合同明确；
- 配置、缓存、日志、指标和时间工具行为可预测；
- 信号处理支持 Linux、macOS 和 Windows；
- lint 能发现真实循环依赖和语法错误；
- requirements 可在干净环境中安装；
- Phase 1 设计指标全部转化为自动化测试；
- Phase 1 可以被重新标记为“验收通过”。

---

## 2. 修复原则

### 2.1 阻断优先

必须先修复会导致后续阶段建立在错误基础上的问题：

- 公共门面导出；
- 真实循环依赖检测；
- Protocol 实现校验；
- 生命周期与懒加载；
- 异常追踪上下文；
- Windows 信号处理；
- 干净环境依赖安装。

### 2.2 合同优先于实现细节

优先明确以下合同，再调整具体代码：

- 什么是公开 API；
- 什么配置可热更；
- DTO 是否真正不可变；
- 容器允许在何时 `resolve()`；
- 生命周期启动和停止顺序；
- 错误码由谁校验；
- 时间戳采用什么标准；
- 日志必须过滤哪些敏感信息。

### 2.3 每个设计指标必须有测试

禁止仅凭注释、文档或人工确认宣称完成。  
每一项设计目标至少对应一个自动化测试或 CI 门禁。

### 2.4 避免一次性大改

建议以小批次提交完成，每个提交满足：

- 单一目的；
- 可独立审查；
- 测试全绿；
- 可独立回滚；
- 不夹带 Phase 2 新功能。

### 2.5 修复期间冻结 Phase 2 扩展

允许修复已有 Phase 2 代码对 Phase 1 公共合同的引用，但暂停：

- 新增 Phase 2 模块；
- 扩大旧代码转发范围；
- 新增索引、存储和业务模块；
- 修改与本轮 Phase 1 整改无关的功能。

---

## 3. 缺陷优先级

### 3.1 P0：必须立即修复

| 编号 | 问题 | 影响 |
|---|---|---|
| P0-01 | `core/__init__.py` 未导出设计要求的公共符号 | Phase 1 指定 import 直接失败 |
| P0-02 | lint 无法发现真实跨文件循环依赖 | 核心门禁失效 |
| P0-03 | lint 对 `SyntaxError` 静默跳过 | 无法运行的文件也可能通过检查 |
| P0-04 | `requirements` 缺少 `pydantic-settings` 和开发依赖 | 干净环境可能无法导入或测试 |
| P0-05 | 容器不校验 Protocol 实现 | 错误实现可被注册并启动 |
| P0-06 | lazy 组件首次解析不启动生命周期 | 产生半初始化实例 |
| P0-07 | Windows 控制台关闭事件未适配 | Windows 下无法可靠优雅停止 |
| P0-08 | 清理回调异常被视为正常退出 | 资源未清理仍返回成功状态 |

### 3.2 P1：Phase 1 验收前必须修复

| 编号 | 问题 |
|---|---|
| P1-01 | DTO 仅浅层 frozen，可变集合仍可原地修改 |
| P1-02 | `Result[T]` 可构造逻辑矛盾状态 |
| P1-03 | 异常不自动携带 trace_id/span_id |
| P1-04 | 各异常层不强制错误码前缀 |
| P1-05 | 容器启动失败不回滚已启动组件 |
| P1-06 | `resolve()` 不检测递归循环 |
| P1-07 | 缺失依赖可能被误报为循环依赖 |
| P1-08 | 北京“偏移时间戳”不符合标准 Unix 时间语义 |
| P1-09 | `week_str()` 跨年 ISO 周计算错误 |
| P1-10 | 日志无生产采样 |
| P1-11 | 异常堆栈和 Authorization 等敏感信息未完整脱敏 |
| P1-12 | 配置热更批量更新对并发读者非原子可见 |
| P1-13 | 默认认证 token 可预测 |
| P1-14 | `config.py` 与 `paths.py` 路径双重真源 |
| P1-15 | 指标错误计数会扭曲请求平均延迟 |

### 3.3 P2：本轮建议修复

- 缓存 key lock 代际竞争；
- 缓存参数边界校验；
- 过期条目主动清理；
- metrics 增加明确错误率；
- metrics 使用 `MetricsSnapshot`；
- 日志 handler 自愈补齐；
- 日志 Formatter 双换行；
- 常量接入；
- 版本号唯一真源；
- 标准库检测改为 `sys.stdlib_module_names`；
- requirements 拆分和 Python 版本约束。

### 3.4 P3：后续优化

- 配置模型进一步按领域拆分；
- lint 输出 SARIF 或 JSON；
- 指标增加分位数统计；
- 日志增加结构化事件 schema；
- cache 增加后台清理或采样清理；
- 容器增加依赖图可视化。

---

## 4. 总体执行顺序

建议按以下八个批次执行。

| 批次 | 内容 | 是否阻断后续 |
|---|---|---|
| R1 | 建立修复基线和独立验收测试 | 是 |
| R2 | 修复公共门面、版本和依赖 | 是 |
| R3 | 修复 types、exceptions 和 logging context | 是 |
| R4 | 修复 container 生命周期和 Protocol 合同 | 是 |
| R5 | 修复 signal handler 跨平台关闭 | 是 |
| R6 | 修复 lint 真实依赖图和语法门禁 | 是 |
| R7 | 修复 config、utils、cache、metrics、logging | 是 |
| R8 | 全量回归、干净安装、跨平台验收和进度更新 | 是 |

只有 R1—R8 全部通过后，Phase 1 才能重新标记为完成。

---

# 5. R1：建立修复基线和独立验收测试

## 5.1 创建修复分支

建议：

```bash
git checkout -b fix/phase1-foundation-acceptance
git tag phase1-before-remediation
```

确保当前代码、262 项测试和已有 Phase 2.1 代码均可回退。

## 5.2 保存当前测试基线

执行：

```bash
python -m compileall core scripts/ops/lint_imports.py version.py
python -m pytest -q
```

保存：

- Python 版本；
- 操作系统；
- 已安装依赖；
- 测试数量；
- 测试耗时；
- 失败日志；
- 当前 Git commit。

## 5.3 新建 Phase 1 验收测试目录

建议新增：

```text
tests/acceptance/phase1/
  test_public_facade.py
  test_types_contract.py
  test_exception_contract.py
  test_container_contract.py
  test_signal_contract.py
  test_config_contract.py
  test_logging_contract.py
  test_lint_contract.py
  test_requirements_contract.py
  test_version_contract.py
```

验收测试应独立于已有单元测试，不允许为了让旧实现通过而降低合同标准。

## 5.4 首先编写失败测试

在正式修代码前，先添加能稳定复现当前缺陷的测试：

### 公共门面

```python
def test_phase1_public_facade_imports() -> None:
    from core import (
        settings,
        Result,
        BaijinBaseError,
        DependencyContainer,
        install_signal_handlers,
    )
```

### lint 跨文件循环

在临时目录构造：

```text
a.py -> import b
b.py -> import a
```

要求 lint 返回非零错误数。

### lint 语法错误

构造非法文件：

```python
def broken(
```

要求 lint 报语法错误并退出失败。

### Protocol 校验

注册不符合 Protocol 的实现，要求注册或启动时失败。

### lazy 生命周期

首次 `resolve()` lazy 组件后，要求组件已经执行 `start()`。

### DTO 深层不可变

要求：

```python
dto.memories.append("x")
```

不能成功。

### Windows 信号适配

在 Windows 平台测试 handler 注册分支；在非 Windows 平台通过 mock 验证 `SetConsoleCtrlHandler` 逻辑。

## 5.5 R1 验收标准

- 新增的缺陷复现测试应在旧代码上失败；
- 原有 262 项测试仍保持结果可复现；
- 不修改生产实现以“绕过”测试；
- 测试名称明确对应设计合同。

---

# 6. R2：修复公共门面、版本和依赖

## 6.1 修复 `core/__init__.py`

### 目标

- `core` 成为 Phase 1 唯一公共导入入口；
- 对外符号明确；
- 内部实现可调整而不影响调用方；
- 杜绝命名空间污染。

### 建议结构

```python
from core.config import FeaturesConfig, Settings, features, settings
from core.container import DependencyContainer
from core.exceptions import (
    AppError,
    BaijinBaseError,
    InfraError,
    MemoryError,
    PluginError,
)
from core.signal_handler import (
    install_signal_handlers,
    is_shutting_down,
)
from core.types import (
    BaseIndex,
    DomainModule,
    FusionStrategy,
    HealthStatus,
    LifecycleComponent,
    MemoryItem,
    MetricsSnapshot,
    PromptData,
    Result,
)
```

定义完整 `__all__`，禁止使用星号导入。

### 注意事项

- 避免 `from core import constants` 这种包内自引用形式；
- 包初始化不能执行重型初始化；
- 不应在 `__init__.py` 中安装 signal handler；
- 不应在导入时启动线程或访问网络；
- `settings = Settings()` 是否在 import 时实例化，需要在 config 修复中统一决定。

### 新增测试

- 所有设计公共符号可导入；
- `__all__` 与实际导出一致；
- 不导出私有实现；
- 重复导入无副作用；
- `python -c "import core"` 不创建额外后台线程。

## 6.2 修复版本唯一真源

### 方案

根目录 `version.py` 保留：

```python
__version__ = "2.0.0"
```

其他模块统一：

```python
from version import __version__
```

修改：

- `HealthStatus.version` 默认值；
- 日志基础字段；
- 健康接口；
- 启动日志；
- 以后包 metadata。

### 验收

- 全项目搜索不存在第二个 `"2.0.0"` 业务硬编码；
- 修改 `version.py` 后，HealthStatus 和日志自动使用新版本。

## 6.3 重构 requirements

建议恢复设计中的职责拆分：

```text
requirements/
  base.txt
  memory.txt
  embedding.txt
  vision.txt
  server.txt
  dev.txt
  all.txt
```

### `base.txt`

至少包括：

```text
pydantic>=2.7,<3
pydantic-settings>=2.2,<3
psutil>=5.9,<7
```

### `server.txt`

```text
-r base.txt
fastapi...
uvicorn...
httpx...
```

### `memory.txt`

只包括记忆和索引依赖：

```text
-r base.txt
numpy...
faiss-cpu...
datasketch...
jionlp...
```

### `embedding.txt`

```text
-r base.txt
torch...
FlagEmbedding...
```

### `vision.txt`

```text
-r base.txt
opencv-python...
```

### `dev.txt`

```text
-r all.txt
pytest...
pytest-cov...
mypy...
ruff...
```

### `all.txt`

组合运行时全部依赖，不应隐含 dev 工具。

### Python 版本

在 `pyproject.toml` 或项目文档中声明支持范围，例如：

```text
Python >=3.10,<3.13
```

若必须支持 Python 3.13，需要先升级 NumPy、Torch、FAISS 等并在 CI 验证。

## 6.4 R2 验收标准

```bash
python -c "from core import settings, Result, BaijinBaseError, DependencyContainer, install_signal_handlers"
```

必须成功。

在全新虚拟环境：

```bash
python -m pip install -r requirements/dev.txt
python -m pytest -q
```

必须成功。

---

# 7. R3：修复 `core/types.py`、`core/exceptions.py` 和追踪上下文

## 7.1 `Result[T]` 合同

### 建议不变量

- `ok=True`：
  - `error_code` 必须为空；
  - `message` 可选；
  - `data` 可有可无。
- `ok=False`：
  - `error_code` 必须非空；
  - `data` 默认必须为空；
  - 如需 partial data，单独设计字段，禁止混用。

示例：

```python
@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    ok: bool
    data: T | None = None
    error_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.ok and self.error_code:
            raise ValueError("成功结果不能携带 error_code")
        if not self.ok and not self.error_code:
            raise ValueError("失败结果必须携带 error_code")
        if not self.ok and self.data is not None:
            raise ValueError("失败结果不能携带 data")
```

建议补充：

```python
Result.success(data)
Result.failure(error_code, message)
```

## 7.2 DTO 深层不可变

将可变字段改为不可变类型：

| 当前类型 | 建议类型 |
|---|---|
| `list[str]` | `tuple[str, ...]` |
| `dict[str, object]` | `Mapping[str, object]` |
| `dict[str, int]` | `Mapping[str, int]` |

构造时使用：

- `tuple(value)`；
- `MappingProxyType(dict(value))`；
- 深拷贝必要的嵌套数据。

建议启用：

```python
@dataclass(frozen=True, slots=True)
```

## 7.3 Protocol 合同清理

逐一确认：

- `LifecycleComponent.start()` 和 `stop()` 是同步还是异步；
- `health()` 返回 `HealthStatus` 还是布尔值；
- `BaseIndex` 的 ID、向量、删除和查询签名；
- `FusionStrategy` 输入输出排序语义；
- `DomainModule` 是否承担生命周期；
- Protocol 方法是否需要 keyword-only 参数。

为每个 Protocol 创建合同测试基类，后续实现模块可以复用。

## 7.4 建立追踪上下文公共模块

建议将 trace/span contextvars 放入独立模块：

```text
core/context.py
```

提供：

```python
get_trace_id()
get_span_id()
set_trace_context()
reset_trace_context()
trace_context(...)
```

原因：

- `exceptions.py` 和 `logging.py` 都需要使用；
- 避免 exceptions 反向导入 logging；
- 避免循环依赖；
- 为中间件和后台线程提供统一接口。

## 7.5 异常自动注入上下文

`BaijinBaseError` 构造时：

1. 复制调用方 context；
2. 自动读取 trace_id/span_id；
3. 调用方未提供时补入；
4. 保留原始 cause；
5. context 对外只读或返回副本。

建议：

```python
class BaijinBaseError(Exception):
    prefix: ClassVar[str]

    def __init__(..., context: Mapping[str, object] | None = None, cause: Exception | None = None):
        ...
```

## 7.6 强制错误码前缀

每层异常定义：

```python
class InfraError(BaijinBaseError):
    prefix = "INF_"
```

构造时校验：

```python
if not error_code.startswith(self.prefix):
    raise ValueError(...)
```

允许使用 `ErrorCode` Enum，内部统一转换为字符串。

## 7.7 异常包装

增加明确 API：

```python
InfraError.wrap(exc, error_code=..., message=..., context=...)
```

并使用：

```python
raise wrapped from exc
```

测试必须验证：

- `__cause__`；
- 错误码；
- trace/span；
- context；
- `__str__`；
- `__repr__`；
- 各层错误码前缀。

## 7.8 R3 验收标准

- DTO 不能通过 list/dict 原地修改；
- `Result` 非法组合无法构造；
- 异常自动包含 trace/span；
- 错层错误码会立即失败；
- 异常链保留原始 cause；
- types、exceptions、logging 之间无循环依赖。

---

# 8. R4：修复 `core/container.py`

## 8.1 明确容器状态机

建议状态：

```text
CREATED
REGISTERING
STARTING
RUNNING
STOPPING
STOPPED
FAILED
```

规则：

- `REGISTERING` 前允许注册；
- `RUNNING` 后禁止新增注册；
- 运行期禁止随意 `resolve()`，只允许已注册依赖或明确允许的 lazy 组件；
- 停止期间禁止创建新实例。

## 8.2 注册时和启动时双重校验

注册时检查：

- key 是否是 Protocol 或预期类型；
- provider 是类、factory 还是实例；
- 依赖是否声明；
- 是否重复注册；
- 生命周期标记是否合法。

启动时检查：

- 实例是否满足 Protocol；
- 生命周期方法是否存在且签名有效；
- 所有依赖是否已注册；
- 图中是否有循环；
- factory 返回类型是否正确。

注意：`runtime_checkable Protocol` 只做结构存在性检查，不能完全校验签名。  
建议结合：

- `isinstance(instance, protocol)`；
- `inspect.signature()`；
- 合同测试。

## 8.3 修复依赖图

为每个注册项建立节点：

```python
Registration(
    contract,
    provider,
    dependencies,
    lazy,
    lifecycle,
)
```

拓扑排序前先单独检测未注册依赖：

```text
MissingDependencyError
```

不要把缺失依赖混淆为循环依赖。

## 8.4 `resolve()` 递归保护

使用 context-local 或线程局部解析栈：

```text
A -> B -> C -> A
```

发现当前 contract 已在解析栈时，立即抛出 `CircularDependencyError`，并输出完整链路。

## 8.5 lazy 生命周期语义

建议：

- lazy 组件在 `start_all()` 时不实例化；
- 首次 `resolve()`：
  1. 解析依赖；
  2. 创建实例；
  3. 校验 Protocol；
  4. 执行 `start()`；
  5. 记录到 started order；
  6. 返回实例。
- 多线程首次 resolve 必须只启动一次；
- lazy 启动失败不得缓存失败实例。

## 8.6 启动失败回滚

`start_all()` 中：

1. 记录已成功启动组件；
2. 任一组件失败：
   - 标记容器 FAILED；
   - 对已启动组件逆序 stop；
   - 收集回滚错误；
   - 抛出包含启动失败和回滚情况的异常。

## 8.7 停止策略

- 使用实际启动顺序的逆序；
- lazy 已解析组件也必须停止；
- 每个组件停止失败不阻止其他组件清理；
- 最终聚合错误；
- 重复 stop 幂等；
- 未启动组件不调用 stop。

## 8.8 lint 规则

增加规则：

- 应用运行时业务代码禁止直接 `container.resolve()`；
- 只允许在 bootstrap、factory、测试和明确白名单文件调用；
- 推荐构造注入。

## 8.9 R4 验收标准

- 不符合 Protocol 的实现无法启动；
- 缺失依赖报明确错误；
- 循环依赖输出完整路径；
- lazy 组件首次解析自动 start；
- lazy 组件并发首次解析只初始化一次；
- 启动失败会逆序回滚；
- stop_all 按实际启动顺序逆序执行；
- 重复 start/stop 行为明确且测试覆盖。

---

# 9. R5：修复 `core/signal_handler.py`

## 9.1 明确关闭阶段

建议定义：

```python
class ShutdownPhase(Enum):
    IDLE
    STOP_ACCEPTING
    DRAINING
    CLOSING_RESOURCES
    FINISHED
    FORCED
```

关闭流程必须明确执行：

1. 停止接收新请求；
2. 排空队列，最大等待 10 秒；
3. 停止调度器和后台任务；
4. 关闭 DB；
5. 关闭索引；
6. 关闭模型和外部客户端；
7. 刷新日志；
8. 正常退出。

## 9.2 使用结构化 shutdown coordinator

不要只接收一个不透明回调。建议注册带阶段的回调：

```python
register_shutdown_step(name, order, callback, timeout)
```

或由容器统一执行 `stop_all()`，前置一个 stop accepting 回调。

## 9.3 Linux/macOS

注册：

- `SIGINT`
- `SIGTERM`

要求：

- 安装幂等；
- 保留或明确替代旧 handler；
- 主线程安装；
- 重复信号触发强制退出。

## 9.4 Windows

使用 `ctypes` 调用：

```text
SetConsoleCtrlHandler
```

处理：

- `CTRL_C_EVENT`
- `CTRL_BREAK_EVENT`
- `CTRL_CLOSE_EVENT`
- `CTRL_LOGOFF_EVENT`
- `CTRL_SHUTDOWN_EVENT`

同时在可用时注册：

- `SIGINT`
- `SIGTERM`
- `SIGBREAK`

handler 函数引用必须被长期保存，防止被垃圾回收。

## 9.5 清理异常传播

后台清理线程必须捕获异常并将结果传回主 handler：

```python
result_queue
future
Event + error holder
```

退出码建议：

- 0：全部清理成功；
- 1：清理失败；
- 2：强制超时；
- 130：SIGINT；
- 143：SIGTERM；

具体规则可统一，但不能将失败伪装为成功。

## 9.6 强制退出

第二次信号或总超时后：

1. 尝试刷新 stderr/log；
2. 使用 `os._exit(code)` 作为最终兜底。

仅使用 `sys.exit()` 不足以终止卡死线程。

## 9.7 测试

- 多次 install 幂等；
- 第一次信号进入关闭；
- 第二次信号强制退出；
- 回调异常返回失败；
- 回调超时强制退出；
- 阶段顺序正确；
- Windows handler 注册；
- 所有入口统一调用 `install_signal_handlers()`。

## 9.8 R5 验收标准

- Windows 控制台关闭可触发清理；
- 清理异常不会记录“成功”；
- drain 超时和总超时行为明确；
- 关闭顺序有自动化测试；
- `_launcher.py`、main、daemon 等入口无重复安装或分叉逻辑。

---

# 10. R6：重写 `scripts/ops/lint_imports.py`

## 10.1 解析模块图

对项目 Python 文件建立：

```text
module -> imported project modules
```

需要处理：

- `import a`
- `import a.b`
- `from a import b`
- 相对导入；
- 包 `__init__.py`；
- 模块别名；
- 仅类型检查导入；
- 可选导入。

## 10.2 真实循环依赖算法

使用 DFS 三色标记或 Tarjan SCC：

- 白色：未访问；
- 灰色：当前路径；
- 黑色：已完成。

发现环时输出完整链路：

```text
core.config -> core.logging -> core.config
```

自导入只是循环依赖的一种特殊情况，不能替代图检测。

## 10.3 SyntaxError 必须硬失败

解析异常输出：

```text
E000 SyntaxError file.py:line:column message
```

lint 退出码必须非零。

不得返回空 import 列表后继续通过。

## 10.4 层级依赖规则

建立显式层级：

```text
core
infra
memory
domain
services
plugins
app
ops
```

每层配置允许依赖集合。Phase 1 至少启用：

- `core` 不依赖上层；
- `scripts/ops` 可依赖 core，但不反向；
- root bootstrap 可依赖各层。

## 10.5 裸异常规则

禁止：

```python
raise Exception(...)
raise RuntimeError(...)
```

允许：

- 测试；
- 明确白名单；
- 第三方边界包装后转换为分层异常。

## 10.6 运行期 resolve 规则

检测：

```python
container.resolve(...)
```

只允许：

- bootstrap；
- provider factory；
- 容器自身；
- 测试。

## 10.7 标准库识别

使用：

```python
sys.stdlib_module_names
```

兼容当前 Python 版本，避免手写列表漂移。

## 10.8 导入顺序

只检查模块顶层导入。函数内部延迟导入单独标记或忽略，不应与顶层顺序混用。

## 10.9 CLI 和退出码

建议：

```bash
python scripts/ops/lint_imports.py
python scripts/ops/lint_imports.py --strict
python scripts/ops/lint_imports.py --format json
```

退出码：

- 0：通过；
- 1：发现 lint 错误；
- 2：工具自身执行失败。

## 10.10 R6 验收标准

以下样例必须失败：

- 双文件循环；
- 三文件循环；
- 相对导入循环；
- SyntaxError；
- core 反向依赖 infra；
- 运行期 resolve；
- 裸抛 Exception。

以下必须通过：

- 类型检查专用导入；
- 标准库导入；
- 合法层级依赖；
- 包内正常相对导入。

---

# 11. R7：修复其余基础模块

# 11.1 `core/config.py`

## 11.1.1 拆分配置组

建议：

```python
class StartupConfig(BaseModel):
    # 启动后只读

class RuntimeConfig(BaseModel):
    # 可热更

class FeaturesConfig(BaseModel):
    # 灰度开关

class Settings(BaseSettings):
    startup: StartupConfig
    runtime: RuntimeConfig
    features: FeaturesConfig
```

不建议让嵌套子配置再次继承 `BaseSettings` 并各自读取 `.env`，否则环境变量解析可能分散。

## 11.1.2 原子热更

推荐 copy-on-write：

1. 获取当前 runtime 快照；
2. 合并 overrides；
3. 完整验证新 runtime；
4. 在锁内一次性替换 `_runtime`；
5. 锁外执行回调。

读线程读取不可变快照，避免看到半更新状态。

## 11.1.3 回调管理

- 注册和移除回调都加锁；
- 回调列表调用前复制；
- 单个回调失败不影响其他回调；
- 回调不能持有 reload 锁执行；
- 支持 unsubscribe。

## 11.1.4 密钥 repr

需要先确认产品要求：

- 安全优先：始终 `**********`；
- 合同严格：显示首尾四位。

若保留“首尾四位”要求，短密钥处理必须明确：

```text
长度 <= 8：完全隐藏
长度 > 8：abcd****wxyz
```

日志中仍应完全隐藏，不应使用首尾展示。

## 11.1.5 移除默认 token

`memory_api_token` 默认应为空。生产模式启动时若为空，直接失败。

开发模式如需默认值，应显式：

```text
BAIJIN_ENV=development
```

且输出安全警告。

## 11.1.6 路径唯一真源

建议：

- 静态项目根路径来自 `core.paths`；
- 配置路径默认值引用 `core.paths`；
- 删除 `BASE_DIR`、`DATA_DIR` 大写字段；
- 运行时只通过 `settings.startup.paths` 读取可配置路径。

## 11.1.7 避免 import 时副作用

当前 `settings = Settings()` 会创建多个目录。建议：

- 评估是否允许 import 时创建目录；
- 更稳妥的是在 bootstrap 中执行 `ensure_directories()`；
- 单元测试 import 不应修改文件系统。

---

# 11.2 `core/utils.py`

## 11.2.1 统一标准 Unix 时间戳

修正为：

```python
def now_ts() -> int:
    return int(time.time())
```

展示北京时间：

```python
datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Shanghai"))
```

禁止再加减八小时。

## 11.2.2 数据迁移

如果旧数据已经使用偏移时间戳，必须：

1. 检测历史字段；
2. 备份数据库；
3. 编写迁移脚本；
4. 仅转换确定属于旧格式的数据；
5. 防止重复迁移；
6. 记录迁移版本。

不能直接改函数而忽略已有数据。

## 11.2.3 ISO week 修复

使用：

```python
iso_year, iso_week, _ = dt.isocalendar()
return f"{iso_year}-W{iso_week:02d}"
```

增加跨年样例。

## 11.2.4 时间校验

验证：

- 类型；
- 秒级范围；
- 最小合法时间；
- 未来容忍范围；
- bool 非法；
- 浮点是否允许。

---

# 11.3 `core/cache.py`

## 11.3.1 修复 key lock 竞争

建议条目：

```python
@dataclass
class _KeyLock:
    lock: RLock
    users: int
```

获取时增加 users，释放后减少。只有 users 为 0 且字典仍指向同一条目时才删除。

另一方案是长期保留有限数量的 striped locks，避免动态 lock 生命周期问题。

## 11.3.2 参数验证

构造时：

- `max_size > 0`；
- `default_ttl > 0`。

set 时：

- `ttl > 0`；
- TTL 抖动后最小为 1 秒。

## 11.3.3 过期清理

在 set 或一定采样比例下清理过期条目。避免过期对象长期占用容量。

## 11.3.4 factory 异常

- factory 失败不得写缓存；
- 等待线程可重新尝试；
- 可选短期错误缓存必须单独设计，不能隐式实现。

## 11.3.5 统计

增加：

- hits；
- misses；
- evictions；
- expirations；
- computes；
- stampede_waits。

---

# 11.4 `core/metrics.py`

## 11.4.1 明确请求记录 API

建议统一为一次调用：

```python
record_request(latency_ms, ok=True, error_type=None)
```

避免 `record_request()` 与 `record_error()` 双计数。

如果保留 `record_error()`，它只能分类错误，不能再次增加 request total。

## 11.4.2 快照使用 DTO

返回 `MetricsSnapshot`，字段包括：

- request_total；
- request_error；
- error_rate；
- latency_avg_ms；
- latency_max_ms；
- cache_hit_rate；
- search_hit_rate；
- token_used；
- process_memory_mb。

## 11.4.3 错误分类

增加：

```python
errors_by_type: Mapping[str, int]
```

限制 error_type 的基数，避免无限增长。

## 11.4.4 内存采集容错

- psutil 不可用或进程结束时不应让快照失败；
- 返回 `None` 或明确 unavailable 状态；
- 不要在锁内执行可能较慢的系统调用。

---

# 11.5 `core/logging.py`

## 11.5.1 结构化敏感信息过滤

过滤范围：

- message；
- args；
- extra；
- exception text；
- stack trace；
- HTTP headers；
- URL query；
- dict/list 嵌套值。

关键词：

```text
api_key
secret
token
authorization
password
client_secret
cookie
set-cookie
```

Authorization 统一隐藏 Bearer/Basic 内容。

## 11.5.2 不在 Formatter 中手动追加换行

返回单行 JSON 字符串，由 handler terminator 添加换行。

## 11.5.3 文件 handler 自愈

分别检测：

- 普通日志 handler；
- 错误日志 handler。

缺哪个补哪个，不因存在一个 handler 就提前返回。

## 11.5.4 使用常量

接入：

- `LOG_RETENTION_DAYS`；
- 错误日志保留天数新常量；
- 必要时选择按日期轮转或按大小轮转，避免定义未使用常量。

## 11.5.5 生产采样

建议只采样低等级高频日志：

- ERROR/CRITICAL：100%；
- WARNING：100% 或高比例；
- INFO：按配置采样；
- DEBUG：生产默认关闭。

采样键建议基于：

```text
logger name + event name + trace id
```

使用确定性 hash，保证同一 trace 行为稳定。

## 11.5.6 trace 接口

logging 从 `core.context` 读取 trace/span，不直接拥有上下文真源。

## 11.5.7 版本字段

日志 payload 自动加入 `service_version`。

---

# 11.6 `core/constants.py` 和 `core/paths.py`

## constants

- 将外部 LLM URL 拆为 `ExternalEndpoint`；
- 修正 `PROTECTED_GRADES` 类型；
- 清理未使用常量；
- 增加错误日志保留天数；
- 所有业务常量标记唯一真源。

## paths

- 仅保留静态默认路径；
- 不创建目录；
- 与 Settings 默认值直接引用；
- 增加路径合同测试；
- Windows 路径测试不得依赖字符串拼接。

## 11.7 R7 验收标准

- 标准 Unix 时间戳与外部系统一致；
- ISO 跨年周正确；
- 批量配置热更对读取者原子可见；
- 日志异常文本也能脱敏；
- 生产采样可配置；
- cache 同 key 不会出现双 factory；
- metrics 不再双计请求；
- 所有路径只有一个默认真源。

---

# 12. R8：全量验收和 CI 门禁

## 12.1 静态检查

```bash
python -m compileall core scripts/ops/lint_imports.py version.py
python scripts/ops/lint_imports.py --strict
ruff check .
mypy core scripts/ops/lint_imports.py
```

## 12.2 单元测试

```bash
python -m pytest tests -q
```

要求：

- 原有 262 项测试全部继续通过，或因合同修正而被合理更新；
- 新增验收测试全部通过；
- 不允许 skip/xfail 掩盖 P0/P1 问题。

## 12.3 覆盖率

建议：

```bash
python -m pytest --cov=core --cov=scripts.ops.lint_imports --cov-report=term-missing
```

最低建议：

- core 总体行覆盖率 ≥ 90%；
- P0/P1 文件分支覆盖率 ≥ 85%；
- container、signal、lint 的错误分支必须覆盖。

覆盖率不是唯一目标，重点是合同分支被验证。

## 12.4 干净环境

至少测试：

- Windows + Python 3.11；
- Linux + Python 3.11；
- Linux + Python 3.12。

若声明支持 Python 3.13，则必须增加 3.13。

流程：

```bash
python -m venv .venv-clean
.venv-clean/bin/python -m pip install --upgrade pip
.venv-clean/bin/python -m pip install -r requirements/dev.txt
.venv-clean/bin/python -m pytest -q
```

Windows 使用对应 Scripts 路径。

## 12.5 安装组合测试

分别测试：

- base；
- server；
- memory；
- embedding；
- vision；
- all；
- dev。

确保模块未安装可选依赖时，核心包仍能 import，且可选功能给出清晰错误。

## 12.6 运行时烟雾测试

```bash
python -c "import core"
python -c "from core import settings, Result"
python -c "from core import DependencyContainer"
python -c "from core import install_signal_handlers"
```

检查：

- 无循环依赖；
- 无后台线程泄漏；
- 无意外目录创建；
- 无密钥明文；
- 无 import-time 网络访问。

## 12.7 进度文档修正

修复完成前：

```text
Phase 1：15/15 文件已产出；验收阻塞。
```

全部通过后：

```text
Phase 1：15/15 文件已产出；设计验收、干净安装和跨平台测试全部通过。
```

不得仅以测试数量作为完成依据。

---

# 13. 建议提交序列

建议每个步骤单独 commit：

```text
test(phase1): add failing acceptance tests
fix(core): restore public facade exports
build(requirements): add clean-install dependency groups
refactor(core-types): enforce immutable DTO contracts
fix(core-errors): validate error prefixes and inject trace context
fix(core-container): enforce protocols and lifecycle state machine
fix(core-signal): add Windows console shutdown support
fix(lint): detect dependency cycles and syntax errors
fix(core-time): adopt standard Unix timestamps and ISO week-year
fix(core-config): make runtime reload atomic
fix(core-cache): close per-key lock race
fix(core-metrics): correct request and error accounting
fix(core-logging): add full redaction and production sampling
test(phase1): complete cross-platform acceptance suite
docs(progress): mark phase1 acceptance complete
```

每个 commit 必须附带对应测试。

---

# 14. 回滚策略

## 14.1 代码回滚

- 每批次独立 commit；
- 任一批次失败可 revert 单个 commit；
- 不删除整个 `core/`；
- 不回滚已验证无关功能。

## 14.2 时间戳迁移回滚

这是本轮风险最高的数据变更。

必须：

1. 数据库完整备份；
2. 迁移表记录版本；
3. 转换前后行数、最小值、最大值和抽样校验；
4. 提供逆向迁移脚本；
5. 灰度验证；
6. 禁止对未知来源时间戳盲目减八小时。

## 14.3 配置回滚

新旧环境变量至少保留一个发布周期的兼容映射，并输出弃用警告。

## 14.4 公共 API 回滚

恢复 `core.__init__` 导出属于兼容性增强。后续删除旧导入路径必须经过 deprecation 周期，不能同一轮直接移除。

---

# 15. 风险清单

| 风险 | 缓解措施 |
|---|---|
| 时间戳修正影响历史数据 | 独立迁移、备份、双读校验 |
| 配置结构拆分影响 Phase 2.1 | 提供兼容 property，逐步迁移 |
| 容器严格校验暴露现有错误实现 | 先增加审计模式，再切严格模式 |
| 日志脱敏误伤正常内容 | 结构化字段优先，正则覆盖测试 |
| Windows handler 难以在 CI 真实触发 | Windows runner + 单元 mock 双覆盖 |
| requirements 版本升级影响模型包 | 分组安装，锁定 Python 支持矩阵 |
| DTO 改 tuple 影响调用方 | 提供转换方法，逐处修正，不保留可变别名 |
| lint 新规则一次产生大量错误 | 先报告模式，修复后转 strict 门禁 |

---

# 16. 详细验收清单

## 公共门面

- [ ] `from core import settings, Result` 成功
- [ ] 所有设计公共符号均在 `__all__`
- [ ] 无私有实现泄漏
- [ ] import 无重型副作用

## 配置

- [ ] 启动只读配置和运行时配置结构分离
- [ ] 批量热更原子
- [ ] 回调线程安全
- [ ] 密钥 repr 符合最终合同
- [ ] 日志中密钥完全隐藏
- [ ] 默认认证 token 已移除
- [ ] 路径只有一个默认真源

## types

- [ ] DTO 深层不可变
- [ ] `Result` 不变量有效
- [ ] Protocol 合同测试存在
- [ ] HealthStatus 版本来自唯一真源

## exceptions

- [ ] 自动注入 trace_id/span_id
- [ ] 四层前缀强制校验
- [ ] context 不共享
- [ ] 异常链保留
- [ ] `__str__` 和 `__repr__` 稳定

## container

- [ ] 注册和启动校验 Protocol
- [ ] 缺失依赖错误明确
- [ ] resolve 循环保护
- [ ] lazy 首次解析自动启动
- [ ] 并发 lazy 只启动一次
- [ ] 启动失败自动回滚
- [ ] stop 逆序、幂等、聚合错误
- [ ] 运行期 resolve 有限制

## signal handler

- [ ] SIGINT
- [ ] SIGTERM
- [ ] Windows Console Control Handler
- [ ] install 幂等
- [ ] 二次信号强制退出
- [ ] 清理失败返回失败
- [ ] drain 和 force timeout 生效
- [ ] 入口统一接入

## utils

- [ ] 标准 Unix 时间戳
- [ ] 北京时区仅用于展示
- [ ] ISO week-year 正确
- [ ] 外部时间校验完整
- [ ] 历史数据迁移可回滚

## cache

- [ ] TTL
- [ ] LRU
- [ ] 容量硬上限
- [ ] 空值缓存
- [ ] TTL 抖动
- [ ] 同 key 单 factory
- [ ] key lock 无代际竞争
- [ ] 参数边界校验
- [ ] 过期条目清理

## metrics

- [ ] 请求计数不重复
- [ ] 错误率字段
- [ ] 延迟平均值正确
- [ ] 错误分类
- [ ] Token 按模型
- [ ] 内存采集容错
- [ ] 返回 MetricsSnapshot

## logging

- [ ] JSON 单行
- [ ] trace/span 自动加入
- [ ] version 自动加入
- [ ] message 脱敏
- [ ] exception 脱敏
- [ ] Authorization 脱敏
- [ ] 普通/错误日志分别归档
- [ ] handler 缺失自动补齐
- [ ] 保留天数使用常量
- [ ] 生产采样

## lint

- [ ] 二文件循环
- [ ] 多文件循环
- [ ] 相对导入循环
- [ ] SyntaxError
- [ ] 层级依赖
- [ ] 裸异常
- [ ] 运行期 resolve
- [ ] 标准库识别
- [ ] 非零退出码

## requirements

- [ ] `pydantic-settings`
- [ ] `dev.txt`
- [ ] embedding 独立
- [ ] vision 独立
- [ ] Python 版本范围
- [ ] 可选依赖不阻塞 core import
- [ ] 干净安装通过

---

# 17. 完成定义

Phase 1 只有同时满足以下条件，才能标记为完成：

1. P0 数量为 0；
2. P1 数量为 0；
3. P2 已修复或有明确批准的延期记录；
4. 设计要求全部有自动化验收；
5. 原有回归测试和新增验收测试全部通过；
6. lint strict 模式通过；
7. 干净环境安装通过；
8. Windows 和 Linux CI 通过；
9. 没有 skip/xfail 掩盖核心合同；
10. 进度文档、实现和测试三者一致；
11. Phase 2.1 已有代码能通过新的 Phase 1 合同；
12. 已完成一次独立终审。

---

# 18. 推荐进度模板

```markdown
## Phase 1 整改进度

| 批次 | 内容 | 状态 | 测试 | 备注 |
|---|---|---|---|---|
| R1 | 验收测试基线 | ⬜ |  |  |
| R2 | 门面、版本、依赖 | ⬜ |  |  |
| R3 | DTO、异常、追踪 | ⬜ |  |  |
| R4 | DI 容器 | ⬜ |  |  |
| R5 | 信号与关闭 | ⬜ |  |  |
| R6 | lint 门禁 | ⬜ |  |  |
| R7 | 其余基础模块 | ⬜ |  |  |
| R8 | 全量验收 | ⬜ |  |  |

### 缺陷统计

| 等级 | 初始数量 | 当前数量 |
|---|---:|---:|
| P0 | 8 |  |
| P1 | 15 |  |
| P2 |  |  |
| P3 |  |  |

### 当前结论

- 文件产出：15/15
- 回归测试：
- Phase 1 验收测试：
- lint：
- Windows CI：
- Linux CI：
- 干净安装：
- Phase 1 是否可关闭：
```

---

# 19. 最终执行建议

本轮不应把目标设定为“继续增加测试数量”，而应设定为：

> **让每一项 Phase 1 设计合同都有真实、可失败、可重复的自动化证明。**

建议立即从 R1 开始，先提交验收失败测试，再按 R2—R8 顺序修复。  
其中最先完成的四项应是：

1. 恢复 `core` 公共门面；
2. 重写真实循环依赖检测；
3. 补全干净安装依赖；
4. 修复容器 Protocol 和生命周期合同。

这四项完成前，不建议继续推进 Phase 2.2 及之后的接入工作。
