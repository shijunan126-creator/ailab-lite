"""New-run document schemas; never mutate the legacy tool objects."""

from ..contracts.document_input import ReadDocument, SearchDocument, ViewDocument


def tool(name, description, contract):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": contract.model_json_schema()}}


TOOLS = [
    tool(
        "read_document",
        "先用navigation_only=true读取有界真实locator导航，再选范围读取。默认text_source=native，只读原生文字；"
        "OCR须显式text_source=ocr并选择页或区域。"
        "文字、视觉、OCR覆盖各自独立；页眉或隐藏文字不代表图形已读。next_cursor须原样继续，不能更改查询或范围。",
        ReadDocument,
    ),
    tool(
        "search_document",
        "在文档已有原生文字和OCR文字中搜索，不自动OCR。每次最多扫描100块、返回20条命中；"
        "零命中且有next_cursor仍须继续。规范化片段只用于定位，精确引用须read_document读取原文。",
        SearchDocument,
    ),
    tool(
        "view_document",
        "查看文档页、图片或绑定表示revision的区域；每次最多4页。不自动OCR。"
        "根据实际返回来源与覆盖判断布局、图表和嵌图，不能把不同Word排版页码互换。",
        ViewDocument,
    ),
]


# These are public operation outcomes, never provider errors or host paths.
ERRORS = frozenset(
    {
        "cursor_stale",
        "native_cache_changed",
        "native_cache_missing",
        "ocr_cache_changed",
        "ocr_image_revision_changed",
        "document_image_revision_changed",
        "document_view_generation_changed",
        "document_source_unavailable",
        "document_run_deadline",
        "document_queue_full",
        "document_ocr_work_limit",
        "document_input_limit",
        "document_source_limit",
        "document_structure_limit",
        "native_structure_limit",
        "derived_capacity_exceeded",
        "derived_accounting_initializing",
        "selector_representation_mismatch",
        "selector_requires_scoped_preparation",
        "document_page_out_of_range",
        "model_vision_required",
        "explicit_ocr_scope_required",
        "ocr_executor_unavailable",
        "native_representation_failed",
        "native_representation_unavailable",
        "ocr_representation_unavailable",
        "native_page_count_unavailable",
        "embedded_image_unavailable_use_pages",
        "document_tool_not_available_for_catalog",
    }
)
