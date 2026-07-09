"""
BOM 解析模块
============
功能：读取 BOM 表（xlsx/csv），自动检测表头关键字并标准化，
      异常值过滤，输出统一格式的 DataFrame。
"""

import re
import pandas as pd
from pathlib import Path
from rich.console import Console

console = Console()

# 表头映射规则（不区分大小写）
HEADER_MAP = {
    "value":        ["值", "规格", "规格型号", "参数", "阻值", "容值", "感值", "物料", "物料描述", "型号"],
    "footprint":    ["封装", "footprint", "package", "尺寸", "焊盘"],
    "designator":   ["位号", "designator", "refdes", "ref des", "编号", "器件位号", "位号标识"],
    "qty":          ["数量", "qty", "quantity", "用量", "个数", "数量(pcs)", "pcs"],
    "mpn":          ["mpn", "制造商编号", "厂商型号", "厂家型号", "厂商料号", "mfr#", "制造商料号", "part#"],
}

# 需要过滤掉的无效行关键词（值列中出现这些词的行跳过）
INVALID_VALUE_PATTERNS = [
    r"^nc$", r"^dni$", r"^dnf$", r"^not\s*fit$", r"^不贴$", r"^空焊$",
    r"^0$", r"^0r$", r"^0ω$", r"^0ohm",
]


def _normalize_header(col: str) -> str:
    """将原始表头标准化为统一字段名（Value / Footprint / Designator / Qty / MPN / Other）。"""
    col_clean = col.strip()
    for standard, aliases in HEADER_MAP.items():
        for alias in aliases:
            if re.search(re.escape(alias), col_clean, re.IGNORECASE):
                return standard.title()  # 首字母大写
    return col_clean


def _merge_footprint_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    如果同时存在多个封装列（例如 "Footprint" 和 "封装" 都被解析出来），
    合并为同一列，删除重复的封装列。
    """
    fp_cols = [c for c in df.columns if c.lower() == "footprint"]
    if len(fp_cols) > 1:
        # 将后面的封装列内容合并到第一个，后面的删除
        base = fp_cols[0]
        for c in fp_cols[1:]:
            df[base] = df[base].fillna(df[c])
            df.drop(columns=[c], inplace=True)
    return df


def load_bom(file_path: str) -> pd.DataFrame:
    """
    加载 BOM 文件，自动检测表头，返回标准化 DataFrame。

    标准化后的列（按顺序）：
      - Designator（位号）
      - Value（值/规格）
      - Footprint（封装）
      - Qty（数量）
      - MPN（制造商编号，可选）
      - Other（原始表头保留）

    如果数据中没有 MPN 列，后续代码会自动使用 Value + Footprint 作为搜索关键词。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"BOM 文件不存在: {file_path}")

    # ---- 读取 ----
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        df = pd.read_excel(path, dtype=str)
    elif suffix == ".csv":
        df = pd.read_csv(path, dtype=str, encoding_errors="replace")
    else:
        raise ValueError(f"不支持的文件格式: {suffix}，仅支持 .xlsx 和 .csv")

    if df.empty:
        raise ValueError("BOM 文件为空")

    # ---- 标准化表头 ----
    df.rename(columns=lambda c: _normalize_header(c), inplace=True)

    # 合并重复的封装列
    df = _merge_footprint_columns(df)

    # ---- 确保核心列存在 ----
    required_cols = ["Value", "Footprint"]
    missing = [c for c in required_cols if c not in df.columns]
    # 如果缺少 Value 则用第一列兜底
    if "Value" not in df.columns:
        console.print("[yellow]⚠️ 未识别到「值/规格」列，将使用第一列作为 Value[/yellow]")
        df.rename(columns={df.columns[0]: "Value"}, inplace=True)
    # 如果缺少 Footprint 则尝试自动补 "N/A"
    if "Footprint" not in df.columns:
        console.print("[yellow]⚠️ 未识别到「封装/Footprint」列，将全部填 N/A[/yellow]")
        df["Footprint"] = "N/A"

    # ---- 去除完全无值的行 ----
    df.dropna(subset=["Value"], inplace=True)
    df["Value"] = df["Value"].astype(str).str.strip()
    df = df[df["Value"] != ""]  # 去掉空字符串行

    # ---- 过滤无效行 ----
    invalid_mask = df["Value"].str.lower().str.strip().isin(
        ["nc", "dni", "dnf", "not fit", "不贴", "空焊", "0", "0r", "0ω", "0ohm", ""]
    )
    if invalid_mask.any():
        console.print(f"[dim]过滤了 {invalid_mask.sum()} 行无效值（NC/DNI/不贴等）[/dim]")
        df = df[~invalid_mask].copy()

    # ---- 补全 Qty 列 ----
    if "Qty" not in df.columns:
        df["Qty"] = 1
        console.print("[yellow]⚠️ 未识别到「数量/Qty」列，默认每行数量为 1[/yellow]")
    else:
        df["Qty"] = pd.to_numeric(df["Qty"], errors="coerce").fillna(1).astype(int)

    # ---- 补全 Designator 列 ----
    if "Designator" not in df.columns:
        df["Designator"] = ""
        console.print("[yellow]⚠️ 未识别到「位号/Designator」列，将留空[/yellow]")

    # ---- 整理列顺序 ----
    final_cols = ["Designator", "Value", "Footprint", "Qty"]
    # 如果原始有 MPN 列则保留
    if "Mpn" in df.columns or "MPN" in df.columns:
        mpn_col = "Mpn" if "Mpn" in df.columns else "MPN"
        final_cols.append(mpn_col)
    # 保留剩余的原始列
    other_cols = [c for c in df.columns if c not in final_cols]
    df = df[final_cols + other_cols]

    console.print(f"[green]✅ BOM 解析完成: {len(df)} 行有效物料[/green]")
    return df


def get_search_keyword(row: pd.Series, mpn_col: str | None = None) -> str:
    """
    根据一行数据生成淘宝搜索关键词。

    规则：
      - 若有 MPN 列且值非空，优先使用 MPN
      - 否则使用 "Value Footprint" 拼接
    """
    if mpn_col and pd.notna(row.get(mpn_col)) and str(row[mpn_col]).strip():
        return str(row[mpn_col]).strip()
    value = str(row.get("Value", "")).strip()
    fp = str(row.get("Footprint", "")).strip()
    keyword = f"{value} {fp}" if fp and fp != "N/A" else value
    return keyword


def get_fallback_keyword(keyword: str) -> str:
    """
    无结果时的降级关键词：去掉封装尾缀。
    例如 "10kΩ 0603" -> "10kΩ"
    """
    parts = keyword.strip().split()
    if len(parts) > 1:
        return " ".join(parts[:-1])
    return keyword