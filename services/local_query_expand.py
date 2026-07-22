import re

_EXPANSIONS = [
    (re.compile(r"大型重機"), "大型重型機車 比照小型汽車 第92條"),
    (re.compile(r"重機|機車"), "機車"),
    # 牌照/號牌 污損·塗抹·遮蔽·無法辨識 的「罰則」在處罰條例 §13(故意損毀或塗抹污損
    # 牌照使不能辨認牌號, 2400~4800) 與 §14第1項第2款(號牌污穢不洗刷或遮蔽, 300~600);
    # 道安規則 §11 只是行為規範(應洗刷清楚)無罰則。rewrite 逾時走 dict fallback 時,
    # 原本不展開 → 用原句搜尋造成罰則時上時下。展開罰則條文確保 §13/§14 穩定召回。
    # 展開時機在 decompose 之後(rewrite 階段), 走單次搜尋, 不會觸發 decompose 拆分。
    # 條號已依 CLAUDE.md Law Reference SOP 以 taiwan-legal-db 查證(2026-06-11)。
    (re.compile(r"(?=.*(牌照|號牌))(?=.*(損毀|變造|塗抹|污損|汙損|污穢|汙穢|遮蔽|無法辨識|不能辨))"),
     "損毀或變造汽車牌照 塗抹污損牌照 使不能辨認其牌號 第13條 號牌污穢不洗刷清楚或為他物遮蔽 第14條 罰鍰"),
    (re.compile(r"酒測|酒駕"), "酒精濃度檢測 第35條"),
    (re.compile(r"肇逃"), "肇事逃逸 刑法第185-4條 第62條"),
    (re.compile(r"臨時停車|臨停"), "汽車駕駛人臨時停車 第55條"),
    (re.compile(r"違停|違規停車"), "第55條 第56條"),
    (re.compile(r"紅單"), "舉發違反道路交通管理事件通知單"),
]

_SLOW_VEHICLE_LIGHT_PATTERN = re.compile(r"慢車|自行車")
_LIGHT_PATTERN = re.compile(r"燈號|信號燈|方向燈|警示燈|危險警告燈|大燈|未開燈|未打燈")
_DIRECTION_INDICATOR_PATTERN = re.compile(r"方向燈|未打燈|沒打燈")
_LANE_CHANGE_PATTERN = re.compile(r"變換車道|切換車道|轉彎")


def local_query_expand(question: str) -> str:
    """Fallback query expansion when rewrite times out.

    Appends relevant law article keywords for high-frequency traffic terms.
    Returns the original question unchanged if no keywords match.
    Order matters: more specific patterns (大型重機) must precede overlapping ones (重機).
    """
    extras = []
    for pattern, expansion in _EXPANSIONS:
        if pattern.search(question):
            extras.append(expansion)
    if _LIGHT_PATTERN.search(question):
        if _SLOW_VEHICLE_LIGHT_PATTERN.search(question):
            extras.append("慢車 夜間行車未開啟燈光 第73條")
        elif _DIRECTION_INDICATOR_PATTERN.search(question) and _LANE_CHANGE_PATTERN.search(question):
            extras.append("汽車駕駛人轉彎或變換車道 第48條 不依規定使用燈光 第42條")
        else:
            extras.append("不依規定使用燈光 第42條 第48條")
    if not extras:
        return question
    return f"{question} {' '.join(extras)}"
