"""京东自营周转补货计算模块。

核心逻辑：
- 按(上级B配送中心名称, SKU)分组聚合
- 日销7+14 = 近7日/7×50% + 近14日/14×50%
- 补货数量 = 日销×周转天数 - (C仓+B仓+采购未到库)
- 补货箱数 = ceil(max(补货数量, 0) / 箱规)
- 修正补货数量 = 补货箱数 × 箱规
"""

import re
import math

import pandas as pd
from loguru import logger

# ============================================================
# 内置SKU映射表（38条，从京东自营周转匹配表提取）
# ============================================================
SKU_MAPPING = {
    # 呼吸系列
    "100197258076": {"商品简称": "呼吸【纸尿裤】NB码36片", "系列": "呼吸系列", "箱规": 8},
    "100197258008": {"商品简称": "呼吸【纸尿裤】S码32片", "系列": "呼吸系列", "箱规": 17},
    "100197258070": {"商品简称": "呼吸【纸尿裤】M码28片", "系列": "呼吸系列", "箱规": 17},
    "100197258416": {"商品简称": "呼吸【纸尿裤】L码24片", "系列": "呼吸系列", "箱规": 16},
    "100197258216": {"商品简称": "呼吸【纸尿裤】XL码20片", "系列": "呼吸系列", "箱规": 20},
    "100197258004": {"商品简称": "呼吸【拉拉裤】L码22片", "系列": "呼吸系列", "箱规": 16},
    "100155286195": {"商品简称": "呼吸【拉拉裤】XL码20片", "系列": "呼吸系列", "箱规": 16},
    "100155286233": {"商品简称": "呼吸【拉拉裤】XXL码18片", "系列": "呼吸系列", "箱规": 16},
    "100197258444": {"商品简称": "呼吸【拉拉裤】3XL码16片", "系列": "呼吸系列", "箱规": 16},
    # 呼吸系列-试用装
    "100197258254": {"商品简称": "呼吸【纸尿裤】NB 5片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100197258050": {"商品简称": "呼吸【纸尿裤】S5片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100155286381": {"商品简称": "呼吸【纸尿裤】M 5片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100155286227": {"商品简称": "呼吸【纸尿裤】L 5片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100155286347": {"商品简称": "呼吸【纸尿裤】XL 5片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100155286399": {"商品简称": "呼吸【拉拉裤】L4片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100155286237": {"商品简称": "呼吸【拉拉裤】XL4片", "系列": "呼吸系列-试用装", "箱规": 100},
    "100197257920": {"商品简称": "呼吸【拉拉裤】XXL 4片", "系列": "呼吸系列-试用装", "箱规": 100},
    # 清风系列
    "100224890144": {"商品简称": "清风【纸尿裤】NB码36片", "系列": "清风系列", "箱规": 8},
    "100224890126": {"商品简称": "清风【纸尿裤】S码64片", "系列": "清风系列", "箱规": 4},
    "100224890064": {"商品简称": "清风【纸尿裤】M码56片", "系列": "清风系列", "箱规": 4},
    "100173705535": {"商品简称": "清风【纸尿裤】L码48片", "系列": "清风系列", "箱规": 4},
    "100224890102": {"商品简称": "清风【纸尿裤】XL码44片", "系列": "清风系列", "箱规": 4},
    "100224890108": {"商品简称": "清风【拉拉裤】L码42片", "系列": "清风系列", "箱规": 4},
    "100173705541": {"商品简称": "清风【拉拉裤】XL码40片", "系列": "清风系列", "箱规": 4},
    "100224890142": {"商品简称": "清风【拉拉裤】XXL码38片", "系列": "清风系列", "箱规": 4},
    "100173705539": {"商品简称": "清风【拉拉裤】XXXL码36片", "系列": "清风系列", "箱规": 4},
    "100224890100": {"商品简称": "清风【拉拉裤】XXXXL码36片", "系列": "清风系列", "箱规": 4},
    # 蓝蓝裤系列
    "100216075096": {"商品简称": "蓝蓝裤【纸尿裤】NB码36片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100216075012": {"商品简称": "蓝蓝裤【纸尿裤】S码32片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100216075086": {"商品简称": "蓝蓝裤【纸尿裤】M码28片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100216075070": {"商品简称": "蓝蓝裤【纸尿裤】L码24片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822275": {"商品简称": "蓝蓝裤【纸尿裤】XL码20片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822267": {"商品简称": "蓝蓝裤【纸尿裤】XXL码40片", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822283": {"商品简称": "蓝蓝裤【拉拉裤】L码22片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100216075048": {"商品简称": "蓝蓝裤【拉拉裤】XL码40片", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822311": {"商品简称": "蓝蓝裤【拉拉裤】XXL码36片", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822231": {"商品简称": "蓝蓝裤【拉拉裤】XXXL码16片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
    "100167822243": {"商品简称": "蓝蓝裤【拉拉裤】XXXXL码16片*2包", "系列": "蓝蓝裤系列", "箱规": 2},
}

# ============================================================
# 总表-分SKU 固定18列列名
TOTAL_SHEET_HEADERS = [
    "SKUID", "上级B配送中心名称", "配送中心", "系列", "商品简称",
    "码数排序", "箱规", "7天销售", "14天销售",
    "C仓库存", "B仓库存", "采购未到库",
    "日销7+14", "补货数量", "补货箱数", "修正补货数量",
    "实时周转天数", "周转天数要求",
]

# 平台采购下单表 固定4列列名
ORDER_SHEET_HEADERS = ["SKU", "采购需求数量", "配送中心", "商品全称"]

# ============================================================
# 列名别名配置
# ============================================================
REQUIRED_FIELDS = {
    "SKU": ["SKU", "sku", "Sku", "商品SKU", "sku编码", "SKU编码", "商品编码"],
    "C仓库存": ["现货库存", "可用库存", "库存件数", "C仓库存", "当前库存", "库存数量"],
    "B仓库存": ["B仓京东可用库存", "B仓库存", "B库存", "前置仓库存", "门店库存", "分仓库存"],
    "采购未到库": ["采购未到货", "在途", "在途库存", "采购在途", "在途数量"],
    "近7日出库": ["近7日出库商品件数", "近7日出库", "近7天出库", "7天出库", "近7日销量", "7天销量", "近7天销量"],
    "近14日出库": ["近14日出库商品件数", "近14日出库", "近14天出库", "14天出库", "近14日销量", "14天销量", "近14天销量"],
    "商品名称": ["商品名称", "品名", "商品全称", "商品名", "产品名称"],
    "RDC": ["RDC", "rdc", "Rdc"],
}


def _clean(s: str) -> str:
    """清理字符串：全角转半角，去空白和标点，小写。"""
    s = str(s).strip()
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("【", "[").replace("】", "]")
    s = re.sub(r"[\s、。，]", "", s)
    return s.lower()


def _clean_sku(val) -> str:
    """规范化 SKU：去除浮点数 .0 后缀。"""
    s = str(val).strip()
    if s.endswith(".0") and len(s) > 2:
        s = s[:-2]
    return s


def match_columns(df: pd.DataFrame) -> dict[str, str]:
    """将 DataFrame 列名匹配到标准字段名，返回 {标准名: 实际列名}。"""
    col_map = {}
    missing = []
    df_cols = {_clean(c): c for c in df.columns}

    for std, aliases in REQUIRED_FIELDS.items():
        for alias in aliases:
            key = _clean(alias)
            if key in df_cols:
                col_map[std] = df_cols[key]
                break
        else:
            missing.append(f"  - {std}（支持别名：{', '.join(aliases)}）")

    if missing:
        raise ValueError(
            f"底表缺失必填字段：\n" + "\n".join(missing)
            + f"\n\n当前文件列名：{list(df.columns)}"
        )

    logger.info(f"列名匹配完成, 匹配 {len(col_map)} 个字段")
    return col_map


def process(df: pd.DataFrame, turnover_days: int = 50) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    执行周转补货计算。

    参数:
        df: 清洗后的底表 DataFrame
        turnover_days: 周转天数要求，默认 50 天

    返回 (底表新_df, 总表_df, 采购下单表_df)
    """
    # 1. 列名匹配
    col_map = match_columns(df)

    # 2. 构建 RDC → 上级B配送中心名称 映射
    rdc_to_wh: dict[str, str] = {}
    for _, row in df.iterrows():
        rdc_raw = row.get(col_map["RDC"], "")
        rdc = str(rdc_raw).strip() if pd.notna(rdc_raw) else ""
        wh_raw = row.get("上级B配送中心名称", "")
        wh = str(wh_raw).strip() if pd.notna(wh_raw) else ""
        if rdc and rdc != "nan" and wh and wh != "nan":
            rdc_to_wh[rdc] = wh

    # 3. 按 (上级B配送中心名称, SKU) 分组聚合
    groups: dict[tuple[str, str], dict] = {}
    for _, row in df.iterrows():
        sku = _clean_sku(row.get(col_map["SKU"], ""))
        if not sku or sku == "nan":
            continue

        rdc_raw = row.get(col_map["RDC"], "")
        rdc = str(rdc_raw).strip() if pd.notna(rdc_raw) else ""
        if not rdc or rdc == "nan":
            continue

        # 上游配送中心名称：优先用映射表，其次用RDC本身
        wh_b = rdc_to_wh.get(rdc, rdc)

        # 只取有上级B配送中心名称对应的数据
        if not wh_b or wh_b == "nan":
            continue

        key = (wh_b, sku)
        if key not in groups:
            groups[key] = {
                "7天销售": 0.0,
                "14天销售": 0.0,
                "C仓库存": 0.0,
                "B仓库存": 0.0,
                "采购未到库": 0.0,
            }

        g = groups[key]

        def _f(field: str) -> float:
            val = row.get(col_map.get(field, ""), 0)
            try:
                return float(val) if pd.notna(val) else 0.0
            except (ValueError, TypeError):
                return 0.0

        g["7天销售"] += _f("近7日出库")
        g["14天销售"] += _f("近14日出库")
        g["C仓库存"] += _f("C仓库存")
        g["B仓库存"] += _f("B仓库存")
        g["采购未到库"] += _f("采购未到库")

    logger.info(f"分组聚合完成, 共 {len(groups)} 组")

    # 4. 生成总表-分SKU 和 平台采购下单表
    total_rows = []
    order_rows = []

    for (wh_b, sku), g in groups.items():
        mapping = SKU_MAPPING.get(sku, {})
        goods_short = mapping.get("商品简称", "")
        series = mapping.get("系列", "")
        box_spec = mapping.get("箱规", 1)

        sales_7 = g["7天销售"]
        sales_14 = g["14天销售"]
        stock_c = g["C仓库存"]
        stock_b = g["B仓库存"]
        on_way = g["采购未到库"]

        # 配送中心：从上级B配送中心名称去"补货B"
        dc_name = wh_b.replace("补货B", "")

        daily_sales = (sales_7 / 7.0 * 0.5) + (sales_14 / 14.0 * 0.5)
        if daily_sales <= 0:
            daily_sales = 0.001

        total_stock = stock_c + stock_b + on_way
        replenish_raw = daily_sales * turnover_days - total_stock
        replenish_num = max(0, math.floor(replenish_raw))
        replenish_box = math.ceil(replenish_num / box_spec) if replenish_num > 0 else 0
        final_replenish = replenish_box * box_spec
        current_turnover = round(total_stock / daily_sales, 2)

        total_rows.append({
            "SKUID": sku,
            "上级B配送中心名称": wh_b,
            "配送中心": dc_name,
            "系列": series,
            "商品简称": goods_short,
            "码数排序": "",
            "箱规": box_spec,
            "7天销售": sales_7,
            "14天销售": sales_14,
            "C仓库存": stock_c,
            "B仓库存": stock_b,
            "采购未到库": on_way,
            "日销7+14": round(daily_sales, 4),
            "补货数量": round(replenish_raw, 2),
            "补货箱数": replenish_box,
            "修正补货数量": final_replenish,
            "实时周转天数": current_turnover,
            "周转天数要求": turnover_days,
        })

        if final_replenish > 0:
            order_rows.append({
                "SKU": sku,
                "采购需求数量": final_replenish,
                "配送中心": dc_name,
                "商品全称": goods_short,
            })

    df_total = pd.DataFrame(total_rows, columns=TOTAL_SHEET_HEADERS) if total_rows else pd.DataFrame(columns=TOTAL_SHEET_HEADERS)
    df_order = pd.DataFrame(order_rows, columns=ORDER_SHEET_HEADERS) if order_rows else pd.DataFrame(columns=ORDER_SHEET_HEADERS)

    logger.info(f"底表新: {len(df)} 行, 总表-分SKU: {len(df_total)} 行, 平台采购下单表: {len(df_order)} 行")
    return df, df_total, df_order
