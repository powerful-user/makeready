var inputFolder = arguments[0];
var outputFolder = arguments[1];
var savedInteraction = app.scriptPreferences.userInteractionLevel;
var savedUnit = app.scriptPreferences.measurementUnit;
var doc = null;
// InDesign refuses a font it cannot load, and Nimbus Sans is deliberately not
// installed on the Mac. The document is saved beside a Document Fonts folder
// holding it and reopened, so the font is active while the reference is built.
var work = Folder(Folder.temp.fsName + "/aid-reference");
var workFont = File(work.fsName + "/Document Fonts/NimbusSans-Regular.otf");
var workDoc = File(work.fsName + "/reference.indd");
try {
    app.scriptPreferences.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    app.scriptPreferences.measurementUnit = MeasurementUnits.POINTS;
    Folder(work.fsName + "/Document Fonts").create();
    if (!File(inputFolder + "/NimbusSans-Regular.otf").copy(workFont.fsName)) {
        throw new Error("could not copy NimbusSans-Regular.otf to " + workFont.fsName);
    }
    doc = app.documents.add(false);
    doc = doc.save(workDoc);
    doc.close(SaveOptions.NO);
    doc = null;
    doc = app.open(workDoc, false);
    var prefs = doc.documentPreferences;
    prefs.pageWidth = 612;
    prefs.pageHeight = 792;
    prefs.facingPages = false;
    prefs.documentBleedTopOffset = 9;
    prefs.documentBleedBottomOffset = 9;
    prefs.documentBleedInsideOrLeftOffset = 9;
    prefs.documentBleedOutsideOrRightOffset = 9;

    var processSwatch = doc.colors.add();
    processSwatch.name = "AID Process";
    processSwatch.model = ColorModel.PROCESS;
    processSwatch.space = ColorSpace.CMYK;
    processSwatch.colorValue = [0, 70, 95, 0];

    var spotSwatch = doc.colors.add();
    spotSwatch.name = "AID Spot";
    spotSwatch.model = ColorModel.SPOT;
    spotSwatch.space = ColorSpace.CMYK;
    spotSwatch.colorValue = [100, 60, 0, 5];

    var body = doc.paragraphStyles.add();
    body.name = "AID Body";
    body.appliedFont = "Nimbus Sans\tRegular";
    body.pointSize = 12;
    body.leading = 14.4;

    var textFrame = doc.textFrames.add();
    textFrame.geometricBounds = [36, 36, 120, 336];
    textFrame.insertLabel("aid.id", "text-1");
    textFrame.contents = "Reference";
    textFrame.paragraphs[0].appliedParagraphStyle = body;
    textFrame.textFramePreferences.firstBaselineOffset = FirstBaseline.FIXED_HEIGHT;
    textFrame.textFramePreferences.minimumFirstBaselineOffset = 9.948;
    textFrame.textFramePreferences.autoSizingType = AutoSizingTypeEnum.HEIGHT_ONLY;
    textFrame.contentTransparencySettings.blendingSettings.opacity = 80;

    var rect = doc.rectangles.add();
    rect.geometricBounds = [200, 100, 220, 300];
    rect.insertLabel("aid.id", "rect-1");
    rect.fillColor = spotSwatch;
    rect.strokeColor = processSwatch;
    rect.strokeWeight = 2;
    rect.rotationAngle = 30;
    rect.transparencySettings.blendingSettings.opacity = 90;
    rect.fillTransparencySettings.blendingSettings.opacity = 50;

    var photo = doc.rectangles.add();
    photo.geometricBounds = [300, -9, 700, 621];
    photo.insertLabel("aid.id", "photo-1");
    photo.place(File(inputFolder + "/ref.jpg"));
    photo.fit(FitOptions.FILL_PROPORTIONALLY);

    var oval = doc.ovals.add();
    oval.geometricBounds = [600, 500, 650, 550];
    oval.insertLabel("aid.id", "oval-1");

    var pdfFrame = doc.rectangles.add();
    pdfFrame.geometricBounds = [720, 36, 760, 116];
    pdfFrame.insertLabel("aid.id", "pdf-1");
    pdfFrame.place(File(inputFolder + "/logo.pdf"));

    var outFolder = Folder(outputFolder);
    if (!outFolder.exists) {
        outFolder.create();
    }
    doc.exportFile(ExportFormat.INDESIGN_MARKUP, File(outputFolder + "/reference.idml"));
} finally {
    try {
        try {
            if (doc !== null) {
                doc.close(SaveOptions.NO);
            }
        } finally {
            workDoc.remove();
            workFont.remove();
            Folder(work.fsName + "/Document Fonts").remove();
            work.remove();
        }
    } finally {
        try {
            app.scriptPreferences.measurementUnit = savedUnit;
        } finally {
            app.scriptPreferences.userInteractionLevel = savedInteraction;
        }
    }
}
"ok";
