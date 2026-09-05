import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
    id: root
    moduleName: "hancore.voxtype-enhance"
    manageIpc: false

    property var anchorItem: null
    property var hostWidget: null
    property string configTool: Qt.resolvedUrl("scripts/voxtype-config.py").toString().replace("file://", "")
    property bool loading: false
    property real downloadProgress: -1
    property string pendingAction: ""
    property string pendingModelId: ""
    property string statusText: ""
    property bool statusIsError: false
    property bool requiresOnnx: false
    property bool onnxSetupSupported: true
    property string blockedModelId: ""
    property string writeStderrText: ""
    property string engine: "sensevoice"
    property string model: "small-int8"
    property string modelId: ""
    property var installedModels: []
    property string language: "zh"
    property string outputMode: "universal"
    property string pasteKeys: "ctrl+v"
    readonly property color panelForeground: Color.popups.text
    readonly property color panelMuted: Util.alpha(Color.popups.text, 0.58)
    readonly property color panelBorder: Color.popups.border

    // These are the three Sasayaki models.  Engine names and Voxtype's
    // internal model names stay implementation details of the config bridge.
    readonly property var models: [
        { id: "sensevoice-int8", title: "SenseVoice Small · int8", detail: "229 MB · multilingual", engine: "sensevoice", backendModel: "small-int8" },
        { id: "sensevoice-full", title: "SenseVoice Small · full precision", detail: "894 MB · higher quality", engine: "sensevoice", backendModel: "small" },
        { id: "paraformer-zh-int8", title: "Paraformer Large · int8", detail: "232 MB · Chinese-first", engine: "paraformer", backendModel: "paraformer-zh" }
    ]
    readonly property var languages: [
        { key: "zh", title: "中文" },
        { key: "en", title: "English" },
        { key: "ja", title: "日本語" },
        { key: "ko", title: "한국어" },
        { key: "auto", title: "Auto detect" }
    ]

    function open() { root.controller.show(); refresh(); }
    function close() { root.controller.hide(); }
    function toggle() { root.opened ? root.close() : root.open(); }
    function refresh() { if (!readProcess.running) readProcess.running = true; }
    function setValue(setting, value) {
        if (writeProcess.running) return;
        writeProcess.command = ["python3", root.configTool, "set", setting, value];
        root.loading = true;
        root.pendingAction = setting === "model" ? "model" : "setting";
        root.pendingModelId = setting === "model" ? value : "";
        root.downloadProgress = setting === "model" ? 0 : -1;
        root.statusText = setting === "model" ? "Downloading and verifying model…" : "Applying…";
        root.statusIsError = false;
        root.requiresOnnx = false;
        root.onnxSetupSupported = true;
        root.writeStderrText = "";
        writeProcess.running = true;
    }
    function enableOnnx() {
        if (writeProcess.running) return;
        writeProcess.command = ["python3", root.configTool, "enable-onnx"];
        root.loading = true;
        root.pendingAction = "onnx";
        root.pendingModelId = "";
        root.downloadProgress = -1;
        root.statusText = "Administrator approval is required to enable ONNX…";
        root.statusIsError = false;
        root.requiresOnnx = false;
        root.writeStderrText = "";
        writeProcess.running = true;
    }
    function updateDownloadProgress(raw) {
        const lines = String(raw || "").split("\n");
        for (let index = lines.length - 1; index >= 0; index--) {
            if (!lines[index].startsWith("VOXTYPE_ENHANCE_PROGRESS ")) continue;
            const fields = lines[index].split(" ");
            if (fields.length >= 2) root.downloadProgress = Number(fields[1]);
            return;
        }
    }
    function processError(raw, fallback) {
        const lines = String(raw || "").split("\n").filter(function(line) {
            return line.length > 0 && !line.startsWith("VOXTYPE_ENHANCE_PROGRESS ");
        });
        return lines.length > 0 ? lines.join("\n") : fallback;
    }
    function modelInstalled(modelId) {
        return root.installedModels.indexOf(modelId) >= 0;
    }
    function clearPluginData() {
        if (writeProcess.running) return;
        writeProcess.command = ["python3", root.configTool, "clear"];
        root.loading = true;
        root.pendingAction = "clear";
        root.pendingModelId = "";
        root.downloadProgress = -1;
        root.statusText = "Clearing models and restoring defaults…";
        root.statusIsError = false;
        root.requiresOnnx = false;
        root.writeStderrText = "";
        writeProcess.running = true;
    }
    function applySnapshot(raw) {
        try {
            const data = JSON.parse(raw);
            // Keep this flag authoritative for the current response. The
            // write process may exit non-zero with a structured JSON error;
            // do not let the generic fallback below hide that useful detail.
            root.statusIsError = data.error ? true : false;
            const requiresOnnx = data.requires_onnx === true;
            if (data.error) {
                root.statusText = data.error;
                if (requiresOnnx && root.pendingAction === "model") {
                    root.blockedModelId = root.pendingModelId;
                }
            }
            root.engine = data.engine || "whisper";
            root.model = data.model || "small";
            root.installedModels = data.installed_models || [];
            root.modelId = data.model_id || "";
            root.language = data.language || "auto";
            root.outputMode = data.mode || "type";
            root.pasteKeys = data.paste_keys || "ctrl+v";
            // Keep the ONNX action visible when the bridge reports a feature
            // gate. This used to be overwritten unconditionally below, so
            // the panel only showed the vague "Could not apply..." error.
            root.requiresOnnx = requiresOnnx;
            root.onnxSetupSupported = data.onnx_setup_supported !== false;
            return true;
        } catch (error) {
            root.statusText = "Could not read Voxtype configuration";
            root.statusIsError = true;
            return false;
        }
    }
    function rowColor(active) {
        return active
            ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.22)
            : Util.alpha(Color.popups.text, 0.07);
    }

    Process {
        id: readProcess
        command: ["python3", root.configTool, "get"]
        running: false
        stdout: StdioCollector { id: readStdout; waitForEnd: true }
        stderr: StdioCollector { id: readStderr; waitForEnd: true }
        onExited: function(exitCode) {
            const output = String(readStdout.text || "").trim();
            if (exitCode !== 0 || output.length === 0) {
                root.statusText = root.processError(readStderr.text, "Could not read Voxtype configuration");
                root.statusIsError = true;
                return;
            }
            root.applySnapshot(output);
        }
    }

    Process {
        id: writeProcess
        command: ["python3", root.configTool, "set", "mode", "paste"]
        running: false
        stdout: StdioCollector { id: writeStdout; waitForEnd: true }
        stderr: SplitParser {
            onRead: function(line) {
                const text = String(line || "");
                root.writeStderrText += text + "\n";
                root.updateDownloadProgress(text);
            }
        }
        onExited: function(exitCode) {
            root.loading = false;
            root.downloadProgress = -1;
            const output = String(writeStdout.text || "").trim();
            const snapshotOk = output.length > 0 && root.applySnapshot(output);
            const succeeded = exitCode === 0 && snapshotOk;
            const requiresOnnxAction = snapshotOk && root.statusIsError && root.requiresOnnx;
            if (succeeded && root.pendingAction === "onnx" && root.blockedModelId !== "") {
                // Resume the model selection that triggered the ONNX prompt so
                // the user does not have to pick the model again by hand.
                const resumeModel = root.blockedModelId;
                root.blockedModelId = "";
                root.statusText = "ONNX enabled; resuming model download…";
                root.statusIsError = false;
                Qt.callLater(function() { root.setValue("model", resumeModel); });
                return;
            }
            if (succeeded) {
                root.statusText = root.pendingAction === "clear"
                    ? "Defaults restored; select a model to download."
                    : "Voxtype restarted with the new setting";
                root.statusIsError = false;
            } else if (!(snapshotOk && root.statusIsError)) {
                root.statusText = root.processError(root.writeStderrText, "Could not apply the Voxtype setting");
                root.statusIsError = true;
            }
            // Download progress is transient; restore authoritative model
            // state on every failed write.
            // A structured ONNX error is already the authoritative state for
            // this failed model action. Refreshing here would read the old
            // config and clear `requiresOnnx` before the action link can be
            // displayed.
            if (!succeeded && !requiresOnnxAction) root.refresh();
            root.pendingAction = "";
            root.pendingModelId = "";
            if (succeeded) clearStatus.restart();
        }
    }

    Timer { id: clearStatus; interval: 3500; repeat: false; onTriggered: root.statusText = "" }

    KeyboardPanel {
        id: panel
        anchorItem: root.anchorItem
        owner: root.hostWidget || root
        bar: root.bar
        open: root.opened
        focusTarget: keyCatcher
        contentWidth: panel.fittedContentWidth(Style.space(390))
        contentHeight: panel.fittedContentHeight(content.implicitHeight)

        PanelKeyCatcher {
            id: keyCatcher
            anchors.fill: parent
            onCloseRequested: root.close()

            Column {
                id: content
                width: parent.width
                spacing: Style.space(12)

                Text {
                    text: "Voxtype Enhance"
                    color: root.panelForeground
                    font.family: root.bar ? root.bar.fontFamily : Style.font.family
                    font.pixelSize: Style.font.title
                    font.bold: true
                }
                Text {
                    text: "Configure dictation without leaving Omarchy. Changes restart Voxtype."
                    color: root.panelMuted
                    wrapMode: Text.WordWrap
                    width: parent.width
                    font.family: root.bar ? root.bar.fontFamily : Style.font.family
                    font.pixelSize: Style.font.caption
                }

                Item {
                    width: parent.width
                    height: Math.max(Style.space(42), animationText.implicitHeight + Style.space(8))

                    Column {
                        id: animationText
                        anchors.left: parent.left
                        anchors.right: animationToggle.left
                        anchors.rightMargin: Style.space(12)
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(2)

                        Text {
                            text: "Recording animation"
                            color: root.panelForeground
                            font.family: root.bar ? root.bar.fontFamily : Style.font.family
                            font.pixelSize: Style.font.body
                            font.bold: true
                        }
                        Text {
                            text: "Gently breathe the microphone while listening"
                            color: root.panelMuted
                            wrapMode: Text.WordWrap
                            width: parent.width
                            font.family: root.bar ? root.bar.fontFamily : Style.font.family
                            font.pixelSize: Style.font.caption
                        }
                    }

                    ToggleSwitch {
                        id: animationToggle
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.setting("recordingAnimation", true) === true
                        foreground: root.panelForeground
                        onToggled: {
                            if (root.hostWidget && typeof root.hostWidget.setRecordingAnimationEnabled === "function") {
                                root.hostWidget.setRecordingAnimationEnabled(!checked);
                            }
                        }
                    }
                }

                Text { text: "Model"; color: root.panelMuted; font.pixelSize: Style.font.caption }
                Column {
                    width: parent.width; spacing: Style.space(6)
                    Repeater {
                        model: root.models
                        delegate: Rectangle {
                            required property var modelData
                            width: content.width
                            height: Style.space(48)
                            property bool installed: root.modelInstalled(modelData.id)
                            property bool selected: root.modelId === modelData.id && installed
                            color: root.rowColor(selected)
                            border.width: 1; border.color: selected ? Color.accent : root.panelBorder
                            Column {
                                anchors.fill: parent; anchors.margins: Style.space(7); spacing: 1
                                Text { text: modelData.title; color: root.panelForeground; font.bold: true; font.pixelSize: Style.font.body }
                                Text {
                                    text: root.loading && root.pendingModelId === modelData.id
                                        ? (root.downloadProgress >= 0
                                            ? "Downloading and verifying · " + Math.round(root.downloadProgress * 100) + "%"
                                            : "Downloading and verifying…")
                                        : (installed
                                            ? modelData.detail + (selected ? " · active" : " · downloaded, not active")
                                            : "Not downloaded · click to download")
                                    color: root.loading && root.pendingModelId === modelData.id ? Color.accent : root.panelMuted
                                    font.pixelSize: Style.font.caption
                                }
                            }
                            Rectangle {
                                id: progressTrack
                                visible: root.loading && root.pendingModelId === modelData.id
                                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                height: Style.space(3)
                                color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.2)
                                clip: true
                                Rectangle {
                                    height: parent.height
                                    width: root.downloadProgress >= 0 ? parent.width * root.downloadProgress : parent.width * 0.32
                                    color: Color.accent
                                    x: root.downloadProgress >= 0 ? 0 : -width
                                    SequentialAnimation on x {
                                        running: progressTrack.visible && root.downloadProgress < 0
                                        loops: Animation.Infinite
                                        NumberAnimation { from: -progressTrack.width * 0.32; to: progressTrack.width; duration: 1000; easing.type: Easing.InOutQuad }
                                    }
                                }
                            }
                            MouseArea { anchors.fill: parent; onClicked: root.setValue("model", modelData.id) }
                        }
                    }
                }

                Text {
                    visible: root.installedModels.length > 0
                    text: "Language"; color: root.panelMuted; font.pixelSize: Style.font.caption
                }
                Row {
                    visible: root.installedModels.length > 0
                    width: parent.width; spacing: Style.space(6)
                    Repeater {
                        model: root.languages
                        delegate: Rectangle {
                            required property var modelData
                            width: (content.width - Style.space(24)) / 5
                            height: Style.space(34)
                            color: root.rowColor(root.language === modelData.key)
                            border.width: 1; border.color: root.language === modelData.key ? Color.accent : root.panelBorder
                            Text { anchors.centerIn: parent; text: modelData.title; color: root.panelForeground; font.pixelSize: Style.font.caption }
                            MouseArea { anchors.fill: parent; onClicked: root.setValue("language", modelData.key) }
                        }
                    }
                }

                Text {
                    visible: root.installedModels.length > 0
                    text: "Output"; color: root.panelMuted; font.pixelSize: Style.font.caption
                }
                Row {
                    visible: root.installedModels.length > 0
                    width: parent.width; spacing: Style.space(6)
                    Repeater {
                        model: [
                            { key: "universal", title: "Omarchy universal paste", detail: "Terminal-aware clipboard insertion" },
                            { key: "type", title: "Type", detail: "Simulated keyboard input" }
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            width: (content.width - Style.space(6)) / 2
                            height: Style.space(52)
                            color: root.rowColor(root.outputMode === modelData.key)
                            border.width: 1; border.color: root.outputMode === modelData.key ? Color.accent : root.panelBorder
                            Column { anchors.fill: parent; anchors.margins: Style.space(7); spacing: 1
                                Text { text: modelData.title; color: root.panelForeground; font.bold: true; font.pixelSize: Style.font.caption }
                                Text {
                                    text: modelData.detail
                                    color: root.panelMuted
                                    font.pixelSize: Style.font.caption
                                    width: parent.width
                                    elide: Text.ElideRight
                                }
                            }
                            MouseArea { anchors.fill: parent; onClicked: root.setValue("mode", modelData.key) }
                        }
                    }
                }

                Text {
                    text: root.statusText.length > 0 ? root.statusText : "Universal paste uses Shift+Insert in terminals and Ctrl+V in graphical apps."
                    color: root.statusIsError ? Color.urgent : (root.statusText.length > 0 ? Color.accent : root.panelMuted)
                    wrapMode: Text.WordWrap; width: parent.width
                    font.pixelSize: Style.font.caption
                }

                Text {
                    visible: root.requiresOnnx && root.onnxSetupSupported && !root.loading
                    text: "Enable ONNX support (administrator approval required)"
                    color: Color.accent
                    font.underline: true
                    wrapMode: Text.WordWrap
                    width: parent.width
                    font.pixelSize: Style.font.caption
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.enableOnnx()
                    }
                }

                Text {
                    text: "Clear plugin data"
                    color: root.loading ? root.panelMuted : Color.accent
                    font.pixelSize: Style.font.caption
                    opacity: root.loading ? 0.5 : 1
                    MouseArea {
                        anchors.fill: parent
                        enabled: !root.loading
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.clearPluginData()
                    }
                }
            }
        }
    }
}
