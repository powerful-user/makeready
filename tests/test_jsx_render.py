import json
from pathlib import Path

import makeready  # noqa: F401  (the red fails here until the package exists)

MAC = Path(__file__).resolve().parents[1] / "mac"


def test_render_script():
    text = (MAC / "render.jsx").read_text()
    for needle in [
        "userInteractionLevel", "UserInteractionLevels.NEVER_INTERACT", "finally",
        'insertLabel("aid.job"', 'extractLabel("aid.job")', "SaveOptions.NO", "aid-print.json",
        "pdfExportPresets", "convertToProcess", "overflows", "LinkStatus.LINK_MISSING",
        "FontStatus.INSTALLED", "ExportFormat.PDF_TYPE", "report.json", ".rename(", "Document Fonts",
        "/jobs/",
    ]:
        assert needle in text, needle


def test_package_script():
    text = (MAC / "package.jsx").read_text()
    for needle in [
        "packageForPrint", "package-report.json", "finally", "userInteractionLevel", "Document Fonts",
        ".rename(", "SaveOptions.NO",
    ]:
        assert needle in text, needle


def test_preset_settings():
    preset = json.loads((MAC / "aid-print.json").read_text())
    assert preset == {
        "name": "aid-print",
        "standard": "PDF/X-4:2010",
        "colorConversion": "Convert to Destination (Preserve Numbers)",
        "profileInclusion": "Don't Include Profiles",
        "useDocumentBleed": True,
    }
