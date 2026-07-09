"""
匹配算法模块
============
功能：执行决策树过滤与商品评分排序

流程：
  1. 硬性过滤（黑名单关键词、最低价格、封装匹配）
  2. 按选定策略评分（A=价格优先 / B=综合评分）
  3. 返回最优商品或标记失败原因
"""

import re
import logging
from typing import Optional

from src.taobao_client import TaobaoItem

logger = logging.getLogger(__name__)


# ============================================================
# 过滤与匹配
# ============================================================

class Matcher:
    """BOM 物料匹配器，封装过滤逻辑与策略评分。"""

    def __init__(self, config: dict):
        self.config = config
        self.strategy = config.get("strategy", "B").upper()
        self.blacklist = [kw.lower() for kw in config.get("blacklist", [])]
        self.min_price = config.get("min_price", 0.01)

    # ---------- 硬性过滤 ----------

    def _apply_hard_filters(self, items: list[TaobaoItem],
                            keyword: str,
                            footprint: str) -> tuple[list[TaobaoItem], str | None]:
        """
        对商品列表执行硬性过滤，返回 (通过过滤的商品列表, 失败原因)。

        过滤规则：
          1. 价格低于 min_price -> 刷单/废品链接
          2. 标题含黑名单关键词
          3. 封装不匹配
        """
        if not items:
            return [], None

        passed: list[TaobaoItem] = []

        for item in items:
            # ---- 1. 最低价格过滤 ----
            if item.price < self.min_price:
                continue

            # ---- 2. 黑名单关键词过滤 ----
            title_lower = item.title.lower()
            if any(kw in title_lower for kw in self.blacklist):
                continue
            # 特殊处理"包邮"：只有在"包邮"单独出现（而非词组一部分）时过滤
            if "包邮" in title_lower:
                # 简单规则：如果标题里"包邮"独立存在（前后是空格或标点），就过滤
                if re.search(r'(^|[\s,，。、.;；])包邮([\s,，。、.;；]|$)', title_lower):
                    continue

            # ---- 3. 封装匹配 ----
            if footprint and footprint.strip().upper() != "N/A":
                if not self._match_footprint(item.title, footprint):
                    continue

            passed.append(item)

        return passed, None

    @staticmethod
    def _match_footprint(title: str, expected_fp: str) -> bool:
        """
        检查商品标题是否包含预期的封装尺寸（忽略大小写）。

        匹配逻辑（只做数字提取，不做公制/英制转换）：
          1. 从 BOM 封装字段中提取纯数字部分（C0402 → 0402）
          2. 从标题中提取所有 3-4 位数字
          3. 两边数字匹配即成功

        注意：不做 0603↔1608 这类公制/英制转换，
              因为不同体系下的相同数字代表不同物理尺寸，
              转换可能导致买错封装无法焊接。
        """
        fp_raw = expected_fp.strip().upper()

        # 从 BOM 封装中提取纯数字（C0402 → 0402, R0603 → 0603）
        fp_nums = re.findall(r'\d{3,4}', fp_raw)
        fp_clean = fp_nums[0] if fp_nums else fp_raw

        title_upper = title.upper()

        # 策略1: 标题中的 3-4 位数字与 BOM 封装数字精确匹配
        title_fps = set(re.findall(r'\b\d{3,4}\b', title_upper))
        if fp_clean in title_fps:
            return True

        # 策略2: 子串匹配（标题含 "0402" 而 BOM 写 "C0402"）
        if fp_clean in title_upper:
            return True

        # 策略3: 完整 BOM 封装字符串匹配（BOM "C0402" 在标题中完整出现）
        if fp_raw in title_upper:
            return True

        # 如果期望封装太短（<=2字符）或为 N/A，宽松通过
        if len(fp_raw) <= 2:
            return True

        return False

    # ---------- 评分策略 ----------

    def _score_by_strategy(self, items: list[TaobaoItem]) -> list[TaobaoItem]:
        """
        按选定策略对商品评分并排序（降序，最优在前）。

        策略 A（价格优先）：
          过滤后取价格最低的 3 个，再从中选月销量最高的。

        策略 B（综合评分）：
          综合得分 = (商品销量 / 本页最高销量) * 0.6 + (本页最低价 / 商品价格) * 0.4
        """
        if not items:
            return items

        if self.strategy == "A":
            return self._strategy_a(items)
        else:
            return self._strategy_b(items)

    def _strategy_a(self, items: list[TaobaoItem]) -> list[TaobaoItem]:
        """价格优先策略"""
        # 按价格升序取前 3
        sorted_by_price = sorted(items, key=lambda x: x.price)
        top3 = sorted_by_price[:3]
        # 再按销量降序排序
        top3_sorted = sorted(top3, key=lambda x: x.sales, reverse=True)
        return top3_sorted

    def _strategy_b(self, items: list[TaobaoItem]) -> list[TaobaoItem]:
        """综合评分策略"""
        if not items:
            return items

        max_sales = max(item.sales for item in items)
        min_price = min(item.price for item in items)

        # 防止除零
        max_sales = max(max_sales, 1)
        min_price = max(min_price, 0.001)

        scored_items = []
        for item in items:
            sales_score = (item.sales / max_sales) * 0.6
            price_score = (min_price / item.price) * 0.4
            total_score = sales_score + price_score
            scored_items.append((total_score, item))

        # 按综合得分降序排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        logger.debug(f"  策略B评分: 最高分={scored_items[0][0]:.4f}, "
                     f"商品={scored_items[0][1].title[:20]}")
        return [item for _, item in scored_items]

    # ---------- 主匹配入口 ----------

    def match(self, items: list[TaobaoItem],
              keyword: str,
              footprint: str,
              used_fallback: bool = False) -> tuple[Optional[TaobaoItem], str]:
        """
        对搜索到的商品列表执行完整的匹配流程。

        参数:
          items: 淘宝搜索返回的商品列表
          keyword: 搜索关键词
          footprint: 期望的封装
          used_fallback: 是否已使用过降级关键词

        返回:
          (最优商品 or None, 状态标记)
          状态标记: "成功" / "未找到商品" / "封装不匹配" / "价格异常"
        """
        # ---- 无结果 ----
        if not items:
            return None, "未找到商品"

        # ---- 硬性过滤 ----
        passed, _ = self._apply_hard_filters(items, keyword, footprint)
        if not passed:
            # 判断失败原因
            if any(not self._match_footprint(item.title, footprint)
                   for item in items if item.price >= self.min_price):
                return None, "封装不匹配"
            if all(item.price < self.min_price for item in items):
                return None, "价格异常"
            return None, "未找到商品"

        # ---- 策略评分 ----
        ranked = self._score_by_strategy(passed)

        # 取最优商品
        best = ranked[0]

        # 额外检查：最优商品的价格是否合理
        if best.price < self.min_price:
            return None, "价格异常"

        return best, "成功"