import re
import time


def normalize_intent(raw_text):
    """Normalize model output to SEARCH/BLOCK with safe default."""
    category = (raw_text or "").strip().upper()
    return category if category in {"SEARCH", "BLOCK"} else "SEARCH"


def rewrite_with_retry(model, prompt, fallback_text, retries=3, timeout=60, sleep_seconds=0.5, sleep_fn=time.sleep):
    """Call rewrite model with bounded retries; return fallback when exhausted.

    Accepts both ModelClient (generate_text) and legacy GenerativeModel (generate_content).
    """
    for attempt in range(retries):
        try:
            if hasattr(model, "generate_text"):
                response = model.generate_text(prompt, temperature=0.4)
                return response.text or fallback_text
            else:
                response = model.generate_content(
                    prompt,
                    generation_config={"temperature": 0.4},
                    request_options={"timeout": timeout},
                )
                return (response.text or "").strip() or fallback_text
        except Exception:
            if attempt < retries - 1:
                sleep_fn(sleep_seconds * (2 ** attempt))
                continue
            return fallback_text


def mapping_get(mapping, key, default=None):
    """Read from dict-like protobuf wrappers that may not implement .get()."""
    if mapping is None:
        return default

    getter = getattr(mapping, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            pass

    try:
        if key in mapping:
            return mapping[key]
    except Exception:
        pass

    try:
        return mapping[key]
    except Exception:
        return default


def parse_related_result_ids(decision_text, max_results):
    """Extract valid result IDs from model decision output."""
    decision = (decision_text or "").strip()
    if not decision or "None" in decision:
        return []
    ids = [int(x) for x in re.findall(r"\d+", decision)]
    return [idx for idx in ids if 0 <= idx < max_results]


def extract_result_data(result):
    """Return best-available result payload, preferring structured store data."""
    document = getattr(result, "document", None)
    if document is None:
        document = mapping_get(result, "document", None)

    if document is None:
        return {}

    struct_data = getattr(document, "struct_data", None)
    if struct_data is None:
        struct_data = mapping_get(document, "struct_data", None)
    if struct_data:
        return struct_data

    derived_struct_data = getattr(document, "derived_struct_data", None)
    if derived_struct_data is None:
        derived_struct_data = mapping_get(document, "derived_struct_data", None)
    if derived_struct_data:
        return derived_struct_data

    return {}


def extract_result_title(data, default="文件"):
    """Pick a readable title for either structured or extractive search results."""
    title = (mapping_get(data, "title", "") or "").strip()
    if title:
        return title

    law_name = (mapping_get(data, "law_name", "") or "").strip()
    display_name = (mapping_get(data, "display_name", "") or "").strip()
    article_number = (mapping_get(data, "article_number", "") or "").strip()

    if law_name and display_name:
        return f"{law_name} {display_name}".strip()
    if display_name:
        return display_name
    if law_name and article_number:
        return f"{law_name} 第 {article_number} 條"
    if law_name:
        return law_name
    return default


def extract_result_content(data):
    """Pick best available content from a Discovery Engine result."""
    nested_data = extract_result_data(data)
    if nested_data:
        data = nested_data

    structured_text = mapping_get(data, "embedding_text", "")
    if not structured_text:
        structured_text = mapping_get(data, "content", "")
    if isinstance(structured_text, str) and structured_text:
        return structured_text
    if structured_text and not isinstance(structured_text, (dict, list, tuple, set)):
        return str(structured_text)

    ext_answers = mapping_get(data, "extractive_answers", [])
    if ext_answers:
        content = mapping_get(ext_answers[0], "content", "")
        if content:
            return content

    ext_segments = mapping_get(data, "extractive_segments", [])
    if ext_segments:
        content = mapping_get(ext_segments[0], "content", "")
        if content:
            return content

    snippets = mapping_get(data, "snippets", [{}])
    return mapping_get(snippets[0], "snippet", "")
