import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
    id: root
    moduleName: "hancore.voxtype-enhance"

    property string runtimeDir: {
        const xdg = Quickshell.env("XDG_RUNTIME_DIR");
        // Do not assume uid 1000: target machines may create the first user
        // with another uid (for example uid 1001 on the Omarchy Mac image).
        return xdg && xdg.length > 0 ? xdg + "/voxtype" : "";
    }
    property string daemonState: "idle"
    property int spinnerFrame: 0
    property real recordingMix: 0.0
    readonly property bool recordingAnimationEnabled: setting("recordingAnimation", true) === true
    readonly property color normalForeground: root.bar ? root.bar.barForeground : Color.foreground
    readonly property color recordingYellow: "#f2c94c"
    readonly property var spinnerFrames: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    function blendColor(from, to, amount) {
        var t = Math.max(0, Math.min(1, amount));
        return Qt.rgba(
            from.r + (to.r - from.r) * t,
            from.g + (to.g - from.g) * t,
            from.b + (to.b - from.b) * t,
            from.a + (to.a - from.a) * t
        );
    }

    function setRecordingAnimationEnabled(enabled) {
        root.settings = Object.assign({}, root.settings, { recordingAnimation: enabled });
        if (root.bar && root.bar.shell) root.bar.shell.updateEntryInline(root.moduleName, root.settings);
    }

    FileView {
        id: stateFile
        path: root.runtimeDir + "/state"
        watchChanges: true
        printErrors: false
        onLoaded: root.daemonState = (text() || "idle").trim().toLowerCase()
        onLoadFailed: root.daemonState = "idle"
        onFileChanged: reload()
    }

    // Voxtype may replace the state file atomically. Keep the file watcher
    // for immediate updates and poll as a fallback for filesystems where the
    // replacement does not emit FileView's change signal.
    Timer {
        interval: 150
        repeat: true
        running: root.runtimeDir.length > 0
        onTriggered: stateFile.reload()
    }

    Timer {
        interval: 120
        repeat: true
        running: root.daemonState === "transcribing"
        onTriggered: root.spinnerFrame = (root.spinnerFrame + 1) % root.spinnerFrames.length
    }

    SequentialAnimation {
        running: root.daemonState === "recording" && root.recordingAnimationEnabled
        loops: Animation.Infinite
        NumberAnimation {
            target: root
            property: "recordingMix"
            from: 0.0
            to: 1.0
            duration: 1100
            easing.type: Easing.InOutSine
        }
        NumberAnimation {
            target: root
            property: "recordingMix"
            from: 1.0
            to: 0.0
            duration: 1100
            easing.type: Easing.InOutSine
        }
    }

    onDaemonStateChanged: {
        if (root.daemonState !== "transcribing") root.spinnerFrame = 0;
        if (root.daemonState !== "recording") root.recordingMix = 0.0;
    }

    onRecordingAnimationEnabledChanged: {
        if (!root.recordingAnimationEnabled) root.recordingMix = 0.0;
    }

    function injectPanel() {
        if (!panelLoader.item) return;
        panelLoader.item.bar = root.bar;
        panelLoader.item.settings = root.settings;
        panelLoader.item.anchorItem = button;
        panelLoader.item.hostWidget = root;
    }

    function open() {
        if (panelLoader.item) {
            panelLoader.item.open();
            return;
        }
        panelLoader.active = true;
        Qt.callLater(function() {
            if (panelLoader.item) panelLoader.item.open();
        });
    }

    function close() {
        if (panelLoader.item) panelLoader.item.close();
    }

    function toggle() {
        if (root.opened) root.close();
        else root.open();
    }

    onBarChanged: injectPanel()
    onSettingsChanged: injectPanel()

    Loader {
        id: panelLoader
        active: false
        source: Qt.resolvedUrl("../VoxtypePanel.qml")
        visible: false
        onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel); }
    }

    BarIconButton {
        id: button
        bar: root.bar
        foreground: root.daemonState === "recording" && root.recordingAnimationEnabled
            ? root.blendColor(root.normalForeground, root.recordingYellow, root.recordingMix)
            : root.normalForeground
        text: root.daemonState === "transcribing" ? root.spinnerFrames[root.spinnerFrame] : "󰍬"
        tooltipText: root.daemonState === "recording" ? "Voxtype recording"
            : root.daemonState === "transcribing" ? "Voxtype transcribing"
            : "Voxtype settings"
        onPressed: function(buttonCode) {
            if (buttonCode === Qt.LeftButton) root.toggle();
        }
    }
}
