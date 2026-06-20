"""
白槿 v2 — 统一配置中心。
"""

import logging
import threading
from pathlib import Path
from typing import Callable, get_type_hints

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"

_SECRET_FIELD_PATTERNS = (
    "api_key", "client_secret", "amap_key", "memory_api_token",
)


def _collect_hot_fields(model: type[BaseSettings], prefix: str = "") -> set[str]:
    fields: set[str] = set()
    type_hints = get_type_hints(model)
    for name, field_info in model.model_fields.items():
        extra = field_info.json_schema_extra
        if extra and extra.get("hot_reload"):
            key = f"{prefix}{name}" if prefix else name
            fields.add(key)
        field_type = type_hints.get(name)
        if isinstance(field_type, type) and issubclass(field_type, BaseSettings):
            sub_prefix = f"{prefix}{name}."
            fields |= _collect_hot_fields(field_type, sub_prefix)
    return fields


def _get_nested(obj: object, key: str) -> object:
    parts = key.split(".")
    for part in parts:
        obj = getattr(obj, part)
    return obj


def _flatten_to_nested(flat: dict[str, object]) -> dict:
    result: dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    return result


def _dict_deep_merge(base: dict, updates: dict) -> dict:
    result = base.copy()
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _dict_deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _log_mask(k: str, v: object) -> object:
    if any(k.endswith(s) for s in _SECRET_FIELD_PATTERNS):
        return "***"
    if isinstance(v, SecretStr):
        return "***"
    return v


class FeaturesConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAIJIN_FEATURE_",
        env_file=".env",
        env_file_encoding="utf-8",
        validate_assignment=True,
        extra="ignore",
    )
    shadow_mode: bool = Field(default=False, json_schema_extra={"hot_reload": True})
    sense_enable: bool = Field(default=True, json_schema_extra={"hot_reload": True})
    sense_camera_enable: bool = Field(default=False, json_schema_extra={"hot_reload": True})
    enable_tts: bool = Field(default=False, json_schema_extra={"hot_reload": True})
    enable_qq: bool = Field(default=False, json_schema_extra={"hot_reload": True})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAIJIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        validate_assignment=True,
        extra="ignore",
    )

    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    config_version: str = Field(default="v2.0")
    log_level: str = Field(default="INFO", json_schema_extra={"hot_reload": True})
    enable_file_logging: bool = Field(default=True, json_schema_extra={"hot_reload": True})

    def __init__(self, **data):
        super().__init__(**data)
        self._reload_lock: threading.RLock = threading.RLock()
        self._on_change_callbacks: list[Callable[[str, object], None]] = []
        self._on_batch_change_callbacks: list[Callable[[dict[str, object]], None]] = []

    def on_change(self, callback: Callable[[str, object], None]) -> None:
        self._on_change_callbacks.append(callback)

    def on_batch_change(self, callback: Callable[[dict[str, object]], None]) -> None:
        self._on_batch_change_callbacks.append(callback)

    def reload(self, **overrides: object) -> dict[str, object]:
        if not overrides:
            return {}
        from core.logging import get_logger
        _logger = get_logger(__name__)
        hot_fields = self._hot_reload_fields()
        with self._reload_lock:
            self._reload_validate_keys(overrides, hot_fields)
            validated = self._reload_apply(overrides, _logger)
            real_changes = {k: _get_nested(validated, k) for k in overrides}
            self._reload_log(real_changes, _logger)
            self._reload_fire_single(real_changes, _logger)
        self._reload_fire_batch(real_changes, _logger)
        return real_changes

    def reload_from_env(self) -> None:
        from core.logging import get_logger
        _logger = get_logger(__name__)
        fresh = type(self)()
        overrides = {}
        for key in self._hot_reload_fields():
            try:
                new_val = _get_nested(fresh, key)
                if new_val != _get_nested(self, key):
                    overrides[key] = new_val
            except AttributeError:
                pass
        if overrides:
            self.reload(**overrides)
        else:
            _logger.debug("reload_from_env: 无变更")

    @classmethod
    def _hot_reload_fields(cls) -> set[str]:
        if not hasattr(cls, "_cached_hot_fields"):
            cls._cached_hot_fields = _collect_hot_fields(cls)
        return cls._cached_hot_fields

    @staticmethod
    def _reload_validate_keys(overrides: dict, hot_fields: set[str]) -> None:
        for key in overrides:
            if key not in hot_fields:
                raise ValueError(f"禁止热更字段 '{key}'。可用：{sorted(hot_fields)}")

    def _reload_apply(self, overrides: dict, logger: "logging.Logger") -> BaseSettings:
        nested = _flatten_to_nested(overrides)
        merged = _dict_deep_merge(self.model_dump(), nested)
        try:
            validated = type(self).model_validate(merged)
        except Exception as e:
            logger.error("热更校验失败: %s", e)
            raise ValueError(f"热更校验失败: {e}") from e
        for key in overrides:
            self._set_nested(key, _get_nested(validated, key))
        return validated

    @staticmethod
    def _reload_log(real_changes: dict, logger: "logging.Logger") -> None:
        logged = {k: _log_mask(k, v) for k, v in real_changes.items()}
        logger.info("配置热更: %s", logged)

    def _reload_fire_single(self, real_changes: dict, logger: "logging.Logger") -> None:
        for key, value in real_changes.items():
            for cb in self._on_change_callbacks:
                try:
                    cb(key, value)
                except Exception:
                    logger.debug("on_change failed: %s", key, exc_info=True)

    def _reload_fire_batch(self, real_changes: dict, logger: "logging.Logger") -> None:
        if not self._on_batch_change_callbacks:
            return
        for cb in self._on_batch_change_callbacks:
            try:
                cb(dict(real_changes))
            except Exception:
                logger.debug("on_batch_change failed", exc_info=True)

    # ── 路径 ──
    base_dir: Path = Field(default=_BASE_DIR)
    data_dir: Path = Field(default=_DATA_DIR)
    db_path: Path = Field(default=_DATA_DIR / "memory.db")
    faiss_index_path: Path = Field(default=_DATA_DIR / "faiss.index")
    cooccurrence_path: Path = Field(default=_DATA_DIR / "cooccurrence.pkl")
    social_intuition_path: Path = Field(default=_DATA_DIR / "social_intuition.json")
    social_intuition_faiss_path: Path = Field(default=_DATA_DIR / "social_intuition.faiss")
    categories_dir: Path = Field(default=_DATA_DIR / "categories")
    resources_dir: Path = Field(default=_DATA_DIR / "resources")
    backup_dir: Path = Field(default=_DATA_DIR / "backup")
    desire_persist_path: Path = Field(default=_DATA_DIR / "desire_state.json")
    logs_dir: Path = Field(default=_BASE_DIR / "logs")
    persona_path: Path = Field(default=_BASE_DIR / "persona.txt")
    BASE_DIR: Path = Field(default=_BASE_DIR)
    DATA_DIR: Path = Field(default=_DATA_DIR)

    memory_limit_mb: int = Field(default=3072)
    cpu_background_max: int = Field(default=30)
    cpu_idle_max: int = Field(default=15)

    vector_dim: int = Field(default=1024)
    hnsw_m: int = Field(default=32)
    ef_construction: int = Field(default=200)
    ef_search: int = Field(default=64)
    bm25_k1: float = Field(default=1.5)
    bm25_b: float = Field(default=0.75)

    top_k: int = Field(default=20, json_schema_extra={"hot_reload": True})
    vector_distance_max: float = Field(default=1.0, json_schema_extra={"hot_reload": True})
    bm25_weight: float = Field(default=0.3, json_schema_extra={"hot_reload": True})
    vector_weight: float = Field(default=0.7, json_schema_extra={"hot_reload": True})

    cleanup_expired_cron: str = Field(default="0 3 * * *", json_schema_extra={"hot_reload": True})
    daily_summary_cron: str = Field(default="0 4 * * *", json_schema_extra={"hot_reload": True})
    backup_cron: str = Field(default="0 3 * * *", json_schema_extra={"hot_reload": True})
    light_maintenance_cron: str = Field(default="0 5 */3 * *", json_schema_extra={"hot_reload": True})
    full_maintenance_cron: str = Field(default="0 5 * * 0", json_schema_extra={"hot_reload": True})
    monthly_health_cron: str = Field(default="0 2 1 * *", json_schema_extra={"hot_reload": True})

    c_grade_expire_days: int = Field(default=7, json_schema_extra={"hot_reload": True})
    hot_data_days: int = Field(default=180, json_schema_extra={"hot_reload": True})
    promote_access_count: int = Field(default=5, json_schema_extra={"hot_reload": True})
    promote_window_days: int = Field(default=30, json_schema_extra={"hot_reload": True})
    light_promote_count: int = Field(default=3, json_schema_extra={"hot_reload": True})
    light_promote_window_days: int = Field(default=3, json_schema_extra={"hot_reload": True})
    demote_no_access_days: int = Field(default=90, json_schema_extra={"hot_reload": True})
    event_archive_days: int = Field(default=30, json_schema_extra={"hot_reload": True})
    resource_keep_days: int = Field(default=30, json_schema_extra={"hot_reload": True})

    dedup_threshold: float = Field(default=0.8, json_schema_extra={"hot_reload": True})
    minhash_num_perm: int = Field(default=128)
    dedup_window_days: int = Field(default=90, json_schema_extra={"hot_reload": True})

    write_max_retries: int = Field(default=3, json_schema_extra={"hot_reload": True})
    write_retry_delay_sec: int = Field(default=1, json_schema_extra={"hot_reload": True})
    backup_keep_days: int = Field(default=7, json_schema_extra={"hot_reload": True})

    embedding_model_name: str = Field(default="BAAI/bge-m3")
    query_instruction: str = Field(default="为这个句子生成表示以用于检索相关文章：")

    db_cache_size_mb: int = Field(default=2000)
    db_mmap_size_mb: int = Field(default=2048)
    db_busy_timeout_ms: int = Field(default=5000)
    db_items_per_page: int = Field(default=10000)

    emotion_time_window_days: int = Field(default=30, json_schema_extra={"hot_reload": True})
    emotion_max_per_fact: int = Field(default=3)
    emotion_cache_ttl: int = Field(default=60, json_schema_extra={"hot_reload": True})
    emotion_recent_days: int = Field(default=7, json_schema_extra={"hot_reload": True})
    emotion_recent_limit: int = Field(default=3, json_schema_extra={"hot_reload": True})
    emotion_older_days: int = Field(default=90, json_schema_extra={"hot_reload": True})
    emotion_older_limit: int = Field(default=2, json_schema_extra={"hot_reload": True})
    emotion_max_text_len: int = Field(default=200)
    emotion_archive_days: int = Field(default=365, json_schema_extra={"hot_reload": True})
    emotion_save_min_interval: int = Field(default=300)
    emotion_decay_rate: float = Field(default=0.02, json_schema_extra={"hot_reload": True})
    emotion_decay_min: float = Field(default=0.1, json_schema_extra={"hot_reload": True})
    emotion_weight_base: float = Field(default=0.1, json_schema_extra={"hot_reload": True})
    emotion_weight_max: float = Field(default=2.0, json_schema_extra={"hot_reload": True})
    emotion_similarity_threshold: float = Field(default=0.1, json_schema_extra={"hot_reload": True})
    emotion_match_boost: float = Field(default=1.5, json_schema_extra={"hot_reload": True})
    timestamp_future_tolerance: int = Field(default=3600)
    timestamp_min_valid: int = Field(default=1577836800)

    @property
    def emotion_type_weights(self) -> dict:
        return {
            "proud": 1.2, "angry": 1.15, "sad": 1.1, "happy": 1.1,
            "tender": 1.05, "anxious": 1.05, "longing": 1.0, "confused": 1.0,
            "neutral": 1.0,
        }

    @property
    def emotion_type_decay(self) -> dict:
        return {
            "proud": 0.015, "happy": 0.02, "sad": 0.02, "angry": 0.03,
            "anxious": 0.025, "neutral": 0.025,
        }

    reminder_ahead_seconds: int = Field(default=86400)
    reminder_max_text_len: int = Field(default=200)
    reminder_dedup_window: int = Field(default=86400)

    active_max_per_day: int = Field(default=2, json_schema_extra={"hot_reload": True})
    active_missing_hours_peak: int = Field(default=48, json_schema_extra={"hot_reload": True})
    active_base_probability: float = Field(default=0.25, json_schema_extra={"hot_reload": True})
    active_quiet_start_hour: int = Field(default=6, json_schema_extra={"hot_reload": True})
    active_daily_count_reset_hour: int = Field(default=0, json_schema_extra={"hot_reload": True})

    weather_show_num: bool = Field(default=False, json_schema_extra={"hot_reload": True})

    desire_max_delta_sec: int = Field(default=43200, json_schema_extra={"hot_reload": True})
    desire_global_max_out_msg_per_month: int = Field(default=2)
    desire_reach_out_max_per_week: int = Field(default=2)
    desire_quiet_start: int = Field(default=23, json_schema_extra={"hot_reload": True})
    desire_quiet_end: int = Field(default=7, json_schema_extra={"hot_reload": True})

    self_check_social: float = Field(default=0.5)
    self_check_mood: float = Field(default=0.6)
    self_check_sense_fresh: float = Field(default=0.0)
    self_check_energy: float = Field(default=0.5)
    self_check_is_late: bool = Field(default=False)

    deepseek_api_key: SecretStr = Field(default="")
    serper_api_key: SecretStr = Field(default="")
    qwen_api_key: SecretStr = Field(default="")
    qq_client_secret: SecretStr = Field(default="")
    amap_key: SecretStr = Field(default="")
    memory_api_token: SecretStr = Field(default="baijin-memory-ask-2026")

    qq_app_id: str = Field(default="")
    qq_user_id: str = Field(default="")
    qq_chat_name: str = Field(default="")
    gpt_sovits_url: str = Field(default="http://127.0.0.1:9880")

    # ── LLM 客户端参数（热更生效，infra/llm_client 读取）──
    llm_default_timeout: float = Field(default=45.0, json_schema_extra={"hot_reload": True})
    llm_fallback_timeout: float = Field(default=30.0, json_schema_extra={"hot_reload": True})
    llm_tool_timeout: float = Field(default=30.0, json_schema_extra={"hot_reload": True})
    llm_circuit_threshold: int = Field(default=3, json_schema_extra={"hot_reload": True})
    llm_circuit_cooldown: float = Field(default=30.0, json_schema_extra={"hot_reload": True})
    llm_retry_max: int = Field(default=3, json_schema_extra={"hot_reload": True})
    llm_retry_base: float = Field(default=1.0, json_schema_extra={"hot_reload": True})
    llm_retry_max_sec: float = Field(default=60.0, json_schema_extra={"hot_reload": True})
    llm_fallback_model: str = Field(default="qwen-turbo", json_schema_extra={"hot_reload": True})
    llm_fallback_max_tokens: int = Field(default=256, json_schema_extra={"hot_reload": True})
    llm_fallback_temperature: float = Field(default=0.7, json_schema_extra={"hot_reload": True})

    relative_days: dict = Field(default={
        "大前天": 3, "大后天": -3, "上前天": 3,
        "前天": 2, "后天": -2, "昨天": 1, "明天": -1, "今天": 0,
    })
    week_offsets: dict = Field(default={
        "上上周": 14, "下下周": -14, "上上上周": 21,
        "上周": 7, "下周": -7, "本周": 0, "这周": 0,
    })
    periods: dict = Field(default={
        "早上": (6, 9), "上午": (8, 12), "中午": (11, 13),
        "下午": (12, 18), "傍晚": (17, 19), "晚上": (18, 23),
        "凌晨": (0, 6), "半夜": (23, 3), "大清早": (5, 7),
    })

    @field_validator("vector_weight", "bm25_weight")
    @classmethod
    def _weight_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"权重必须在 [0, 1] 之间，当前值：{v}")
        return v

    @model_validator(mode="after")
    def _ensure_dirs(self) -> "Settings":
        for attr in ("data_dir", "logs_dir", "backup_dir", "categories_dir", "resources_dir"):
            path = getattr(self, attr, None)
            if path is not None:
                path.mkdir(parents=True, exist_ok=True)
        return self

    def _set_nested(self, key: str, value: object) -> None:
        if "." not in key:
            setattr(self, key, value)
            return
        parts = key.split(".")
        obj = self
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)


settings = Settings()
features = settings.features
