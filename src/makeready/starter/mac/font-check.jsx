var savedInteraction = app.scriptPreferences.userInteractionLevel;
var doc = null;
var lines = "";
try {
    app.scriptPreferences.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    doc = app.open(File(arguments[0]), false);
    var i;
    for (i = 0; i < doc.fonts.length; i++) {
        var font = doc.fonts[i];
        var line = font.name + "\t" + font.status.toString();
        if (lines === "") {
            lines = line;
        } else {
            lines = lines + "\n" + line;
        }
    }
} finally {
    try {
        if (doc !== null) {
            doc.close(SaveOptions.NO);
        }
    } finally {
        app.scriptPreferences.userInteractionLevel = savedInteraction;
    }
}
lines;
