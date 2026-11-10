// generateIDMLSchema writes some multi-line doc comments with "#" on the
// first line only, which makes the schema unparseable. A non-blank line that
// follows a comment and does not start a definition is commented out.
var DEFINITION = /^(\w+\s*=|\s|}|grammar\b|start\b|include\b|namespace\b|datatypes\b|default\b)/;

function repairComments(file) {
    file.encoding = "UTF-8";
    if (!file.open("r")) {
        throw new Error("could not read " + file.fsName);
    }
    var lines = file.read().split("\n");
    file.close();
    var inComment = false;
    var changed = false;
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (/^#/.test(line)) {
            inComment = true;
        } else if (/\S/.test(line)) {
            if (inComment && !DEFINITION.test(line)) {
                lines[i] = "# " + line;
                changed = true;
            } else {
                inComment = false;
            }
        }
    }
    if (changed) {
        file.lineFeed = "Unix";
        if (!file.open("w")) {
            throw new Error("could not write " + file.fsName);
        }
        file.write(lines.join("\n"));
        file.close();
    }
}

function repairFolder(folder) {
    var entries = folder.getFiles();
    for (var i = 0; i < entries.length; i++) {
        if (entries[i] instanceof Folder) {
            repairFolder(entries[i]);
        } else if (/\.rnc$/.test(entries[i].name)) {
            repairComments(entries[i]);
        }
    }
}

var savedInteraction = app.scriptPreferences.userInteractionLevel;
try {
    app.scriptPreferences.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    var folder = Folder(arguments[0]);
    if (!folder.exists) {
        folder.create();
    }
    app.generateIDMLSchema(Folder(arguments[0]), true);
    repairFolder(Folder(arguments[0]));
} finally {
    app.scriptPreferences.userInteractionLevel = savedInteraction;
}
"ok";
