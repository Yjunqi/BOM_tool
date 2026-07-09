#!/usr/bin/env python3
"""
BOM-Taobao-Filler 主入口
=========================
BOM 表淘宝自动比价、价格标注与链接填充工具。

用法:
    python main.py "你的BOM表路径.xlsx"
"""

import sys
import os
import time
import logging
import argparse
from pathlib import Path

import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress, TextColumn, BarColumn, TaskProgressColumn,
    TimeElapsedColumn,
)

from src.bom_parser import load_bom, get_search_keyword, get_fallback_keyword
from src.matcher import Matcher
from src.exporter import write_results
from src import cache_manager
from src.taobao_client import TaobaoItem, TaobaoSearcher

console = Console()


def setup_logging(log_level: str = "INFO"):
    """配置日志同时输出到文件和控制台（Rich 美化）。"""
    log_path = Path("run.log")
    log_level_num = getattr(logging, log_level.upper(), logging.INFO)

    # 根日志记录器
    logger = logging.getLogger()
    logger.setLevel(log_level_num)

    # 清除已有 handler
    logger.handlers.clear()

    # 文件日志（完整格式）
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    fh.setLevel(log_level_num)
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(file_fmt)
    logger.addHandler(fh)

    # 控制台日志（Rich 美化）
    rh = RichHandler(
        console=console,
        show_time=False,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rh.setLevel(log_level_num)
    logger.addHandler(rh)

    return logger


def load_config(config_path: str = "config.yaml") -> dict:
    """加载 YAML 配置文件。"""
    path = Path(config_path)
    if not path.exists():
        console.print(f"[yellow]⚠️ 配置文件 {config_path} 不存在，使用默认配置[/yellow]")
        return {
            "search_pages": 5,
            "strategy": "B",
            "min_price": 0.01,
            "blacklist": ["样品", "测试", "开发板", "拆机", "二手"],
            "timeout": 30,
            "cache_ttl_hours": 24,
            "log_level": "INFO",
        }

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    console.print(f"[green]✅ 配置文件已加载: {config_path}[/green]")
    return config if config else {}


def rebuild_items_from_cache(items_data: list[dict]) -> list[TaobaoItem]:
    """将缓存的字典列表还原为 TaobaoItem 对象列表。"""
    items = []
    for d in items_data:
        try:
            items.append(TaobaoItem(
                title=d.get("title", ""),
                price=d.get("price", 0.0),
                sales=d.get("sales", 0),
                shop=d.get("shop", ""),
                url=d.get("url", ""),
            ))
        except Exception:
            continue
    return items


def main():
    parser = argparse.ArgumentParser(
        description="BOM 表淘宝自动比价、价格标注与链接填充工具"
    )
    parser.add_argument("bom_path", help="BOM 表文件路径（.xlsx 或 .csv）")
    parser.add_argument("--config", default="config.yaml",
                        help="配置文件路径（默认: config.yaml）")
    args = parser.parse_args()

    # ---- 加载配置 ----
    config = load_config(args.config)
    setup_logging(config.get("log_level", "INFO"))
    logger = logging.getLogger("main")

    console.rule("[bold cyan]BOM-Taobao-Filler[/bold cyan]")
    console.print(f"📂 BOM 文件: {args.bom_path}")
    console.print(f"⚙️  策略: {'B - 综合评分' if config.get('strategy', 'B') == 'B' else 'A - 价格优先'}")
    console.print(f"📄 搜索页数: {config.get('search_pages', 5)}")

    # ---- 1. 解析 BOM ----
    console.rule("[bold]第1步: 解析 BOM 表[/bold]")
    df = load_bom(args.bom_path)
    if df.empty:
        console.print("[red]❌ BOM 表无有效数据，退出程序[/red]")
        sys.exit(1)

    # ---- 2. 准备搜索关键词 ----
    mpn_col = "MPN" if "MPN" in df.columns else ("Mpn" if "Mpn" in df.columns else None)
    keywords = []
    for _, row in df.iterrows():
        kw = get_search_keyword(row, mpn_col)
        keywords.append(kw)

    # ---- 3. 初始化 Matcher ----
    matcher = Matcher(config)

    # ---- 4. 搜索与匹配（优先检查缓存） ----
    console.rule("[bold]第2步: 搜索与匹配[/bold]")

    matched_results: list[dict] = []
    need_search_indices: list[int] = []  # 需要真正搜索的下标
    cached_items: dict[int, list[TaobaoItem]] = {}  # 下标 -> 缓存商品列表

    ttl_hours = config.get("cache_ttl_hours", 24)

    for idx, (_, row) in enumerate(df.iterrows()):
        value = str(row.get("Value", "")).strip()
        fp = str(row.get("Footprint", "")).strip()

        cached_data = cache_manager.get_from_cache(value, fp, ttl_hours)
        if cached_data is not None:
            items = rebuild_items_from_cache(cached_data)
            cached_items[idx] = items
        else:
            need_search_indices.append(idx)

    # 如果有需要搜索的，初始化 TaobaoSearcher 并批量搜索
    taobao_results: dict[str, list[TaobaoItem]] = {}

    if need_search_indices:
        # 收集需要搜索的关键词
        search_keywords: list[str] = []
        search_index_map: list[tuple[int, str, str]] = []  # (idx, keyword, footprint)
        for idx in need_search_indices:
            row = df.iloc[idx]
            kw = keywords[idx]
            fp = str(row.get("Footprint", "")).strip()
            search_keywords.append(kw)
            search_index_map.append((idx, kw, fp))

        # 去重（相同关键词只搜索一次）
        unique_keywords = list(dict.fromkeys(search_keywords))
        logger.info(f"需要实际搜索的关键词数: {len(unique_keywords)}"
                    f"（总物料 {len(df)} 行，缓存命中 {len(df) - len(need_search_indices)} 行）")

        # 批量搜索（带异常保护）
        try:
            searcher = TaobaoSearcher(config)
            taobao_results = searcher.search_batch(unique_keywords)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]👋 用户中断，程序将使用已获取的数据继续处理...[/bold yellow]")
        except Exception as e:
            logger.error(f"搜索过程出错: {e}")
            console.print(f"[bold red]❌ 搜索过程异常: {e}[/bold red]")
            console.print("[yellow]⚠️ 将使用已有结果继续处理...[/yellow]")

        # 将搜索结果写入缓存
        for kw, items in taobao_results.items():
            # 找到这个关键词对应的 value+footprint
            for row_idx, (_, row) in enumerate(df.iterrows()):
                value = str(row.get("Value", "")).strip()
                fp = str(row.get("Footprint", "")).strip()
                row_kw = keywords[row_idx]
                if row_kw == kw:
                    cache_manager.set_to_cache(value, fp, items)
                    # 只要写入一次就行（相同 kw 对应多个 row 时避免重复写）
                    break

        # 将搜索结果填入 cached_items
        for idx, kw, fp in search_index_map:
            if kw in taobao_results:
                cached_items[idx] = taobao_results[kw]
            else:
                cached_items[idx] = []

    # ---- 5. 执行匹配逻辑 ----
    console.rule("[bold]第3步: 执行匹配逻辑[/bold]")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[cyan]匹配进度...[/cyan]", total=len(df)
        )

        for idx, (_, row) in enumerate(df.iterrows()):
            value = str(row.get("Value", "")).strip()
            fp = str(row.get("Footprint", "")).strip()
            kw = keywords[idx]
            items = cached_items.get(idx, [])

            # 执行匹配
            best_item, status = matcher.match(items, kw, fp)

            # 若未找到且未使用降级，尝试降级关键词
            if status != "成功" and fp and fp.upper() != "N/A":
                fallback_kw = get_fallback_keyword(kw)
                if fallback_kw != kw:
                    logger.info(f"  原关键词 [{kw}] 未匹配成功，尝试降级: {fallback_kw}")
                    # 检查降级关键词是否在已有搜索结果中
                    fallback_items = taobao_results.get(fallback_kw, [])
                    if not fallback_items:
                        # 如果在已有搜索结果中没有，需要单独搜索
                        # 但在批量搜索模式中已经搜过了，这里只尝试在缓存中找
                        pass
                    best_item, status = matcher.match(
                        fallback_items or items, fallback_kw, fp, used_fallback=True
                    )

            matched_results.append({
                "keyword": kw,
                "status": status,
                "item": best_item,
            })

            progress.update(task, advance=1)

    # ---- 6. 统计 ----
    success_count = sum(1 for r in matched_results if r["status"] == "成功")
    fail_count = len(matched_results) - success_count
    console.print(f"\n[bold]📊 匹配结果: 成功 {success_count} 行, 失败 {fail_count} 行[/bold]")
    if fail_count > 0:
        fail_reasons = {}
        for r in matched_results:
            if r["status"] != "成功":
                fail_reasons[r["status"]] = fail_reasons.get(r["status"], 0) + 1
        for reason, cnt in fail_reasons.items():
            console.print(f"   [red]❌ {reason}: {cnt} 行[/red]")

    # ---- 7. 导出 Excel ----
    console.rule("[bold]第4步: 导出结果[/bold]")
    output_path = write_results(args.bom_path, df, matched_results)

    console.rule("[bold green]✅ 全部完成[/bold green]")
    console.print(f"📁 输出文件: {output_path}")
    console.print("💡 打开 Excel 文件，点击「淘宝链接（带价签）」列中的链接可直接跳转下单！")


if __name__ == "__main__":
    main()