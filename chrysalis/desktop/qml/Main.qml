import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    flags: Qt.Window | Qt.FramelessWindowHint
    width: 1500
    height: 920
    minimumWidth: 1100
    minimumHeight: 720
    title: "Chrysalis"
    color: theme.bg
    property string renameSessionId: ""
    property string activePage: "chat"

    onVisibilityChanged: {
        if (window.visibility === Window.Minimized) {
            return
        }
    }

    onClosing: function(close) {
        if (backend) {
            backend.shutdown()
        }
    }

    QtObject {
        id: settingsState
        property bool enabled: false
        property string name: ""
        property string provider: "openai"
        property string apiKey: ""
        property string baseUrl: ""
        property string model: ""
        property string contextWindow: "28000"
        property string temperature: "0.2"
        property string maxTokens: ""
        property string maxRetries: "4"
        property string timeout: "60"
        property string proxy: ""
        property string thinking: "disabled"
        property string thinkingBudget: ""
        property string systemPrompt: ""
    }

    function addDroppedFiles(drop) {
        var added = 0
        if (drop.urls && drop.urls.length > 0) {
            for (var i = 0; i < drop.urls.length; i++) {
                if (backend.add_attachment(drop.urls[i].toString())) {
                    added += 1
                }
            }
        }
        drop.accepted = added > 0
        return added
    }

    function submitTask() {
        var raw = taskInput.text.trim()
        if ((raw.length > 0 || (backend && backend.attachment_count > 0)) && !(backend && backend.busy_state)) {
            backend.run_task(raw)
            taskInput.text = ""
        }
    }

    function parseIntOrFallback(text, fallback) {
        var value = parseInt(String(text).trim())
        return isNaN(value) ? fallback : value
    }

    function parseFloatOrFallback(text, fallback) {
        var value = parseFloat(String(text).trim())
        return isNaN(value) ? fallback : value
    }

    function openSettingsPage() {
        var data = {}
        try {
            data = JSON.parse(backend.load_settings_text())
        } catch (e) {
            data = {}
        }
        var llm = data.llm || {}
        settingsState.enabled = data.enabled === true
        settingsState.name = llm.name || ""
        settingsState.provider = llm.provider || "openai"
        settingsState.apiKey = llm.api_key || ""
        settingsState.baseUrl = llm.base_url || ""
        settingsState.model = llm.model || ""
        settingsState.contextWindow = String(llm.context_window || 28000)
        settingsState.temperature = String(llm.temperature !== undefined ? llm.temperature : 0.2)
        settingsState.maxTokens = llm.max_tokens === null || llm.max_tokens === undefined ? "" : String(llm.max_tokens)
        settingsState.maxRetries = String(llm.max_retries || 4)
        settingsState.timeout = String(llm.timeout || 60)
        settingsState.proxy = llm.proxy || ""
        settingsState.thinking = llm.thinking || "disabled"
        settingsState.thinkingBudget = llm.thinking_budget === null || llm.thinking_budget === undefined ? "" : String(llm.thinking_budget)
        settingsState.systemPrompt = data.system_prompt || ""
        if (settingsEnabled) settingsEnabled.checked = settingsState.enabled
        if (settingsNameField) settingsNameField.value = settingsState.name
        if (settingsProviderField) settingsProviderField.value = settingsState.provider
        if (settingsApiKeyField) settingsApiKeyField.value = settingsState.apiKey
        if (settingsBaseUrlField) settingsBaseUrlField.value = settingsState.baseUrl
        if (settingsModelField) settingsModelField.value = settingsState.model
        if (settingsContextWindowField) settingsContextWindowField.value = settingsState.contextWindow
        if (settingsTemperatureField) settingsTemperatureField.value = settingsState.temperature
        if (settingsMaxTokensField) settingsMaxTokensField.value = settingsState.maxTokens
        if (settingsMaxRetriesField) settingsMaxRetriesField.value = settingsState.maxRetries
        if (settingsTimeoutField) settingsTimeoutField.value = settingsState.timeout
        if (settingsProxyField) settingsProxyField.value = settingsState.proxy
        if (settingsThinkingField) settingsThinkingField.value = settingsState.thinking
        if (settingsThinkingBudgetField) settingsThinkingBudgetField.value = settingsState.thinkingBudget
        if (settingsSystemPromptField) settingsSystemPromptField.value = settingsState.systemPrompt
        activePage = "settings"
    }

    function saveSettingsPage() {
        var payload = {
            enabled: settingsState.enabled,
            llm: {
                name: settingsState.name,
                provider: settingsState.provider,
                api_key: settingsState.apiKey,
                base_url: settingsState.baseUrl,
                model: settingsState.model,
                context_window: parseIntOrFallback(settingsState.contextWindow, 28000),
                temperature: parseFloatOrFallback(settingsState.temperature, 0.2),
                max_tokens: settingsState.maxTokens.trim().length > 0 ? parseIntOrFallback(settingsState.maxTokens, 0) : null,
                max_retries: parseIntOrFallback(settingsState.maxRetries, 4),
                timeout: parseIntOrFallback(settingsState.timeout, 60),
                proxy: settingsState.proxy,
                thinking: settingsState.thinking,
                thinking_budget: settingsState.thinkingBudget.trim().length > 0 ? parseIntOrFallback(settingsState.thinkingBudget, 0) : null
            },
            system_prompt: settingsState.systemPrompt
        }
        if (backend.save_settings_text(JSON.stringify(payload))) {
            activePage = "chat"
        }
    }

    function resetSettingsPage() {
        backend.reset_settings()
        openSettingsPage()
    }

    Shortcut {
        sequence: "Ctrl+C"
        context: Qt.ApplicationShortcut
        onActivated: backend.cancel_active_task()
    }

    Shortcut {
        sequence: "Ctrl+K"
        context: Qt.ApplicationShortcut
        onActivated: commandPalette.open()
    }

    Shortcut {
        sequence: "Ctrl+,"
        context: Qt.ApplicationShortcut
        onActivated: openSettingsPage()
    }

    QtObject {
        id: theme
        readonly property color bg: "#000000"
        readonly property color shell: "#0b0b10"
        readonly property color sidebar: "#101019"
        readonly property color panel: "#151522"
        readonly property color panelHover: "#202034"
        readonly property color line: "#2b2b3f"
        readonly property color text: "#cdd6f4"
        readonly property color muted: "#585b70"
        readonly property color faint: "#414558"
        readonly property color purple: "#b4befe"
        readonly property color blue: "#89b4fa"
        readonly property color green: "#a6e3a1"
        readonly property color red: "#f38ba8"
        readonly property color yellow: "#f9e2af"
        readonly property string mono: "Consolas"
    }

    component TogglePill: Rectangle {
        id: root
        property string text: ""
        property bool active: false
        signal clicked()

        implicitHeight: 24
        radius: 4
        color: active ? theme.panelHover : (area.containsMouse ? theme.shell : "transparent")
        border.color: active ? theme.purple : theme.line
        border.width: 1

        Text {
            anchors.centerIn: parent
            text: root.text
            color: root.active ? theme.text : theme.muted
            font.family: theme.mono
            font.pixelSize: 11
            font.bold: root.active
        }

        MouseArea {
            id: area
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.clicked()
        }
    }

    component TaskJumpRow: Rectangle {
        id: root
        property string title: ""
        property string summary: ""
        property string status: ""
        signal clicked()

        height: 36
        radius: 4
        color: area.containsMouse ? theme.panelHover : "transparent"
        border.color: theme.line
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 1

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: root.title
                    color: theme.text
                    font.family: theme.mono
                    font.pixelSize: 10
                    font.bold: true
                    elide: Text.ElideRight
                }

                Text {
                    text: root.status
                    color: theme.purple
                    font.family: theme.mono
                    font.pixelSize: 9
                }
            }

            Text {
                Layout.fillWidth: true
                text: root.summary
                color: theme.muted
                font.family: theme.mono
                font.pixelSize: 9
                elide: Text.ElideRight
            }
        }

        MouseArea {
            id: area
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.clicked()
        }
    }

    component TodoLine: Rectangle {
        id: root
        property string title: ""
        property string note: ""
        property string status: ""
        property bool active: false

        height: Math.max(36, lineColumn.implicitHeight + 12)
        radius: 4
        color: active ? theme.panelHover : "transparent"
        border.color: active ? theme.purple : theme.line
        border.width: 1

        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 6

            Text {
                text: status === "completed" ? "✓" : "•"
                color: active ? theme.purple : theme.muted
                font.family: theme.mono
                font.pixelSize: 11
            }

            ColumnLayout {
                id: lineColumn
                Layout.fillWidth: true
                spacing: 2

                Text {
                    Layout.fillWidth: true
                    text: root.title
                    color: theme.text
                    font.family: theme.mono
                    font.pixelSize: 12
                    font.bold: active
                    wrapMode: Text.Wrap
                }

                Text {
                    visible: root.note.length > 0
                    Layout.fillWidth: true
                    text: root.note
                    color: theme.muted
                    font.family: theme.mono
                    font.pixelSize: 10
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    component TodoPanel: Rectangle {
        id: root
        property var snapshot: ({})

        radius: 4
        color: theme.panel
        border.color: theme.line
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "Todo"
                    color: theme.text
                    font.family: theme.mono
                    font.pixelSize: 13
                    font.bold: true
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: (root.snapshot.pending_count || 0) + "/" + (root.snapshot.total_count || 0)
                    color: theme.purple
                    font.family: theme.mono
                    font.pixelSize: 11
                }
            }

            Text {
                Layout.fillWidth: true
                visible: !!root.snapshot.goal
                text: root.snapshot.goal || ""
                color: theme.muted
                font.family: theme.mono
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            Text {
                Layout.fillWidth: true
                text: "Round " + (root.snapshot.rounds_since_todo || 0) + " / " + (root.snapshot.todo_reminder_interval || 4)
                color: theme.faint
                font.family: theme.mono
                font.pixelSize: 10
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: theme.line
            }

            ListView {
                id: todoList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: root.snapshot.todos || []
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                    active: true
                    width: 8
                }

                delegate: TodoLine {
                    width: todoList.width
                    title: modelData.title || ""
                    note: modelData.note || ""
                    status: modelData.status || ""
                    active: modelData.id === (root.snapshot.active_todo_id || "")
                }
            }

            Text {
                Layout.fillWidth: true
                visible: !root.snapshot.todos || root.snapshot.todos.length === 0
                text: "No TODOs yet"
                color: theme.muted
                font.family: theme.mono
                font.pixelSize: 10
            }
        }
    }

    component TodoSidebar: Rectangle {
        id: root
        property var snapshot: ({})

        radius: 4
        color: theme.sidebar
        border.color: theme.line
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "Task TODO"
                    color: theme.text
                    font.family: theme.mono
                    font.pixelSize: 12
                    font.bold: true
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: (root.snapshot.pending_count || 0) + "/" + (root.snapshot.total_count || 0)
                    color: theme.purple
                    font.family: theme.mono
                    font.pixelSize: 10
                }
            }

            TodoPanel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                snapshot: root.snapshot
            }
        }
    }

    component TinyButton: Rectangle {
        id: root
        property string text: ""
        property bool primary: false
        signal clicked()

        height: 30
        radius: 4
        color: primary ? theme.purple : (area.containsMouse ? theme.panelHover : "transparent")
        border.color: primary ? theme.purple : theme.line
        border.width: 1

        Text {
            anchors.centerIn: parent
            text: root.text
            color: root.primary ? "#000000" : theme.text
            font.family: theme.mono
            font.pixelSize: 12
            font.bold: root.primary
        }

        MouseArea {
            id: area
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.clicked()
        }
    }

    Dialog {
        id: renameDialog
        modal: true
        title: "Rename session"
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 420
        anchors.centerIn: parent
        background: Rectangle {
            color: theme.panel
            radius: 6
            border.color: theme.line
        }
        contentItem: TextField {
            id: renameInput
            color: theme.text
            placeholderText: "Session title"
            placeholderTextColor: theme.muted
            font.family: theme.mono
            font.pixelSize: 14
            background: Rectangle {
                color: theme.shell
                radius: 4
                border.color: renameInput.activeFocus ? theme.purple : theme.line
            }
        }
        onAccepted: {
            if (renameSessionId.length > 0 && renameInput.text.trim().length > 0) {
                backend.rename_session(renameSessionId, renameInput.text.trim())
            }
            renameSessionId = ""
        }
        onRejected: renameSessionId = ""
    }

    FileDialog {
        id: attachmentDialog
        title: "Attach files"
        fileMode: FileDialog.OpenFiles
        onAccepted: {
            for (var i = 0; i < selectedFiles.length; i++) {
                backend.add_attachment(selectedFiles[i].toString())
            }
        }
    }

    component WindowButton: Rectangle {
        id: root
        property string text: ""
        property bool danger: false
        signal clicked()

        width: 42
        height: 24
        radius: 4
        color: hover.containsMouse ? (root.danger ? "#332126" : theme.panelHover) : "transparent"
        border.color: "transparent"

        Text {
            anchors.centerIn: parent
            text: root.text
            color: root.danger && hover.containsMouse ? theme.red : theme.text
            font.family: theme.mono
            font.pixelSize: 13
            font.bold: true
        }

        MouseArea {
            id: hover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.clicked()
        }
    }

    component SectionLabel: Text {
        color: theme.muted
        font.family: theme.mono
        font.pixelSize: 12
        font.bold: true
        text: ""
    }

    component SettingField: ColumnLayout {
        id: root
        property string label: ""
        property string value: ""
        property string placeholder: ""
        property bool multiline: false
        property bool secret: false
        property bool readOnly: false
        property int preferredHeight: multiline ? 188 : 78

        spacing: 4
        implicitHeight: root.preferredHeight

        function syncValue() {
            if (root.multiline) {
                if (area.text !== root.value) {
                    area.text = root.value
                }
            } else if (editor.text !== root.value) {
                editor.text = root.value
            }
        }

        Component.onCompleted: syncValue()
        onValueChanged: syncValue()

        Text {
            Layout.fillWidth: true
            text: root.label
            color: theme.muted
            font.family: theme.mono
            font.pixelSize: 12
            font.bold: true
        }

        TextField {
            id: editor
            visible: !root.multiline
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            color: theme.text
            placeholderText: root.placeholder
            placeholderTextColor: theme.muted
            echoMode: root.secret ? TextInput.Password : TextInput.Normal
            readOnly: root.readOnly
            font.family: theme.mono
            font.pixelSize: 13
            background: Rectangle {
                color: theme.shell
                radius: 4
                border.color: editor.activeFocus ? theme.purple : theme.line
                border.width: 1
            }
            leftPadding: 8
            rightPadding: 8
            topPadding: 8
            bottomPadding: 8
            onTextChanged: {
                if (root.value !== text) {
                    root.value = text
                }
            }
        }

        TextArea {
            id: area
            visible: root.multiline
            Layout.fillWidth: true
            Layout.preferredHeight: 150
            color: theme.text
            placeholderText: root.placeholder
            placeholderTextColor: theme.muted
            readOnly: root.readOnly
            wrapMode: TextArea.Wrap
            font.family: theme.mono
            font.pixelSize: 13
            background: Rectangle {
                color: theme.shell
                radius: 4
                border.color: area.activeFocus ? theme.purple : theme.line
                border.width: 1
            }
            leftPadding: 8
            rightPadding: 8
            topPadding: 8
            bottomPadding: 8
            onTextChanged: {
                if (root.value !== text) {
                    root.value = text
                }
            }
        }
    }

    Popup {
        id: commandPalette
        modal: true
        focus: true
        width: Math.min(620, window.width - 80)
        height: Math.min(420, window.height - 120)
        anchors.centerIn: parent
        background: Rectangle {
            color: theme.panel
            radius: 6
            border.color: theme.purple
            border.width: 1
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 10

            TextField {
                id: paletteInput
                Layout.fillWidth: true
                placeholderText: "Command or session search..."
                color: theme.text
                placeholderTextColor: theme.muted
                font.family: theme.mono
                font.pixelSize: 14
                background: Rectangle {
                    color: theme.shell
                    radius: 4
                    border.color: theme.line
                }
                onAccepted: {
                    if (text.trim().length > 0) {
                        taskInput.text = text.trim()
                        commandPalette.close()
                        taskInput.forceActiveFocus()
                    }
                }
                onTextChanged: backend.set_session_filter(text)
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                TinyButton {
                    Layout.fillWidth: true
                    text: "New chat"
                    primary: true
                    onClicked: {
                        backend.new_session()
                        commandPalette.close()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.line
            }
        }

        onOpened: {
            paletteInput.text = ""
            paletteInput.forceActiveFocus()
        }
        onClosed: backend.set_session_filter(sessionSearch.text)
    }

    component SessionRow: Rectangle {
        id: root
        property string sessionId: ""
        property string title: ""
        property string updatedAt: ""
        property bool busy: false
        property bool active: false
        property bool pinned: false

        width: parent.width
        height: 52
        radius: 4
        color: active ? theme.panelHover : (area.containsMouse ? "#14141d" : "transparent")
        border.color: active ? theme.purple : "transparent"
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 8
            anchors.topMargin: 6
            anchors.bottomMargin: 6
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    Layout.fillWidth: true
                    text: root.title || "Untitled session"
                    color: theme.text
                    font.family: theme.mono
                    font.pixelSize: 12
                    font.bold: root.active
                    elide: Text.ElideRight
                }

                Text {
                    visible: !root.busy && root.pinned
                    text: "pin"
                    color: theme.yellow
                    font.family: theme.mono
                    font.pixelSize: 11
                }

                SessionSpinner {
                    running: root.busy
                }

                Text {
                    visible: root.busy
                    text: "running"
                    color: root.busy ? theme.blue : theme.yellow
                    font.family: theme.mono
                    font.pixelSize: 11
                }
            }

            Text {
                Layout.fillWidth: true
                text: root.updatedAt
                color: theme.muted
                font.family: theme.mono
                font.pixelSize: 11
                elide: Text.ElideRight
            }
        }

        MouseArea {
            id: area
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                sessionList.currentIndex = index
                backend.activate_session_row(index)
            }
        }
    }

    component SessionSpinner: Item {
        id: root
        property bool running: false

        width: 12
        height: 12
        visible: running

        Rectangle {
            anchors.fill: parent
            radius: 6
            color: "transparent"
            border.color: theme.blue
            border.width: 2
            opacity: 0.35
        }

        Rectangle {
            width: 3
            height: 5
            radius: 1.5
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            color: theme.blue
        }

        RotationAnimator on rotation {
            from: 0
            to: 360
            duration: 900
            loops: Animation.Infinite
            running: root.visible
        }
    }

    component LogRow: Item {
        id: root
        property int rowIndex: -1
        property string kind: ""
        property string role: ""
        property string content: ""
        property string title: ""
        property string summary: ""
        property var details: []
        property bool expanded: false
        property string status: ""
        property bool streaming: false

        width: logList.width
        implicitHeight: kind === "spacer" ? 12 : logColumn.implicitHeight

        readonly property bool isTurn: kind === "turn"
        readonly property bool isUser: kind === "user"
        readonly property bool isFinal: kind === "final"
        readonly property bool isStream: kind === "stream"
        readonly property bool isUsage: kind === "usage"
        readonly property bool isWarning: kind === "warning"
        readonly property bool isSystem: kind === "system"

        ColumnLayout {
            id: logColumn
            width: parent.width
            visible: kind !== "spacer"
            spacing: 4

            Text {
                visible: root.isUser
                Layout.fillWidth: true
                text: "> " + root.content
                color: theme.text
                font.family: theme.mono
                font.pixelSize: 14
                font.bold: true
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Text {
                visible: root.isSystem || root.isUsage || root.isWarning
                Layout.fillWidth: true
                text: root.content
                color: root.isWarning ? theme.yellow : theme.muted
                font.family: theme.mono
                font.pixelSize: 12
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Text {
                visible: root.isStream
                Layout.fillWidth: true
                text: root.content + "▌"
                color: theme.muted
                font.pixelSize: 13
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Item {
                visible: root.isTurn
                Layout.fillWidth: true
                implicitHeight: turnColumn.implicitHeight

                ColumnLayout {
                    id: turnColumn
                    width: parent.width
                    spacing: 2

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: turnHeader.implicitHeight + 8
                        color: turnArea.containsMouse ? "#080810" : "transparent"
                        border.color: "transparent"

                        Text {
                            id: turnHeader
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 10
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: (root.expanded ? "▾ " : "▸ ") + root.title
                            color: theme.muted
                            font.family: theme.mono
                            font.pixelSize: 12
                            font.bold: true
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        MouseArea {
                            id: turnArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: backend.active_session_object.messages_model_object.toggle_expanded(root.rowIndex)
                        }
                    }

                    ColumnLayout {
                        visible: root.expanded
                        Layout.fillWidth: true
                        Layout.leftMargin: 18
                        spacing: 2

                        Repeater {
                            model: root.details
                            delegate: Text {
                                Layout.fillWidth: true
                                text: modelData
                                color: theme.muted
                                font.family: theme.mono
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            visible: root.details.length === 0
                            Layout.fillWidth: true
                            text: "(empty)"
                            color: theme.muted
                            font.family: theme.mono
                            font.pixelSize: 12
                        }
                    }
                }
            }

            TextEdit {
                visible: root.isFinal
                Layout.fillWidth: true
                text: root.content
                color: theme.text
                font.family: theme.mono
                font.pixelSize: 14
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.MarkdownText
                readOnly: true
                padding: 0
                leftPadding: 0
                rightPadding: 0
                topPadding: 0
                bottomPadding: 0
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: theme.bg

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                color: theme.shell
                border.color: theme.line
                border.width: 1

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    onPressed: window.startSystemMove()
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 8
                    spacing: 8

                    Text {
                        text: "Chrysalis"
                        color: theme.text
                        font.family: theme.mono
                        font.pixelSize: 13
                        font.bold: true
                    }

                    TogglePill {
                        Layout.preferredWidth: 78
                        text: "Settings"
                        active: activePage === "settings"
                        onClicked: {
                            if (activePage !== "settings") {
                                openSettingsPage()
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Text {
                        text: backend ? backend.status_text : "ready"
                        color: backend && backend.busy_state ? theme.purple : theme.muted
                        font.family: theme.mono
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }

                    Text {
                        text: "v" + appVersion
                        color: theme.muted
                        font.family: theme.mono
                        font.pixelSize: 11
                    }

                    WindowButton {
                        text: "—"
                        onClicked: window.showMinimized()
                    }

                    WindowButton {
                        text: window.visibility === Window.Maximized ? "❐" : "□"
                        onClicked: {
                            if (window.visibility === Window.Maximized) window.showNormal()
                            else window.showMaximized()
                        }
                    }

                    WindowButton {
                        text: "×"
                        danger: true
                        onClicked: window.close()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.preferredWidth: 320
                    Layout.minimumWidth: 300
                    Layout.maximumWidth: 360
                    Layout.fillHeight: true
                    color: theme.sidebar
                    border.color: theme.line
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Text {
                                Layout.fillWidth: true
                                text: "Chrysalis"
                                color: theme.purple
                                font.family: theme.mono
                                font.pixelSize: 20
                                font.bold: true
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: backend ? backend.model_name_text : ""
                            color: theme.muted
                            font.family: theme.mono
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TinyButton {
                                Layout.fillWidth: true
                                text: "New chat"
                                primary: true
                                onClicked: backend.new_session()
                            }

                            TinyButton {
                                Layout.preferredWidth: 78
                                text: "Refresh"
                                onClicked: backend.refresh_sessions()
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            height: 1
                            color: theme.line
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Sessions"
                            color: theme.muted
                            font.family: theme.mono
                            font.pixelSize: 12
                            font.bold: true
                        }

                        TextField {
                            id: sessionSearch
                            Layout.fillWidth: true
                            placeholderText: "Search sessions..."
                            color: theme.text
                            placeholderTextColor: theme.muted
                            font.family: theme.mono
                            font.pixelSize: 12
                            background: Rectangle {
                                color: theme.shell
                                radius: 4
                                border.color: sessionSearch.activeFocus ? theme.purple : theme.line
                                border.width: 1
                            }
                            leftPadding: 8
                            rightPadding: 8
                            onTextChanged: backend.set_session_filter(text)
                        }

                        ListView {
                            id: sessionList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            model: backend ? backend.sessions_model_object : []
                            clip: true
                            spacing: 2
                            boundsBehavior: Flickable.StopAtBounds

                            ScrollBar.vertical: ScrollBar {
                                policy: ScrollBar.AsNeeded
                                active: true
                                width: 8
                            }

                            Component.onCompleted: {
                                currentIndex = backend ? backend.active_session_index : -1
                            }

                            delegate: SessionRow {
                                sessionId: model.sessionId
                                title: model.title
                                updatedAt: model.updatedAt
                                busy: model.busy
                                active: index === (backend ? backend.active_session_index : -1)
                                pinned: model.pinned
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TinyButton {
                                Layout.fillWidth: true
                                text: "Pin"
                                onClicked: {
                                    if (sessionList.currentIndex >= 0) {
                                        var idx = sessionList.model.index(sessionList.currentIndex, 0)
                                        var sid = sessionList.model.data(idx, Qt.UserRole + 1)
                                        backend.toggle_session_pinned(sid)
                                    }
                                }
                            }

                            TinyButton {
                                Layout.fillWidth: true
                                text: "Rename"
                                onClicked: {
                                    if (sessionList.currentIndex >= 0) {
                                        var idx = sessionList.model.index(sessionList.currentIndex, 0)
                                        renameSessionId = sessionList.model.data(idx, Qt.UserRole + 1)
                                        renameInput.text = sessionList.model.data(idx, Qt.UserRole + 2)
                                        renameDialog.open()
                                    }
                                }
                            }

                            TinyButton {
                                Layout.fillWidth: true
                                text: "Delete"
                                onClicked: {
                                    if (sessionList.currentIndex >= 0) {
                                        var sid = sessionList.model.data(sessionList.model.index(sessionList.currentIndex, 0), Qt.UserRole + 1)
                                        if (sid) {
                                            backend.delete_session(sid)
                                        }
                                    }
                                }
                            }
                        }

                        Connections {
                            target: backend
                            function onActiveSessionChanged() {
                                if (window.visibility !== Window.Minimized) {
                                    sessionList.currentIndex = backend.active_session_index
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    StackLayout {
                        id: mainStack
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: activePage === "chat" ? 0 : 1

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 18
                                anchors.rightMargin: 10
                                anchors.topMargin: 14
                                anchors.bottomMargin: 14
                                spacing: 12

                                Item {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true

                                    ListView {
                                        id: logList
                                        anchors.fill: parent
                                        anchors.bottomMargin: chatFooter.height + 14
                                        model: backend ? backend.active_session_object.messages_model_object : null
                                        clip: true
                                        spacing: 0
                                        boundsBehavior: Flickable.StopAtBounds

                                        ScrollBar.vertical: ScrollBar {
                                            policy: ScrollBar.AlwaysOn
                                            active: true
                                            width: 10
                                        }

                                        property bool stickToBottom: true
                                        onContentYChanged: stickToBottom = contentHeight - (contentY + height) < 50
                                        onCountChanged: if (stickToBottom) positionViewAtEnd()
                                        onContentHeightChanged: if (stickToBottom) positionViewAtEnd()
                                        onModelChanged: positionViewAtEnd()

                                        delegate: LogRow {
                                            rowIndex: index
                                            kind: model.kind
                                            role: model.role
                                            content: model.content
                                            title: model.title
                                            summary: model.summary
                                            details: model.details
                                            expanded: model.expanded
                                            status: model.status
                                            streaming: model.streaming
                                        }
                                    }

                                    Rectangle {
                                        id: chatFooter
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        height: backend && backend.attachment_count > 0 ? 100 : 54
                                        color: theme.bg
                                        border.color: theme.line
                                        border.width: 1

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 12
                                            anchors.rightMargin: 12
                                            anchors.topMargin: 8
                                            anchors.bottomMargin: 8
                                            spacing: 6

                                            Flickable {
                                                id: attachmentStrip
                                                visible: backend && backend.attachment_count > 0
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: visible ? 34 : 0
                                                contentWidth: attachmentRow.implicitWidth
                                                contentHeight: height
                                                clip: true
                                                boundsBehavior: Flickable.StopAtBounds

                                                RowLayout {
                                                    id: attachmentRow
                                                    height: parent.height
                                                    spacing: 6

                                                    Repeater {
                                                        model: backend ? backend.attachments_model_object : null

                                                        delegate: Rectangle {
                                                            Layout.preferredWidth: Math.min(260, Math.max(130, attachmentText.implicitWidth + removeAttachment.implicitWidth + 30))
                                                            Layout.preferredHeight: 28
                                                            radius: 4
                                                            color: theme.shell
                                                            border.color: theme.line
                                                            border.width: 1

                                                            RowLayout {
                                                                anchors.fill: parent
                                                                anchors.leftMargin: 8
                                                                anchors.rightMargin: 4
                                                                spacing: 6

                                                                Text {
                                                                    id: attachmentText
                                                                    Layout.fillWidth: true
                                                                    text: model.kind + ": " + model.name
                                                                    color: theme.text
                                                                    font.family: theme.mono
                                                                    font.pixelSize: 11
                                                                    elide: Text.ElideRight
                                                                }

                                                                Text {
                                                                    id: removeAttachment
                                                                    text: "x"
                                                                    color: removeArea.containsMouse ? theme.red : theme.muted
                                                                    font.family: theme.mono
                                                                    font.pixelSize: 12
                                                                    font.bold: true

                                                                    MouseArea {
                                                                        id: removeArea
                                                                        anchors.fill: parent
                                                                        anchors.margins: -6
                                                                        hoverEnabled: true
                                                                        cursorShape: Qt.PointingHandCursor
                                                                        onClicked: backend.remove_attachment(index)
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                spacing: 8

                                                Text {
                                                    text: ">"
                                                    color: theme.purple
                                                    font.family: theme.mono
                                                    font.pixelSize: 18
                                                    font.bold: true
                                                }

                                                TextField {
                                                    id: taskInput
                                                    Layout.fillWidth: true
                                                    enabled: !(backend && backend.busy_state)
                                                    placeholderText: "Type a task..."
                                                    color: theme.text
                                                    placeholderTextColor: theme.muted
                                                    font.family: theme.mono
                                                    font.pixelSize: 14
                                                    background: Rectangle { color: "transparent" }
                                                    Component.onCompleted: text = backend ? backend.draft_text : ""
                                                    Connections {
                                                        target: backend
                                                        function onActiveSessionChanged() {
                                                            if (backend) {
                                                                taskInput.text = backend.draft_text
                                                            }
                                                        }
                                                    }
                                                    onTextChanged: if (backend) backend.save_draft(text)
                                                    onAccepted: submitTask()
                                                }

                                                TinyButton {
                                                    Layout.preferredWidth: 82
                                                    text: "Attach"
                                                    onClicked: attachmentDialog.open()
                                                }

                                                TinyButton {
                                                    Layout.preferredWidth: 82
                                                    text: backend && backend.busy_state ? "Running" : "Send"
                                                    primary: !(backend && backend.busy_state)
                                                    onClicked: submitTask()
                                                }
                                            }
                                        }
                                    }

                                    Item {
                                        id: todoOverlay
                                        anchors.top: parent.top
                                        anchors.right: parent.right
                                        anchors.topMargin: 14
                                        anchors.rightMargin: 18
                                        width: 420
                                        height: Math.min(parent.height * 0.58, 520)
                                        z: 18
                                        visible: backend
                                                 && backend.busy_state
                                                 && backend.active_session_object
                                                 && backend.active_session_object.working_snapshot
                                                 && backend.active_session_object.working_snapshot.todos
                                                 && backend.active_session_object.working_snapshot.todos.length > 0

                                        TodoPanel {
                                            id: todoOverlayContent
                                            anchors.fill: parent
                                            snapshot: backend && backend.active_session_object ? backend.active_session_object.working_snapshot : ({})
                                        }
                                    }

                                Item {
                                    id: taskNavHost
                                    anchors.top: parent.top
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    width: taskPanel.visible ? 284 : 8
                                    z: 20

                                            property bool navOpen: false

                                            Timer {
                                                id: navCloseTimer
                                                interval: 160
                                                repeat: false
                                                onTriggered: taskNavHost.navOpen = false
                                            }

                                            HoverHandler {
                                                id: navHoverHandler
                                                target: taskNavHost
                                                acceptedDevices: PointerDevice.Mouse
                                                onHoveredChanged: {
                                                    if (hovered) {
                                                        navCloseTimer.stop()
                                                        taskNavHost.navOpen = true
                                                    } else {
                                                        navCloseTimer.restart()
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                id: taskPanel
                                                visible: taskNavHost.navOpen
                                                anchors.top: parent.top
                                                anchors.bottom: parent.bottom
                                                anchors.right: parent.right
                                                width: 280
                                                radius: 6
                                                color: theme.sidebar
                                                border.color: theme.line
                                                border.width: 1

                                                ColumnLayout {
                                                    anchors.fill: parent
                                                    anchors.margins: 10
                                                    spacing: 8

                                                    RowLayout {
                                                        Layout.fillWidth: true

                                                        Text {
                                                            text: "Tasks"
                                                            color: theme.text
                                                            font.family: theme.mono
                                                            font.pixelSize: 12
                                                            font.bold: true
                                                        }

                                                        Item { Layout.fillWidth: true }

                                                        Text {
                                                            text: "jump"
                                                            color: theme.muted
                                                            font.family: theme.mono
                                                            font.pixelSize: 10
                                                        }
                                                    }

                                                    ListView {
                                                        id: turnNavList
                                                        Layout.fillWidth: true
                                                        Layout.fillHeight: true
                                                        clip: true
                                                        spacing: 6
                                                        model: backend ? backend.active_session_object.turns_model_object : null
                                                        boundsBehavior: Flickable.StopAtBounds

                                                        ScrollBar.vertical: ScrollBar {
                                                            policy: ScrollBar.AsNeeded
                                                            active: true
                                                            width: 8
                                                        }

                                        delegate: TaskJumpRow {
                                                            width: turnNavList.width
                                                            title: model.title
                                                            summary: model.summary
                                                            status: model.status
                                                            onClicked: {
                                                                logList.positionViewAtIndex(model.rowIndex, ListView.Beginning)
                                                            }
                                                        }
                                                    }
                                                }
                                            }

                                        Rectangle {
                                            id: spine
                                            visible: true
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.right: parent.right
                                            width: 4
                                            height: 120
                                            radius: 2
                                            color: theme.purple
                                        }
                                    }
                                }
                            }
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Rectangle {
                                anchors.fill: parent
                                color: theme.bg

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 18
                                    spacing: 12

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 10

                                        Text {
                                            text: "Desktop settings"
                                            color: theme.text
                                            font.family: theme.mono
                                            font.pixelSize: 18
                                            font.bold: true
                                        }

                                        Item { Layout.fillWidth: true }

                                        TinyButton {
                                            Layout.preferredWidth: 72
                                            text: "Back"
                                            onClicked: activePage = "chat"
                                        }
                                    }

                                    Flickable {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        contentWidth: width
                                        contentHeight: settingsColumn.implicitHeight
                                        boundsBehavior: Flickable.StopAtBounds

                                        ScrollBar.vertical: ScrollBar {
                                            policy: ScrollBar.AsNeeded
                                            active: true
                                            width: 10
                                        }

                                        ColumnLayout {
                                            id: settingsColumn
                                            width: parent.width
                                            spacing: 12

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 156
                                                radius: 6
                                                color: theme.panel
                                                border.color: theme.line
                                                border.width: 1

                                                ColumnLayout {
                                                    id: profileSection
                                                    anchors.fill: parent
                                                    anchors.margins: 16
                                                    spacing: 10

                                                    Text {
                                                        text: "Profile"
                                                        color: theme.purple
                                                        font.family: theme.mono
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 10

                                                        Text {
                                                            text: "Enable desktop override"
                                                            color: theme.text
                                                            font.family: theme.mono
                                                            font.pixelSize: 13
                                                            font.bold: true
                                                            Layout.fillWidth: true
                                                        }

                                                        CheckBox {
                                                            id: settingsEnabled
                                                            checked: settingsState.enabled
                                                            onToggled: settingsState.enabled = checked
                                                        }
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsNameField
                                                            Layout.fillWidth: true
                                                            label: "Profile name"
                                                            placeholder: "Desktop profile"
                                                            value: settingsState.name
                                                            onValueChanged: settingsState.name = value
                                                        }

                                                        SettingField {
                                                            id: settingsProviderField
                                                            Layout.fillWidth: true
                                                            label: "Provider"
                                                            placeholder: "openai / anthropic"
                                                            value: settingsState.provider
                                                            onValueChanged: settingsState.provider = value
                                                        }
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 198
                                                radius: 6
                                                color: theme.panel
                                                border.color: theme.line
                                                border.width: 1

                                                ColumnLayout {
                                                    id: connectionSection
                                                    anchors.fill: parent
                                                    anchors.margins: 16
                                                    spacing: 10

                                                    Text {
                                                        text: "Connection"
                                                        color: theme.purple
                                                        font.family: theme.mono
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsApiKeyField
                                                            Layout.fillWidth: true
                                                            label: "API key"
                                                            placeholder: "sk-..."
                                                            secret: true
                                                            value: settingsState.apiKey
                                                            onValueChanged: settingsState.apiKey = value
                                                        }

                                                        SettingField {
                                                            id: settingsBaseUrlField
                                                            Layout.fillWidth: true
                                                            label: "Base URL"
                                                            placeholder: "https://api.example.com"
                                                            value: settingsState.baseUrl
                                                            onValueChanged: settingsState.baseUrl = value
                                                        }
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsModelField
                                                            Layout.fillWidth: true
                                                            label: "Model"
                                                            placeholder: "gpt-4.1 / claude..."
                                                            value: settingsState.model
                                                            onValueChanged: settingsState.model = value
                                                        }

                                                        SettingField {
                                                            id: settingsProxyField
                                                            Layout.fillWidth: true
                                                            label: "Proxy"
                                                            placeholder: "http://127.0.0.1:7890"
                                                            value: settingsState.proxy
                                                            onValueChanged: settingsState.proxy = value
                                                        }
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 278
                                                radius: 6
                                                color: theme.panel
                                                border.color: theme.line
                                                border.width: 1

                                                ColumnLayout {
                                                    id: behaviorSection
                                                    anchors.fill: parent
                                                    anchors.margins: 16
                                                    spacing: 10

                                                    Text {
                                                        text: "Behavior"
                                                        color: theme.purple
                                                        font.family: theme.mono
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsContextWindowField
                                                            Layout.fillWidth: true
                                                            label: "Context window"
                                                            placeholder: "28000"
                                                            value: settingsState.contextWindow
                                                            onValueChanged: settingsState.contextWindow = value
                                                        }

                                                        SettingField {
                                                            id: settingsTemperatureField
                                                            Layout.fillWidth: true
                                                            label: "Temperature"
                                                            placeholder: "0.2"
                                                            value: settingsState.temperature
                                                            onValueChanged: settingsState.temperature = value
                                                        }

                                                        SettingField {
                                                            id: settingsMaxTokensField
                                                            Layout.fillWidth: true
                                                            label: "Max tokens"
                                                            placeholder: "4096"
                                                            value: settingsState.maxTokens
                                                            onValueChanged: settingsState.maxTokens = value
                                                        }
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsMaxRetriesField
                                                            Layout.fillWidth: true
                                                            label: "Max retries"
                                                            placeholder: "4"
                                                            value: settingsState.maxRetries
                                                            onValueChanged: settingsState.maxRetries = value
                                                        }

                                                        SettingField {
                                                            id: settingsTimeoutField
                                                            Layout.fillWidth: true
                                                            label: "Timeout"
                                                            placeholder: "60"
                                                            value: settingsState.timeout
                                                            onValueChanged: settingsState.timeout = value
                                                        }

                                                        SettingField {
                                                            id: settingsThinkingField
                                                            Layout.fillWidth: true
                                                            label: "Thinking mode"
                                                            placeholder: "disabled"
                                                            value: settingsState.thinking
                                                            onValueChanged: settingsState.thinking = value
                                                        }
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 12

                                                        SettingField {
                                                            id: settingsThinkingBudgetField
                                                            Layout.fillWidth: true
                                                            label: "Thinking budget"
                                                            placeholder: "10000"
                                                            value: settingsState.thinkingBudget
                                                            onValueChanged: settingsState.thinkingBudget = value
                                                        }

                                                        Item { Layout.fillWidth: true }
                                                        Item { Layout.fillWidth: true }
                                                    }
                                                }
                                            }

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 292
                                                radius: 6
                                                color: theme.panel
                                                border.color: theme.line
                                                border.width: 1

                                                ColumnLayout {
                                                    id: promptSection
                                                    anchors.fill: parent
                                                    anchors.margins: 16
                                                    spacing: 10

                                                    Text {
                                                        text: "Prompt"
                                                        color: theme.purple
                                                        font.family: theme.mono
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }

                                                    SettingField {
                                                        id: settingsSystemPromptField
                                                        Layout.fillWidth: true
                                                        label: "System prompt"
                                                        placeholder: "Project instructions for the agent"
                                                        multiline: true
                                                        value: settingsState.systemPrompt
                                                        onValueChanged: settingsState.systemPrompt = value
                                                    }

                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 8

                                                        TinyButton {
                                                            Layout.preferredWidth: 88
                                                            text: "Save"
                                                            primary: true
                                                            onClicked: saveSettingsPage()
                                                        }

                                                        TinyButton {
                                                            Layout.preferredWidth: 88
                                                            text: "Reset"
                                                            onClicked: resetSettingsPage()
                                                        }

                                                        Item { Layout.fillWidth: true }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    DropArea {
        id: fileDropArea
        anchors.fill: parent
        enabled: backend && !(backend.busy_state)
        keys: ["text/uri-list"]
        onEntered: drag.accepted = true
        onDropped: addDroppedFiles(drop)
    }

    Rectangle {
        anchors.fill: parent
        visible: fileDropArea.containsDrag
        color: "#66000000"
        border.color: theme.purple
        border.width: 2
        z: 100

        Rectangle {
            anchors.centerIn: parent
            width: 360
            height: 86
            radius: 6
            color: theme.panel
            border.color: theme.purple
            border.width: 1

            Text {
                anchors.centerIn: parent
                text: "Drop files to attach"
                color: theme.text
                font.family: theme.mono
                font.pixelSize: 16
                font.bold: true
            }
        }
    }
}

