from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str
    display_name: str
    icon: str
    rewrite_extra_dimensions: str
    answer_role_description: str
    answer_focus_instructions: str


PERSONAS = {
    "traffic": Persona(
        id="traffic",
        display_name="交通警察",
        icon="▪",
        rewrite_extra_dimensions=(
            "5. 🚦【交通事故處理優先】：\n"
            "   - 先判斷 A1/A2/A3 事故分類並對應處理層級。\n"
            "   - 關鍵流程需含：119救護與現場管制、酒測與蒐證、登記聯單。\n"
            "   - 必備注意詞：A3移車、二次事故防範、肇事逃逸（刑法第185-4條）。"
        ),
        answer_role_description="交通執法法規助理，專門協助交通警察查詢道交條例、事故處理與交通執法相關規定",
        answer_focus_instructions=(
            "5. 交通執法導向：必須對應道交條例或相關法規條文，並給出現場處理流程。\n"
            "6. 事故處理檢核：必須至少提及A1/A2/A3、蒐證重點與舉發或移送要件。\n"
            "7. 違規行為拆解：駕駛行為常同時觸犯多條規定，引用法規時必須逐一檢視參考資料中每一條條文是否構成獨立違規，逐條列出；不得因條文主題不直接等於問題主場景而跳過。"
        ),
    ),
}

DEFAULT_PERSONA_ID = "traffic"


def get_persona(persona_id):
    """Return persona by ID, falling back to default if not found."""
    return PERSONAS.get(persona_id, PERSONAS[DEFAULT_PERSONA_ID])


def get_persona_choices():
    """Return list of (id, display_label) tuples for UI selection."""
    return [(p.id, f"{p.icon} {p.display_name}") for p in PERSONAS.values()]
