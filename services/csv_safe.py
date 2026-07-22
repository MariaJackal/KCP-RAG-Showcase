"""CSV/試算表公式注入防護。

匯出檔以 utf-8-sig 編碼供 Excel/Calc 直接開啟，若使用者可控的儲存格
以 = + - @ Tab CR 開頭會被當成公式執行（CWE-1236）。寫入前以單引號前綴
中和，僅作用於危險開頭字元，正常內容不受影響。冪等。
"""

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _TRIGGERS:
        return "'" + s
    return s
