// Renders one job folder to out/<doc>.pdf and writes out/report.json last.
// ES3. Leaves every InDesign preference as it found it.

var jobFolder = new Folder(arguments[0]);
var docName = String(arguments[1]);
var buildId = String(arguments[2]);
var jobId = String(arguments[3]);
var profileName = String(arguments[4]);

var savedInteraction = app.scriptPreferences.userInteractionLevel;
var doc = null;
var failure = null;
var outcome = "failed";
var started = "";
var version = "";
var overset = [];
var missingLinks = [];
var missingFonts = [];
var inks = [];
var preflight = [];

function pad(number) {
    var text = String(number);
    if (text.length < 2) {
        return "0" + text;
    }
    return text;
}

function utcNow() {
    var date = new Date();
    return date.getUTCFullYear() + "-" + pad(date.getUTCMonth() + 1) + "-" + pad(date.getUTCDate()) + "T" + pad(date.getUTCHours()) + ":" + pad(date.getUTCMinutes()) + ":" + pad(date.getUTCSeconds()) + "Z";
}

function errorText(error) {
    if (error === null || error === undefined) {
        return "";
    }
    if (error.message !== undefined && String(error.message) !== "") {
        return String(error.message);
    }
    return String(error);
}

function jsonString(value) {
    var text = String(value);
    var out = '"';
    var i;
    for (i = 0; i < text.length; i++) {
        var ch = text.charAt(i);
        var code = text.charCodeAt(i);
        if (ch === "\\") {
            out += "\\\\";
        } else if (ch === '"') {
            out += '\\"';
        } else if (ch === "\n") {
            out += "\\n";
        } else if (ch === "\r") {
            out += "\\r";
        } else if (ch === "\t") {
            out += "\\t";
        } else if (code < 32) {
            var hex = code.toString(16);
            while (hex.length < 4) {
                hex = "0" + hex;
            }
            out += "\\u" + hex;
        } else {
            out += ch;
        }
    }
    return out + '"';
}

function jsonStrings(items) {
    var parts = [];
    var i;
    for (i = 0; i < items.length; i++) {
        parts.push(jsonString(items[i]));
    }
    return "[" + parts.join(",") + "]";
}

function jsonPreflight(items) {
    var parts = [];
    var i;
    for (i = 0; i < items.length; i++) {
        var element = "null";
        if (items[i].element !== null && items[i].element !== undefined) {
            element = jsonString(items[i].element);
        }
        parts.push('{"rule":' + jsonString(items[i].rule) + ',"element":' + element + "}");
    }
    return "[" + parts.join(",") + "]";
}

function seriesDoc(folder) {
    var path = folder.fsName;
    while (path.length > 1 && path.charAt(path.length - 1) === "/") {
        path = path.substring(0, path.length - 1);
    }
    var parts = path.split("/");
    var seriesName = parts.length > 1 ? parts[parts.length - 2] : "";
    var folderName = parts.length > 0 ? parts[parts.length - 1] : "";
    return seriesName + "/" + folderName;
}

function aidLabel(item) {
    var current = item;
    var guard = 0;
    while (current !== null && current !== undefined && guard < 8) {
        guard++;
        if (current.extractLabel !== undefined) {
            try {
                var label = current.extractLabel("aid.id");
                if (label !== "") {
                    return label;
                }
            } catch (labelError) {
                return "";
            }
        }
        if (current.parent === undefined) {
            return "";
        }
        current = current.parent;
    }
    return "";
}

function closeAbandoned() {
    var i;
    for (i = app.documents.length - 1; i >= 0; i--) {
        var openDoc = app.documents[i];
        var label = openDoc.extractLabel("aid.job");
        if (label === "") {
            continue;
        }
        var path = "";
        if (openDoc.saved) {
            path = openDoc.fullName.fsName;
        }
        if (openDoc.saved === false || path.indexOf("/makeready/jobs/") !== -1) {
            openDoc.close(SaveOptions.NO);
        }
    }
}

function removeTree(folder) {
    var items = folder.getFiles();
    var i;
    for (i = 0; i < items.length; i++) {
        if (items[i] instanceof Folder) {
            removeTree(items[i]);
            items[i].remove();
        } else {
            items[i].remove();
        }
    }
}

function emptyOut(folder) {
    if (folder.exists === false) {
        if (folder.create() === false) {
            throw new Error("could not create " + folder.fsName);
        }
        return;
    }
    removeTree(folder);
}

function readText(file) {
    file.encoding = "UTF-8";
    if (file.open("r") === false) {
        throw new Error("could not open " + file.fsName);
    }
    var text = file.read();
    file.close();
    if (text.length > 0 && text.charCodeAt(0) === 0xFEFF) {
        text = text.substring(1);
    }
    return text;
}

function writeText(file, text) {
    file.encoding = "UTF-8";
    if (file.open("w") === false) {
        throw new Error("could not open " + file.fsName);
    }
    var ok = file.write(text);
    file.close();
    if (ok === false) {
        throw new Error("could not write " + file.fsName);
    }
}

function publish(folder, name, text) {
    var finalFile = new File(folder.fsName + "/" + name);
    var tempFile = new File(folder.fsName + "/" + name + ".tmp");
    if (tempFile.exists) {
        tempFile.remove();
    }
    writeText(tempFile, text);
    if (finalFile.exists) {
        finalFile.remove();
    }
    if (tempFile.rename(name) === false) {
        throw new Error("could not rename " + tempFile.fsName);
    }
}

function presetByName(name) {
    try {
        var preset = app.pdfExportPresets.itemByName(name);
        if (preset !== null && preset.isValid) {
            return preset;
        }
    } catch (missing) {
        return null;
    }
    return null;
}

function applyPreset(profile) {
    var presetFile = new File(File($.fileName).parent.fsName + "/aid-print.json");
    var settings = eval("(" + readText(presetFile) + ")");
    if (settings === null || typeof settings !== "object") {
        throw new Error("aid-print.json must be an object");
    }
    if (typeof settings.name !== "string" || settings.name === "") {
        throw new Error("aid-print.json name must be text");
    }
    var preset = presetByName(settings.name);
    if (preset === null) {
        preset = app.pdfExportPresets.add({name: settings.name});
    }
    preset.standardsCompliance = PDFXStandards.PDFX42010_STANDARD;
    preset.pdfColorSpace = PDFColorSpace.REPURPOSE_CMYK;
    preset.pdfDestinationProfile = profile;
    preset.pdfXProfile = profile;
    preset.includeICCProfiles = ICCProfiles.INCLUDE_NONE;
    preset.useDocumentBleedWithPDF = true;
    return preset;
}

function collectOverset(opened, ids) {
    var i;
    for (i = 0; i < opened.stories.length; i++) {
        var story = opened.stories[i];
        if (story.overflows !== true) {
            continue;
        }
        var id = "";
        if (story.textContainers.length > 0) {
            id = aidLabel(story.textContainers[0]);
        }
        ids.push(id);
    }
}

function collectMissingLinks(opened, ids) {
    var i;
    for (i = 0; i < opened.links.length; i++) {
        var link = opened.links[i];
        if (link.status !== LinkStatus.LINK_MISSING) {
            continue;
        }
        ids.push(aidLabel(link.parent));
    }
}

function collectMissingFonts(opened, names) {
    var i;
    for (i = 0; i < opened.fonts.length; i++) {
        var font = opened.fonts[i];
        if (font.status !== FontStatus.INSTALLED) {
            names.push(font.name);
        }
    }
}

function labelForNode(opened, nodeId) {
    try {
        var item = opened.pageItems.itemByID(nodeId);
        if (item === null || item.isValid === false) {
            return null;
        }
        var label = item.extractLabel("aid.id");
        if (label === "") {
            return null;
        }
        return label;
    } catch (missing) {
        return null;
    }
}

function readPreflightRows(opened, results, found) {
    if (results === null || results === undefined || results.length === undefined) {
        throw new Error("aggregatedResults is not an array");
    }
    if (results.length < 3) {
        return;
    }
    var errors = results[2];
    if (errors === null || errors.length === undefined) {
        throw new Error("aggregatedResults has no error list");
    }
    var i;
    for (i = 0; i < errors.length; i++) {
        var row = errors[i];
        if (row === null || row.length === undefined || row.length < 2 || typeof row[1] !== "string" || row[1] === "") {
            throw new Error("preflight error row is not the expected shape");
        }
        var element = null;
        if (typeof row[0] === "number") {
            element = labelForNode(opened, row[0]);
        }
        found.push({rule: row[1], element: element});
    }
}

function collectPreflight(opened, found) {
    var process = null;
    var added = found.length;
    try {
        var profile = app.preflightProfiles.itemByName("[Basic]");
        if (profile === null || profile.isValid === false) {
            throw new Error("preflight profile [Basic] is missing");
        }
        process = app.preflightProcesses.add(opened, profile);
        process.waitForProcess();
        readPreflightRows(opened, process.aggregatedResults, found);
    } catch (preflightError) {
        found.length = added;
        found.push({rule: "preflight unavailable: " + errorText(preflightError), element: null});
    }
    if (process !== null) {
        try {
            process.remove();
        } catch (removeError) {
        }
    }
}

function isProcessName(name) {
    return name === "Process Cyan" || name === "Process Magenta" || name === "Process Yellow" || name === "Process Black";
}

function swatchIsSpot(opened, name) {
    try {
        var swatch = opened.swatches.itemByName(name);
        return swatch !== null && swatch.isValid === true && swatch.model === ColorModel.SPOT;
    } catch (swatchError) {
        return false;
    }
}

function collectInks(opened, names) {
    var i;
    for (i = 0; i < opened.inks.length; i++) {
        var ink = opened.inks[i];
        if (isProcessName(ink.name)) {
            continue;
        }
        ink.convertToProcess = swatchIsSpot(opened, ink.name) === false;
    }
    for (i = 0; i < opened.inks.length; i++) {
        var kept = opened.inks[i];
        if (isProcessName(kept.name) || kept.convertToProcess === false) {
            names.push(kept.name);
        }
    }
}

function copyFonts(srcFolder, dstFolder) {
    if (srcFolder.exists === false) {
        return;
    }
    if (dstFolder.exists === false && dstFolder.create() === false) {
        throw new Error("could not create " + dstFolder.fsName);
    }
    var items = srcFolder.getFiles();
    var i;
    for (i = 0; i < items.length; i++) {
        if (items[i] instanceof File) {
            var target = new File(dstFolder.fsName + "/" + items[i].name);
            if (items[i].copy(target) === false) {
                throw new Error("could not copy " + items[i].name + " to " + target.fsName);
            }
        }
    }
}

function closeOpened() {
    if (doc === null) {
        return;
    }
    try {
        if (doc.isValid) {
            doc.close(SaveOptions.NO);
        }
    } catch (closeError) {
        if (failure === null) {
            failure = closeError;
        }
    }
    doc = null;
}

function reportStatus() {
    if (failure !== null) {
        return "failed";
    }
    if (overset.length > 0 || missingLinks.length > 0 || missingFonts.length > 0 || preflight.length > 0) {
        return "warnings";
    }
    return "ok";
}

function reportBody() {
    var status = reportStatus();
    outcome = status;
    var errorJson = "null";
    if (failure !== null) {
        errorJson = jsonString(errorText(failure));
    }
    var seen = version;
    if (seen === "") {
        try {
            seen = String(app.version);
        } catch (versionError) {
            seen = "";
        }
    }
    var began = started;
    if (began === "") {
        began = utcNow();
    }
    return "{"
        + '"job":' + jsonString(jobId)
        + ',"doc":' + jsonString(seriesDoc(jobFolder))
        + ',"build_id":' + jsonString(buildId)
        + ',"indesign":' + jsonString(seen)
        + ',"started":' + jsonString(began)
        + ',"finished":' + jsonString(utcNow())
        + ',"status":' + jsonString(status)
        + ',"overset":' + jsonStrings(overset)
        + ',"missing_links":' + jsonStrings(missingLinks)
        + ',"missing_fonts":' + jsonStrings(missingFonts)
        + ',"inks":' + jsonStrings(inks)
        + ',"preflight":' + jsonPreflight(preflight)
        + ',"error":' + errorJson
        + "}\n";
}

if (arguments.length < 5) {
    throw new Error("render.jsx needs the job folder, document name, build id, job id and profile");
}

try {
    app.scriptPreferences.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    started = utcNow();
    version = String(app.version);
    closeAbandoned();
    var outFolder = new Folder(jobFolder.fsName + "/out");
    emptyOut(outFolder);
    var preset = applyPreset(profileName);
    var idml = new File(jobFolder.fsName + "/in/" + docName + ".idml");
    doc = app.open(idml, false);
    doc.insertLabel("aid.job", jobId);
    collectOverset(doc, overset);
    collectMissingLinks(doc, missingLinks);
    collectMissingFonts(doc, missingFonts);
    collectPreflight(doc, preflight);
    collectInks(doc, inks);
    copyFonts(new Folder(jobFolder.fsName + "/in/Document Fonts"), new Folder(outFolder.fsName + "/Document Fonts"));
    doc.insertLabel("aid.job", "");
    doc.save(new File(outFolder.fsName + "/" + docName + ".indd"));
    doc.exportFile(ExportFormat.PDF_TYPE, new File(outFolder.fsName + "/" + docName + ".pdf"), false, preset);
    doc.close(SaveOptions.NO);
    doc = null;
} catch (error) {
    failure = error;
} finally {
    try {
        closeOpened();
        var reportFolder = new Folder(jobFolder.fsName + "/out");
        if (reportFolder.exists === false) {
            reportFolder.create();
        }
        publish(reportFolder, "report.json", reportBody());
    } finally {
        app.scriptPreferences.userInteractionLevel = savedInteraction;
    }
}

outcome;
