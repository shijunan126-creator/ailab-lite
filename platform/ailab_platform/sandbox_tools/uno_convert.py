"""Convert one file through the sandbox's private LibreOffice listener."""

import pathlib
import sys

import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


source, output, filter_name = sys.argv[1:]
context = uno.getComponentContext()
resolver = context.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", context)
remote = resolver.resolve("uno:pipe,name=ailab;urp;StarOffice.ComponentContext")
desktop = remote.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", remote)
document = desktop.loadComponentFromURL(pathlib.Path(source).resolve().as_uri(), "_blank", 0, (prop("Hidden", True),))
if document is None:
    raise RuntimeError("document load failed")
try:
    if not filter_name and output.lower().endswith(".pdf"):
        if document.supportsService("com.sun.star.presentation.PresentationDocument"):
            filter_name = "impress_pdf_Export"
        elif document.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
            filter_name = "calc_pdf_Export"
        else:
            filter_name = "writer_pdf_Export"
    options = (prop("Overwrite", True),) + ((prop("FilterName", filter_name),) if filter_name else ())
    document.storeToURL(pathlib.Path(output).resolve().as_uri(), options)
finally:
    document.close(True)
