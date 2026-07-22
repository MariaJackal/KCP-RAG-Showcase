import re

from rag_logic import normalize_intent


LOCAL_BLOCK_PATTERNS = [
    re.compile(r"^(hi|hello|hey)$"),
    re.compile(r"^(你好|哈囉|早安|午安|晚安|謝謝|謝謝你|感謝)$"),
    re.compile(r".*(畫圖|畫一張圖|生成圖片|做圖|寫程式|寫代碼|天氣|講笑話).*"),
    re.compile(r".*畫.*圖.*"),
    re.compile(r".*(python|javascript|java|c\+\+|程式|程式碼|code|coding).*"),
]


def semantic_router(user_query, rewriter_model):
    """只區分 SEARCH/BLOCK，優先使用本機規則避免不必要 API 呼叫。"""
    normalized = (user_query or "").strip().lower()
    if any(pattern.search(normalized) for pattern in LOCAL_BLOCK_PATTERNS):
        return "BLOCK"

    prompt = f"""
    <任務>
    你是警用法規系統的「意圖分類器」。請判斷使用者的問題，並只回傳以下兩個分類標籤之一：

    1. SEARCH
       - 使用者詢問法律、法規、罰則、警察作業程序、案件處理方式。
       - 即使問題很簡短（如「無人機」、「酒駕」），只要涉及專業知識，都算此類。

    2. BLOCK
       - 使用者打招呼（你好、謝謝、早安）。
       - 使用者閒聊、問天氣、要求畫圖、寫程式。
       - 任何與「查詢法規」無關的輸入。

    <使用者問題>{user_query}</使用者問題>
    <輸出>(只輸出 SEARCH 或 BLOCK，不要有其他廢話)
    """

    try:
        response = rewriter_model.generate_text(prompt, temperature=0.0)
        return normalize_intent(response.text)
    except Exception:
        return "BLOCK"
