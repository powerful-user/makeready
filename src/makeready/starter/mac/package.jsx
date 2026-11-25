// Packages out/<doc>.indd into the handoff folder and writes package-report.json last.
// Fonts are already active from out/Document Fonts, beside the saved document.
// ES3. Leaves every InDesign preference as it found it.

var jobFolder = new Folder(arguments[0]);
var docName = String(arguments[1]);
var handoffFolder = new Folder(arguments[2]);

var savedInteraction = app.scriptPreferences.userInteractionLevel;
var doc = null;
var failure = null;
var packaged = false;
var outcome = "failed";
var missingLinks = [];
var missingFonts = [];

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

function reportBody() {
    var status = "failed";
    if (failure === null && packaged === true && missingFonts.length === 0 && missingLinks.length === 0) {
        status = "ok";
    }
    outcome = status;
    var errorJson = "null";
    if (failure !== null) {
        errorJson = jsonString(errorText(failure));
    } else if (packaged === false) {
        errorJson = jsonString("packageForPrint returned false");
    }
    return "{"
        + '"status":' + jsonString(status)
        + ',"missing_fonts":' + jsonStrings(missingFonts)
        + ',"missing_links":' + jsonStrings(missingLinks)
        + ',"error":' + errorJson
        + "}\n";
}

if (arguments.length < 3) {
    throw new Error("package.jsx needs the job folder, document name and handoff folder");
}

try {
    app.scriptPreferences.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    closeAbandoned();
    var outFolder = new Folder(jobFolder.fsName + "/out");
    // Opening the document beside out/Document Fonts activates those fonts.
    doc = app.open(new File(outFolder.fsName + "/" + docName + ".indd"), false);
    collectMissingFonts(doc, missingFonts);
    collectMissingLinks(doc, missingLinks);
    if (handoffFolder.exists === false && handoffFolder.create() === false) {
        throw new Error("could not create " + handoffFolder.fsName);
    }
    var preset = app.pdfExportPresets.itemByName("aid-print");
    if (preset === null || preset.isValid === false) {
        throw new Error("PDF preset aid-print is missing; render the document again");
    }
    packaged = doc.packageForPrint(
        handoffFolder,
        true,
        true,
        false,
        true,
        false,
        true,
        false,
        true,
        true,
        "aid-print"
    ) === true;
    doc.close(SaveOptions.NO);
    doc = null;
} catch (error) {
    failure = error;
} finally {
    try {
        closeOpened();
        if (handoffFolder.exists === false) {
            handoffFolder.create();
        }
        publish(handoffFolder, "package-report.json", reportBody());
    } finally {
        app.scriptPreferences.userInteractionLevel = savedInteraction;
    }
}

outcome;
