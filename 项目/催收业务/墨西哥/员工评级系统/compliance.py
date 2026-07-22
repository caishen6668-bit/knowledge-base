"""
合规模块 — 预留（V2 当前版本不实现）。

以后接飞书知识库，按违规等级扣分（一般 -1 / 中度 -5 / 严重 -10，合规分 = 10 - 扣分，下限 0）。
届时在 rating_engine.combine() 中合并 合规分，并加入综合评分与导出列。
"""


def fetch_compliance(*args, **kwargs):
    """占位：以后接飞书知识库返回各员工合规扣分。当前返回空。"""
    raise NotImplementedError("合规模块预留，当前版本不实现")
