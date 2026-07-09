# BOM-Taobao-Filler

**BOM 表淘宝自动比价、价格标注与链接填充工具**

## 功能概述

本工具用于 PCB 项目的 BOM 表（物料清单）自动化处理：

1. **读取** BOM 表（`.xlsx` / `.csv`），自动识别表头
2. **搜索** 淘宝中对应的元器件（使用 Playwright 模拟浏览器自动搜索）
3. **匹配** 最优商品（策略 B：综合评分 = 销量×0.6 + 价格×0.4）
4. **填充** 将商品价格、店铺名、带价签的淘宝链接写入原文件同目录下的 `[文件名]_filled.xlsx`

---

## 环境要求

- **Python 3.10+**
- **Windows / macOS / Linux**（淘宝前端在 Windows 下测试最充分）

---

## 快速开始

### 1. 创建虚拟环境（推荐）

```bash
# 在项目根目录下执行
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器

```bash
playwright install chromium
```

> ⚠️ 此步骤会下载约 200MB 的 Chromium 浏览器，请确保网络畅通。

### 4. 运行

```bash
python main.py "你的BOM表路径.xlsx"
```

首次运行会打开浏览器窗口，**请手动扫码登录淘宝**。登录成功后按 Enter 键继续。

第二次运行会自动加载缓存的登录态和搜索结果，速度会快很多。

---

## 使用说明

### 命令行参数

```bash
python main.py <BOM表路径> [--config 配置文件路径]
```

示例：
```bash
python main.py ./BOM/project1_bom.xlsx
python main.py ./BOM/project1_bom.xlsx --config my_config.yaml
```

### 支持的 BOM 表头

工具会自动识别以下表头（不区分大小写、支持中英文）：

| 标准字段 | 可识别的表头关键词 |
|:---------|:-------------------|
| **Value** | `值`、`规格`、`规格型号`、`参数`、`阻值`、`容值`、`物料描述`、`型号` |
| **Footprint** | `封装`、`Footprint`、`Package`、`尺寸` |
| **Designator** | `位号`、`Designator`、`RefDes`、`编号` |
| **Qty** | `数量`、`Qty`、`Quantity`、`用量` |
| **MPN** | `MPN`、`制造商编号`、`厂商型号`、`Part#`（可选列） |

### 输出文件

生成在 BOM 文件同目录下，文件名为 `[原文件名]_filled.xlsx`，包含 4 个新增列：

| 列名 | 说明 |
|:----|:-----|
| **匹配价格(元)** | 两位小数数字格式，区间价只取最低值 |
| **匹配店铺名** | 淘宝店铺全称 |
| **淘宝链接（带价签）** | 含 `=HYPERLINK()` 公式，显示为 `点击查看 ¥0.85`，点击可跳转 |
| **匹配状态** | `成功` / `未找到商品` / `封装不匹配` / `价格异常` |

---

## 配置说明

编辑 `config.yaml` 可调节以下参数：

```yaml
search_pages: 5               # 淘宝搜索页数（1-5）
strategy: "B"                  # 匹配策略: A=价格优先, B=综合评分
min_price: 0.01                # 最低有效价格，低于此值过滤
blacklist:                     # 黑名单关键词
  - "样品"
  - "测试"
  - "开发板"
  - "拆机"
  - "二手"
timeout: 30                    # 页面加载超时（秒）
cache_ttl_hours: 24            # 搜索结果缓存有效期（小时）
log_level: "INFO"              # 日志级别: DEBUG / INFO / WARNING / ERROR
```

---

## 策略说明

### 策略 A — 价格优先
1. 硬性过滤后取**价格最低**的前 3 个商品
2. 再从中挑选**月销量最高**的那个

### 策略 B — 综合评分（默认推荐）
```
综合得分 = (商品销量 / 本页最高销量) × 0.6 + (本页最低价 / 商品价格) × 0.4
```
选取得分最高的商品，兼顾销量与价格。

---

## 项目结构

```
bom_tool/
├── main.py                    # 程序入口
├── config.yaml                # 配置文件
├── requirements.txt           # 依赖清单
├── README.md                  # 本文件
├── src/
│   ├── __init__.py
│   ├── bom_parser.py          # BOM 表解析与标准化
│   ├── taobao_client.py       # 淘宝自动化（登录/搜索/翻页/解析）
│   ├── matcher.py             # 匹配算法（过滤/评分/决策）
│   ├── cache_manager.py       # 搜索结果缓存管理
│   └── exporter.py            # Excel 结果导出（HYPERLINK 公式）
├── cache/                     # 运行时生成
│   ├── taobao_auth.json       # 淘宝登录态
│   └── search_cache.pkl       # 搜索结果缓存
└── run.log                    # 运行日志
```
## APP 
  1. 下载整个 dist/ 
  2. 首次需在 dist/ 目录下执行一次：python -m playwright install chromium
  3. 之后双击 run.bat，把 BOM 表拖进去按回车
---


## 常见问题

### Q: 淘宝搜索无结果？
- 检查网络连接
- 检查关键词是否过于复杂（工具会自动降级，砍掉封装尾缀重试）
- 检查是否已登录淘宝

### Q: 遇到滑块验证码？
程序检测到滑块/验证码时会暂停，请在浏览器中手动完成验证，然后按 Enter 键继续。

### Q: 生成的 Excel 链接点不开？
确保输出的 `=HYPERLINK()` 公式未被 Excel 安全设置拦截。在 Excel 中可能需要启用超链接功能。

### Q: 淘宝页面改版了，抓取不到数据？
`taobao_client.py` 中已内置多个 CSS 选择器和 XPath 降级方案。若仍失效，请检查淘宝页面结构，更新 `ITEM_SELECTORS` 等选择器。

---

## 注意事项

- 本工具仅供个人学习与合法用途使用
- 请合理控制搜索频率，避免对淘宝服务器造成压力
- 淘宝页面结构可能随时变化，若抓取失败请更新选择器
- 缓存有效期为 24 小时（可配置），请根据实际需求调整
