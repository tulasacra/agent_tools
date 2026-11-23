pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
    id: root
    moduleName: "local.agent-usage"
    // The shell routes summon/hide/toggle to this widget on each screen.
    manageIpc: false

    readonly property color foreground: Color.popups.text
    readonly property color muted: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)
    readonly property color paceOrange: "#f97316"
    readonly property color paceYellow: "#eab308"
    readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
    property bool busy: false
    property var rows: []
    property string updatedAt: ""
    property string refreshError: ""
    property double nowMs: Date.now()

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    function formatResetExpiry(value, includeZone) {
        return Qt.formatDateTime(new Date(value), includeZone ? "dd.MM.yy HH:mm t" : "dd.MM.yy HH:mm");
    }

    function applySnapshot(text) {
        var snapshot = Model.parseSnapshot(text);
        rows = snapshot.rows;
        updatedAt = snapshot.generated_at;
        nowMs = Date.now();
    }

    function refresh() {
        if (busy) return;
        refreshError = "";
        busy = true;
        usageProcess.running = true;
    }

    Component.onCompleted: {
        var saved = snapshotFile.text();
        if (!saved) return;
        try {
            applySnapshot(saved);
        } catch (e) {
        }
    }

    onOpenedChanged: if (opened) {
        nowMs = Date.now();
        var snapshotAge = nowMs - Date.parse(updatedAt);
        if (!updatedAt || snapshotAge < 0 || snapshotAge >= 60000) refresh();
        Qt.callLater(function() { keyCatcher.forceActiveFocus(); });
    }

    FileView {
        id: snapshotFile
        path: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state"))
            + "/agent-usage/snapshot.json"
        blockLoading: true
        atomicWrites: true
        printErrors: false
    }

    Process {
        id: usageProcess
        // Also bound the whole process tree, including a hung CLI child.
        command: ["/usr/bin/timeout", "--kill-after=5", "65",
                  Quickshell.env("HOME") + "/.local/bin/au", "--json"]
        // stdout is only committed once both the process and its pipes finish.
        stdout: StdioCollector {
            id: output
            waitForEnd: true
        }
        stderr: StdioCollector { waitForEnd: true }
        onExited: function(exitCode, exitStatus) {
            root.busy = false;
            if (exitCode !== 0 || exitStatus !== 0) {
                root.refreshError = exitCode === 124 || exitCode === 137
                    ? "Quota refresh timed out. Press Refresh to retry."
                    : "au failed (exit " + exitCode + "). Check ~/.local/bin/au, then press Refresh.";
                return;
            }
            try {
                root.applySnapshot(output.text);
                snapshotFile.setText(output.text);
            } catch (e) {
                root.refreshError = e.message;
            }
        }
        // Process has no error signal; failed starts only change running.
        // Defer so a normal exit can finish handling its output first.
        onRunningChanged: if (!running) Qt.callLater(function() {
            if (root.busy && !usageProcess.running) {
                root.busy = false;
                root.refreshError = "Could not start the quota command. Press Refresh to retry.";
            }
        })
    }

    Timer {
        interval: 1000
        running: root.opened
        repeat: true
        onTriggered: root.nowMs = Date.now()
    }

    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "AU"
        tooltipText: "Agent usage"
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.LeftButton) root.toggle();
        }
    }

    component Cell: Text {
        textFormat: Text.PlainText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
    }

    KeyboardPanel {
        id: popup
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: keyCatcher
        contentWidth: popup.fittedContentWidth(Style.space(640))
        contentHeight: popup.fittedContentHeight(content.implicitHeight, Style.space(640))

        PanelKeyCatcher {
            id: keyCatcher
            anchors.fill: parent
            onCloseRequested: root.close()
            onActivateRequested: root.refresh()
            onTextKey: function(text) {
                if (text.toLowerCase() === "r") root.refresh();
            }
            onTabRequested: function(direction) { root.switchPanel(direction); }
            onMoveRequested: function(dx, dy) {
                scroll.contentY = Math.max(0, Math.min(scroll.contentHeight - scroll.height,
                                                      scroll.contentY + dy * Style.space(48)));
            }

            Flickable {
                id: scroll
                anchors.fill: parent
                contentWidth: width
                contentHeight: content.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                interactive: contentHeight > height
                Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

                Column {
                    id: content
                    width: scroll.width
                    spacing: Style.space(14)
                    readonly property real columnSpace: Style.space(12)
                    readonly property real tableWidth: Math.max(0, width - columnSpace * 4 - dateWidth - windowWidth)
                    readonly property real nameWidth: tableWidth * 0.55
                    readonly property real usedWidth: tableWidth * 0.18
                    readonly property real countdownWidth: tableWidth * 0.27
                    readonly property real countdownOffset: nameWidth + usedWidth + columnSpace * 2
                    readonly property real dateOffset: countdownOffset + countdownWidth + columnSpace
                    readonly property real dateWidth: Math.ceil(dateMetrics.advanceWidth)
                    readonly property real windowWidth: Math.ceil(windowMetrics.advanceWidth)

                    TextMetrics {
                        id: dateMetrics
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        text: "00.00.00 00:00"
                    }

                    TextMetrics {
                        id: windowMetrics
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        text: "Window"
                    }

                    Item {
                        width: parent.width
                        implicitHeight: Math.max(title.implicitHeight, updatedLabel.implicitHeight,
                                                 refreshButton.implicitHeight)

                        Cell {
                            id: title
                            anchors.left: parent.left
                            anchors.right: updatedLabel.left
                            anchors.rightMargin: Style.space(12)
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Agent usage"
                            font.pixelSize: Style.font.heading
                            font.bold: true
                        }
                        Cell {
                            id: updatedLabel
                            anchors.right: refreshButton.left
                            anchors.rightMargin: Style.space(12)
                            anchors.verticalCenter: parent.verticalCenter
                            color: root.muted
                            font.pixelSize: Style.font.bodySmall
                            text: root.updatedAt
                                ? (root.refreshError ? "Previous snapshot · " : "Updated ")
                                  + Qt.formatDateTime(new Date(root.updatedAt), "dd.MM.yy HH:mm:ss")
                                : root.busy ? "Fetching quotas…" : "No snapshot yet"
                        }
                        Button {
                            id: refreshButton
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: Style.space(120)
                            implicitHeight: refreshLabel.implicitHeight + verticalPadding * 2
                                + _reservedBorderTop + _reservedBorderBottom
                            enabled: !root.busy
                            opacity: enabled ? 1 : 0.4
                            bordered: true
                            foreground: root.foreground
                            fontFamily: root.fontFamily
                            fontSize: Style.font.bodySmall
                            onClicked: root.refresh()

                            Item {
                                id: refreshLabel
                                anchors.centerIn: parent
                                implicitWidth: (root.busy ? 0 : refreshMnemonic.implicitWidth) + refreshRest.implicitWidth
                                implicitHeight: Math.max(refreshMnemonic.implicitHeight, refreshRest.implicitHeight)

                                Text {
                                    id: refreshMnemonic
                                    visible: !root.busy
                                    textFormat: Text.PlainText
                                    text: "R"
                                    font.underline: true
                                    color: refreshButton.foreground
                                    font.family: refreshButton.fontFamily
                                    font.pixelSize: refreshButton.fontSize
                                    anchors.baseline: refreshRest.baseline
                                }
                                Text {
                                    id: refreshRest
                                    x: root.busy ? 0 : refreshMnemonic.implicitWidth
                                    textFormat: Text.PlainText
                                    text: root.busy ? "Refreshing…" : "efresh"
                                    color: refreshButton.foreground
                                    font.family: refreshButton.fontFamily
                                    font.pixelSize: refreshButton.fontSize
                                }
                            }
                        }
                    }

                    Cell {
                        visible: root.refreshError !== ""
                        width: parent.width
                        text: root.refreshError
                        color: Color.urgent
                        wrapMode: Text.Wrap
                        elide: Text.ElideNone
                    }

                    Row {
                        visible: root.rows.length > 0
                        spacing: content.columnSpace
                        Cell { width: content.nameWidth; text: "Agent"; color: root.muted }
                        Cell { width: content.usedWidth; text: "Used"; color: root.muted; horizontalAlignment: Text.AlignRight }
                        Cell { width: content.countdownWidth; text: "Resets in"; color: root.muted }
                        Cell { width: content.dateWidth; text: "Reset date"; color: root.muted }
                        Cell { width: content.windowWidth; text: "Window"; color: root.muted }
                    }

                    Repeater {
                        model: root.rows
                        delegate: Column {
                            id: quota
                            required property var modelData
                            required property int index
                            readonly property bool failed: modelData.error !== null
                            readonly property color resetTextColor: modelData.window_seconds === 5 * 3600
                                ? root.muted : root.foreground
                            readonly property var pace: Model.usagePace(modelData, root.updatedAt, root.nowMs)
                            readonly property var resetSummary: Model.manualResetSummary(root.rows, index, root.nowMs, root.formatResetExpiry)
                            readonly property string resetTooltip: Model.manualResetTooltip(modelData, root.nowMs, root.formatResetExpiry)
                            width: content.width
                            spacing: Style.space(4)

                            Row {
                                spacing: content.columnSpace
                                Cell {
                                    width: content.nameWidth
                                    text: quota.modelData.label
                                }
                                Cell {
                                    width: content.usedWidth
                                    text: quota.failed ? "—" : quota.modelData.used_percent.toFixed(1) + "%"
                                    horizontalAlignment: Text.AlignRight
                                    color: quota.pace.level === "red" ? Color.urgent
                                        : quota.pace.level === "orange" ? root.paceOrange
                                        : quota.pace.level === "yellow" ? root.paceYellow : root.foreground

                                    MouseArea {
                                        id: paceHover
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        acceptedButtons: Qt.NoButton
                                    }
                                    PanelToolTip {
                                        visible: !quota.failed && paceHover.containsMouse
                                        text: quota.pace.hint
                                        fontFamily: root.fontFamily
                                    }
                                }
                                Cell {
                                    width: content.countdownWidth
                                    text: quota.failed || quota.modelData.reset_at === null
                                        ? "—" : Model.countdown(quota.modelData.reset_at, root.nowMs)
                                    color: quota.resetTextColor
                                }
                                Cell {
                                    width: content.dateWidth
                                    text: quota.failed || quota.modelData.reset_at === null
                                        ? "—" : Qt.formatDateTime(new Date(quota.modelData.reset_at), "dd.MM.yy HH:mm")
                                    color: quota.resetTextColor
                                }
                                Cell {
                                    width: content.windowWidth
                                    text: quota.failed || quota.modelData.window_seconds === null
                                        ? "—" : Model.duration(quota.modelData.window_seconds)
                                    color: quota.resetTextColor
                                }
                            }
                            Cell {
                                visible: quota.failed
                                width: parent.width
                                text: quota.failed ? "Unavailable · " + quota.modelData.error : ""
                                color: Color.urgent
                                font.pixelSize: Style.font.bodySmall
                                wrapMode: Text.Wrap
                                elide: Text.ElideNone
                            }
                            Item {
                                visible: quota.resetSummary !== null
                                width: parent.width
                                implicitHeight: Math.max(resetLabel.implicitHeight, resetRemaining.implicitHeight,
                                                         resetDate.implicitHeight)

                                Cell {
                                    id: resetLabel
                                    width: content.countdownOffset - content.columnSpace
                                    text: quota.resetSummary ? quota.resetSummary.label : ""
                                    color: root.muted
                                    wrapMode: Text.Wrap
                                    elide: Text.ElideNone
                                }
                                Cell {
                                    id: resetRemaining
                                    x: content.countdownOffset
                                    width: content.countdownWidth
                                    text: quota.resetSummary ? quota.resetSummary.remaining : ""
                                    color: root.muted
                                    wrapMode: Text.Wrap
                                    elide: Text.ElideNone
                                }
                                Cell {
                                    id: resetDate
                                    x: content.dateOffset
                                    width: parent.width - x
                                    text: quota.resetSummary ? quota.resetSummary.date : ""
                                    color: root.muted
                                    wrapMode: Text.Wrap
                                    elide: Text.ElideNone
                                }

                                MouseArea {
                                    id: resetHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.NoButton
                                }
                                PanelToolTip {
                                    visible: resetHover.containsMouse && quota.resetTooltip !== ""
                                    text: quota.resetTooltip
                                    fontFamily: root.fontFamily
                                }
                            }
                            PanelSeparator {
                                visible: quota.index < root.rows.length - 1
                                    && quota.modelData.provider !== root.rows[quota.index + 1].provider
                                width: parent.width
                                foreground: root.foreground
                            }
                        }
                    }

                }
            }
        }
    }

    // Keep keyboard input on AU after KeyboardPanel releases exclusive focus.
    // A click on another output clears the grab and dismisses the panel.
    HyprlandFocusGrab {
        active: popup.open && popup.focusPrimed
        windows: [popup]
        onCleared: root.close()
    }
}
