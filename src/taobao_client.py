"""
淘宝自动化客户端模块
=====================
功能：
  - 登录态管理（首次扫码 -> 保存 storage_state，后续恢复）
  - 搜索关键词并翻页（最多 5 页）
  - 解析 HTML 提取商品信息（标题、价格、销量、店铺名、详情页链接）
  - 验证码/滑块检测处理
  - 人性化延迟与反爬规避

⚠️ 淘宝前端使用 CSS Modules（哈希类名），故不依赖固定类名选择器，
   改用 page.evaluate() 执行 JS 直接在浏览器中解析 DOM，
   通过属性匹配（href、data-*）定位商品元素，鲁棒性更高。
"""

import re
import json
import random
import time
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

TAOBAO_SEARCH_URL = "https://s.taobao.com/search?q={keyword}"


# ============================================================
# 商品数据模型
# ============================================================
class TaobaoItem:
    """单个淘宝商品的抓取结果"""

    def __init__(self, title: str, price: float, sales: int,
                 shop: str, url: str):
        self.title = title.strip() if title else ""
        self.price = price
        self.sales = sales
        self.shop = shop.strip() if shop else ""
        self.url = url.strip() if url else ""

    def __repr__(self):
        return (f"TaobaoItem(title={self.title[:20]}..., "
                f"price={self.price}, sales={self.sales}, shop={self.shop})")


# ============================================================
# 页面内 JS 解析器（核心）
# ============================================================
EXTRACT_ITEMS_JS = """
() => {
    const results = [];

    // ---- 策略1: 找所有商品链接 ----
    // 淘宝商品详情页链接典型格式: //item.taobao.com/item.htm?id=XXX
    // 或 //detail.tmall.com/item.htm?id=XXX
    const itemLinks = document.querySelectorAll('a[href*="item.htm"], a[href*="detail.tmall"]');
    const seen = new Set();

    itemLinks.forEach(link => {
        const href = link.href || link.getAttribute('href') || '';
        if (!href || seen.has(href)) return;
        seen.add(href);

        // 向上找商品卡片容器（最多3层）
        let card = link;
        for (let i = 0; i < 3; i++) {
            if (card.parentElement) card = card.parentElement;
            else break;
        }

        // --- 标题 ---
        let title = '';
        // 优先取 link 的 title 属性
        title = link.getAttribute('title') || '';
        if (!title) {
            // 取 link 内文本
            title = link.innerText || '';
        }
        if (!title) {
            // 从 card 中找标题
            const allText = card.innerText || '';
            // 取第一行有意义的文本
            const lines = allText.split('\\n').map(s => s.trim()).filter(s => s.length > 3);
            if (lines.length > 0) title = lines[0];
        }
        title = title.replace(/<[^>]+>/g, '').trim();
        if (!title || title.length < 2) return;

        // --- 价格 ---
        let price = 0;
        // 找包含 ¥ 或 ￥ 的文本
        const cardText = card.innerText || '';
        const priceMatches = cardText.match(/[¥￥](\d+\.?\d*)/);
        if (priceMatches) {
            price = parseFloat(priceMatches[1]);
        }
        if (!price || price <= 0) {
            // 尝试用正则提取数字+小数
            const nums = cardText.match(/(\d+\.\d{2})/);
            if (nums) price = parseFloat(nums[0]);
        }
        if (!price || price <= 0) return;

        // --- 销量 ---
        let sales = 0;
        // 常见格式: "xxx人付款" / "月销xxx" / "已售xxx"
        const salesMatch = cardText.match(/(\d+[,\d]*)\s*人付款/);
        if (salesMatch) {
            sales = parseInt(salesMatch[1].replace(/,/g, ''));
        }
        if (!sales) {
            const salesMatch2 = cardText.match(/月销\s*(\d+[,\d]*)/);
            if (salesMatch2) sales = parseInt(salesMatch2[1].replace(/,/g, ''));
        }
        if (!sales) {
            const salesMatch3 = cardText.match(/已售\s*(\d+[,\d]*)/);
            if (salesMatch3) sales = parseInt(salesMatch3[1].replace(/,/g, ''));
        }

        // --- 店铺名 ---
        let shop = '';
        // 找店铺链接附近的文本
        const shopLinks = card.querySelectorAll('a[href*="shop"], a[class*="shop"]');
        shopLinks.forEach(sl => {
            const t = (sl.innerText || '').trim();
            if (t && t.length > 1) shop = t;
        });
        if (!shop) {
            // 尝试从 card 文本中找店铺名
            const allATags = card.querySelectorAll('a');
            allATags.forEach(a => {
                const t = (a.innerText || '').trim();
                // 店铺名通常 2-10 个中文字符，不含"http"
                if (t && t.length >= 2 && t.length <= 20 && !t.includes('http')
                    && t !== title && !t.includes('¥') && !t.includes('￥')) {
                    shop = t;
                }
            });
        }

        // --- 订金/预售 过滤 ---
        if (/订金|预售|定金/.test(title)) return;

        // --- URL 补齐 ---
        let url = href;
        if (url.startsWith('//')) url = 'https:' + url;
        else if (!url.startsWith('http')) url = 'https://' + url;

        results.push({ title, price, sales, shop, url });
    });

    // ---- 策略2: 用更广泛的选择器兜底 ----
    // 如果策略1没找到，尝试找所有带价格的卡片
    if (results.length === 0) {
        const allCards = document.querySelectorAll('[class*="item"], [class*="card"], [class*="Card"], [data-index]');
        allCards.forEach(card => {
            const text = card.innerText || '';
            if (!text) return;

            // 必须有价格
            const priceMatch = text.match(/[¥￥](\d+\.?\d*)/);
            if (!priceMatch) return;
            const price = parseFloat(priceMatch[1]);
            if (!price || price <= 0) return;

            // 找链接
            const aTag = card.querySelector('a[href]');
            if (!aTag) return;
            let url = aTag.href || aTag.getAttribute('href') || '';
            if (!url || (!url.includes('item.htm') && !url.includes('detail'))) return;

            // 标题
            const title = aTag.getAttribute('title') || aTag.innerText || '';
            if (!title || title.length < 2) return;

            // 销量
            let sales = 0;
            const salesM = text.match(/(\d+[,\d]*)\s*人付款/);
            if (salesM) sales = parseInt(salesM[1].replace(/,/g, ''));

            if (/订金|预售|定金/.test(title)) return;

            if (url.startsWith('//')) url = 'https:' + url;
            else if (!url.startsWith('http')) url = 'https://' + url;

            results.push({ title: title.replace(/<[^>]+>/g, '').trim(), price, sales, shop: '', url });
        });
    }

    return results;
}
"""


# 检查页面是否是验证码/拦截页的 JS
CHECK_CAPTCHA_JS = """
() => {
    const text = document.body.innerText || '';
    const captchaKeywords = ['滑块', '请按住', '验证', '滑动', '旋转', '点击按钮',
                             'slide', 'verify', 'captcha', '请完成安全验证'];
    for (const kw of captchaKeywords) {
        if (text.includes(kw)) return true;
    }
    // 检查是否有常见的验证码元素
    if (document.querySelector('#nc_1_wrapper') ||
        document.querySelector('#captcha') ||
        document.querySelector('[class*="captcha"]') ||
        document.querySelector('[class*="verify"]') ||
        document.querySelector('[id*="slide"]')) {
        return true;
    }
    return false;
}
"""

# 检查是否是空结果页
CHECK_EMPTY_JS = """
() => {
    const text = document.body.innerText || '';
    // "没找到" 或 "没有找到相关商品"
    if (/没有找到|没找到|抱歉|找不到相关/.test(text)) return true;
    return false;
}
"""


# ============================================================
# 主客户端类
# ============================================================
class TaobaoSearcher:
    """Playwright 封装的淘宝搜索客户端"""

    def __init__(self, config: dict, auth_path: str = "cache/taobao_auth.json",
                 debug_dir: str = "cache"):
        self.config = config
        self.auth_path = Path(auth_path)
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    # ---------- 登录态管理 ----------

    def _ensure_login(self, playwright):
        """确保登录态有效，首次需扫码。"""
        timeout_ms = self.config.get("timeout", 30) * 1000
        browser = playwright.chromium.launch(
            headless=False,
            timeout=timeout_ms,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
            ],
        )

        if self.auth_path.exists():
            logger.info("📂 检测到已保存的登录态，尝试恢复...")
            self.context = browser.new_context(
                storage_state=str(self.auth_path),
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )
            self.page = self.context.new_page()
            self.page.goto("https://www.taobao.com", timeout=timeout_ms)
            self.page.wait_for_timeout(3000)
            if self._is_logged_in():
                logger.info("✅ 登录态有效，直接使用。")
                return
            else:
                logger.warning("⚠️ 登录态已失效，需要重新扫码。")
                self.context.close()
                self.context = None
                self.page = None

        # 首次登录
        self.context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        self.page = self.context.new_page()
        self.page.goto("https://www.taobao.com", timeout=timeout_ms)
        self._wait_and_handle_captcha()

        console.print("[bold yellow]⚠️ 请手动扫码登录淘宝，登录成功后按 Enter 键继续...[/bold yellow]")
        input()

        self.context.storage_state(path=str(self.auth_path))
        logger.info(f"✅ 登录态已保存至 {self.auth_path}")
        self.page.goto("https://www.taobao.com", timeout=timeout_ms)
        self.page.wait_for_timeout(2000)

    def _is_logged_in(self) -> bool:
        """判断是否已登录。"""
        try:
            url = self.page.url.lower()
            if "login" in url or "passport" in url:
                return False
            body = self.page.inner_text("body", timeout=3000)
            if "请登录" in body and "您好" not in body:
                return False
            return True
        except Exception:
            return False

    # ---------- 验证码/滑块处理 ----------

    def _wait_and_handle_captcha(self, timeout_sec: int = 5):
        """
        检测页面是否有滑块/验证码，持续等待直到用户处理完成或超时。
        返回 True 表示检测到验证码并等待处理，False 表示无需处理。
        """
        try:
            # 先等页面加载
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_sec * 1000)
        except Exception:
            pass

        try:
            has_captcha = self.page.evaluate(CHECK_CAPTCHA_JS)
            if has_captcha:
                # 保存截图方便排查
                self._save_debug_screenshot("captcha")
                console.print(
                    "[bold red]🛑 检测到滑块/验证码，请手动完成验证，"
                    "完成后按 Enter 键继续...[/bold red]"
                )
                input()
                self.page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
        return False

    def _save_debug_screenshot(self, name: str):
        """保存页面截图用于调试。"""
        try:
            ts = int(time.time())
            path = self.debug_dir / f"debug_{name}_{ts}.png"
            self.page.screenshot(path=str(path), full_page=True)
            logger.info(f"📸 调试截图已保存: {path}")
        except Exception as e:
            logger.debug(f"截图保存失败: {e}")

    # ---------- 搜索与提取 ----------

    def search(self, keyword: str) -> list[TaobaoItem]:
        """
        在淘宝搜索关键词，提取前 N 页商品数据。
        使用 page.evaluate() 在浏览器内解析，不依赖 CSS 类名。
        """
        items: list[TaobaoItem] = []
        total_pages = self.config.get("search_pages", 5)
        timeout_ms = self.config.get("timeout", 30) * 1000
        search_url = TAOBAO_SEARCH_URL.format(keyword=keyword)
        logger.info(f"🔍 正在搜索: {keyword}")

        # ---- 加载搜索页 ----
        retry_count = 0
        max_retries = 2
        while retry_count <= max_retries:
            try:
                self.page.goto(search_url, timeout=timeout_ms,
                               wait_until="domcontentloaded")
                break
            except Exception as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"❌ 搜索页面加载失败（已重试{max_retries}次）: {e}")
                    self._save_debug_screenshot("load_fail")
                    return items
                wait = random.uniform(5, 10)
                logger.warning(f"⚠️ 加载失败，{wait:.0f}秒后重试 ({retry_count}/{max_retries})...")
                time.sleep(wait)

        # ---- 等待页面稳定 ----
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self._random_delay(2, 4)

        # ---- 检查验证码 ----
        if self._wait_and_handle_captcha():
            # 处理完验证码后重新等待
            self._random_delay(2, 4)

        # ---- 检查是否空结果 ----
        try:
            is_empty = self.page.evaluate(CHECK_EMPTY_JS)
            if is_empty:
                logger.warning(f"⚠️ 关键词「{keyword}」无搜索结果")
                self._save_debug_screenshot("no_results")
                return items
        except Exception:
            pass

        # ---- 翻页抓取 ----
        for page_num in range(1, total_pages + 1):
            logger.info(f"📄 正在抓取第 {page_num} 页...")

            page_items = self._extract_items_js()
            items.extend(page_items)
            logger.info(f"   第 {page_num} 页提取到 {len(page_items)} 个商品")

            if page_num < total_pages:
                if not self._go_to_next_page():
                    logger.info("   已到达最后一页")
                    break
                self._random_delay(2, 5)

        logger.info(f"📊 搜索「{keyword}」共获取 {len(items)} 个商品")
        return items

    def _extract_items_js(self) -> list[TaobaoItem]:
        """
        使用 page.evaluate() 在浏览器环境中执行 JS 解析商品数据。
        不依赖固定 CSS 类名，通过属性匹配和文本分析提取数据。
        """
        try:
            raw_items = self.page.evaluate(EXTRACT_ITEMS_JS)
        except Exception as e:
            logger.warning(f"⚠️ JS 解析失败: {e}")
            return []

        if not raw_items or not isinstance(raw_items, list):
            logger.warning("⚠️ JS 解析未返回商品数据，页面结构可能已大改")
            self._save_debug_screenshot("parse_fail")
            return []

        items = []
        for raw in raw_items:
            try:
                item = TaobaoItem(
                    title=raw.get("title", ""),
                    price=float(raw.get("price", 0)),
                    sales=int(raw.get("sales", 0)),
                    shop=raw.get("shop", ""),
                    url=raw.get("url", ""),
                )
                if item.title and item.price > 0 and item.url:
                    items.append(item)
            except (ValueError, TypeError) as e:
                logger.debug(f"跳过解析异常的商品: {e}")
                continue

        return items

    def _go_to_next_page(self) -> bool:
        """
        翻到下一页。
        使用 JS 点击/滚动翻页，适配多种分页器样式。
        """
        # 先滚动模拟人类
        try:
            self.page.mouse.wheel(0, random.randint(300, 800))
            self._random_delay(1, 2)
        except Exception:
            pass

        # 用 JS 执行翻页（兼容性更好）
        click_next_js = """
        () => {
            // 多种方式定位"下一页"按钮
            const selectors = [
                'a:has-text("下一页")',
                'a.next',
                'a[class*="next"]',
                '.pagination a:last-child',
                'a[title="下一页"]',
                'button:has-text("下一页")',
                '.pagination .next',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) {  // 可见
                    el.click();
                    return true;
                }
            }
            // 尝试直接修改 URL 参数（部分新淘宝用 SPA 路由）
            try {
                const url = new URL(window.location.href);
                const currentPage = parseInt(url.searchParams.get('s')) || 0;
                url.searchParams.set('s', currentPage + 44);  // 淘宝每页44条
                window.location.href = url.toString();
                return true;
            } catch(e) {}
            return false;
        }
        """

        try:
            clicked = self.page.evaluate(click_next_js)
            if clicked:
                try:
                    self.page.wait_for_load_state("networkidle",
                                                  timeout=self.config.get("timeout", 30) * 1000)
                except Exception:
                    pass
                self._random_delay(2, 4)

                # 检查翻页后是否出现验证码
                self._wait_and_handle_captcha()
                return True
        except Exception:
            pass

        return False

    # ---------- 人性化延迟 ----------

    def _random_delay(self, min_s: float = 3.0, max_s: float = 8.0):
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)

    # ---------- 批量搜索 ----------

    def search_batch(self, keywords: list[str]) -> dict[str, list[TaobaoItem]]:
        """批量搜索多个关键词，返回 {keyword: [TaobaoItem]}"""
        with sync_playwright() as pw:
            self._ensure_login(pw)

            results = {}
            total = len(keywords)

            for idx, kw in enumerate(keywords, 1):
                console.print(f"[cyan]🔍 搜索进度 [{idx}/{total}]: {kw}[/cyan]")

                delay = random.uniform(3, 8)
                logger.info(f"⏳ 搜索前等待 {delay:.1f}s...")
                time.sleep(delay)

                items = self.search(kw)
                results[kw] = items

                time.sleep(random.uniform(1, 2))

            if self.context:
                self.context.close()

        return results