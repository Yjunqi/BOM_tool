"""
缓存管理模块
============
功能：使用 pickle 缓存已搜索过的料号，减少重复请求。
"""

import pickle
import time
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from src.taobao_client import TaobaoItem

logger = logging.getLogger(__name__)
console = Console()

CACHE_FILE = Path("cache/search_cache.pkl")


def _make_cache_key(value: str, footprint: str) -> str:
    """
    生成缓存键。
    规则：Value + Footprint 拼接后取 MD5，避免文件系统兼容问题。
    """
    raw = f"{value}_{footprint}".strip().upper()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_cache() -> dict[str, dict]:
    """加载本地缓存文件。"""
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict):
            return data
    except (pickle.UnpicklingError, EOFError, FileNotFoundError) as e:
        logger.warning(f"缓存文件损坏，将重建: {e}")
    return {}


def save_cache(cache: dict[str, dict]):
    """将缓存字典写入本地文件。"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(cache, f)
    logger.debug(f"缓存已保存 ({len(cache)} 条)")


def get_from_cache(value: str, footprint: str,
                   ttl_hours: int = 24) -> Optional[list[dict]]:
    """
    从缓存中读取搜索结果。
    如果过期则返回 None。
    """
    key = _make_cache_key(value, footprint)
    cache = load_cache()
    entry = cache.get(key)
    if entry is None:
        return None

    # 检查过期
    timestamp = entry.get("timestamp", 0)
    age_hours = (time.time() - timestamp) / 3600
    if age_hours > ttl_hours:
        logger.info(f"  缓存已过期 ({age_hours:.1f}h > {ttl_hours}h)")
        del cache[key]
        save_cache(cache)
        return None

    items_data = entry.get("items", [])

    # 空结果不算命中（旧版本缓存的无结果数据，需要重新搜索）
    if not items_data:
        del cache[key]
        save_cache(cache)
        return None

    console.print(f"[dim]✅ 命中缓存: {value} {footprint} ({len(items_data)} 个商品)[/dim]")
    return items_data


def set_to_cache(value: str, footprint: str, items: list[TaobaoItem]):
    """
    将搜索结果写入缓存。
    items 中的 TaobaoItem 先序列化为 dict 再存储。
    """
    # 不缓存空结果
    if not items:
        return

    key = _make_cache_key(value, footprint)
    items_data = [
        {
            "title": item.title,
            "price": item.price,
            "sales": item.sales,
            "shop": item.shop,
            "url": item.url,
        }
        for item in items
    ]
    cache = load_cache()
    cache[key] = {
        "timestamp": time.time(),
        "items": items_data,
    }
    save_cache(cache)