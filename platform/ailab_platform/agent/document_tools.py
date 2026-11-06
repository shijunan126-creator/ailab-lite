"""New-run document schemas; never mutate the legacy tool objects."""

import re

from pydantic import ValidationError

from ..contracts.document_input import ReadDocument, SearchDocument, ViewDocument

CONTRACTS = {"read_document": ReadDocument, "search_document": SearchDocument, "view_document": ViewDocument}


def tool(name, description, contract):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": contract.model_json_schema()}}


# Descriptions carry the operating contract once; format guides only add format facts.
TOOLS = [
    tool(
        "read_document",
        "读取一份文档的文字。默认text_source=native读原生文字，不隐式OCR；selector选范围：pages为PDF/PPT页号，"
        "word为段落/表格locator，sheet为工作表和可选单元格range；不带selector从头分批读。"
        "navigation_only=true只返回目录与locator。识别图片或扫描页文字时用text_source=ocr并指定pages或region。"
        "返回next_cursor时原样传回继续，其他参数不变。",
        ReadDocument,
    ),
    tool(
        "search_document",
        "在文档已有的原生/OCR文字中做字面匹配（NFKC、忽略大小写），不自动OCR；不是语义、正则或多词OR，多个关键词分别调用。"
        "每次最多扫描100块、返回20条命中；只要返回next_cursor就还没搜完，即使本批0命中也原样传回继续。"
        "零命中只说明该字面短语不在已搜范围；片段已规范化，只作定位，引用原文用read_document。",
        SearchDocument,
    ),
    tool(
        "view_document",
        "查看文档页图或区域，不OCR。pages每次最多4页；Word/Excel的pages是渲染页，不是段落或工作表序号。"
        "看局部细节时用kind=region：把此前返回的files[].region_ref原样填入（page或image_id加revision），"
        "x/y/width/height是该图上的0–1归一化坐标。文字已足够回答时不额外看图。",
        ViewDocument,
    ),
]


def argument_error(name, arguments):
    """Validate locally so a malformed call returns the exact fields to fix.

    The API repeats the same validation; this only turns an opaque 422 into
    feedback the model can act on, without echoing argument values.
    """
    try:
        CONTRACTS[name].model_validate(arguments)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_url=False, include_input=False)[:4]:
            location = ".".join(str(part) for part in error["loc"] if not re.match(r"^[\w-]+\[", str(part)))
            message = error["msg"].removeprefix("Value error, ")
            problems.append(f"{location}: {message}" if location else message)
        return {"ok": False, "error": "invalid_document_arguments", "reason": "invalid_document_arguments", "fix": problems}
    return None


# Next step for outcomes the model can fix itself; others are reported to the user.
REGION_HINT = "region须引用已查看的同页图：先view_document(pages=[该页])，再原样填入返回的files[].region_ref"
HINTS = {
    "ocr_image_revision_changed": REGION_HINT,
    "document_image_revision_changed": REGION_HINT,
    "cursor_stale": "文档表示已变化，去掉cursor从头重新读取或搜索",
    "document_page_out_of_range": "页号超出文档页数，按附件导航中的页数选择",
    "embedded_image_unavailable_use_pages": "改用view_document的pages查看所在页",
    "selector_representation_mismatch": "selector类型与文档格式不符：PDF/PPT用pages，Word用word，Excel用sheet",
}


def failure(reason):
    result = {"ok": False, "error": reason, "reason": reason}
    if reason in HINTS:
        result["fix"] = [HINTS[reason]]
    return result


# These are public operation outcomes, never provider errors or host paths.
ERRORS = frozenset(
    {
        "cursor_stale",
        "native_cache_changed",
        "native_cache_missing",
        "ocr_cache_changed",
        "ocr_cache_missing",
        "ocr_image_revision_changed",
        "document_image_revision_changed",
        "document_view_generation_changed",
        "document_source_unavailable",
        "document_run_deadline",
        "document_queue_full",
        "document_consumer_limit",
        "document_ocr_work_limit",
        "document_input_limit",
        "document_source_limit",
        "document_structure_limit",
        "native_structure_limit",
        "native_format_unsupported",
        "derived_capacity_exceeded",
        "derived_accounting_initializing",
        "selector_representation_mismatch",
        "selector_requires_scoped_preparation",
        "document_page_out_of_range",
        "document_page_images_failed",
        "image_size_limit",
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
