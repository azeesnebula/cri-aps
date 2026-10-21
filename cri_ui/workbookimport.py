"""
The upload endpoint behind the "Import planning workbook" panel.

    POST /planning-workbook/check/     inspect an uploaded .xlsx, write nothing

Dry run by design. The planner gets a per-sheet account of what was recognised
before anything is committed, so a workbook that is subtly wrong - a renamed
tab, a column dropped by whoever exported it - is caught while it is still
cheap to fix. The committing step is deliberately a separate change.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .workbook import SiegerWorkbook, WorkbookProblem


# openpyxl holds the whole sheet; the real extracts are ~10 MB, so allow some
# headroom but refuse something that was obviously never a workbook.
MAX_UPLOAD_BYTES = 80 * 1024 * 1024


@login_required
@require_POST
def check(request):
    upload = request.FILES.get("workbook")
    context = {"filename": getattr(upload, "name", None)}

    if not upload:
        context["error"] = "No file was selected."
    elif upload.size > MAX_UPLOAD_BYTES:
        context["error"] = "That file is %.0f MB; the limit is %d MB." % (
            upload.size / 1048576.0,
            MAX_UPLOAD_BYTES // 1048576,
        )
    elif not upload.name.lower().endswith((".xlsx", ".xlsm")):
        context["error"] = (
            "Only .xlsx workbooks can be read. Open the file in Excel and "
            "use Save As to convert it."
        )
    else:
        try:
            context["report"] = SiegerWorkbook(upload).read().report()
        except WorkbookProblem as e:
            context["error"] = str(e)

    return render(request, "sieger_ui/importworkbook_report.html", context)
