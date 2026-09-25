import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Configuration panel for the Wacom Support plugin.
//
// Shows tablet + daemon status and lets the user assign a shell command
// (workspace switch, app launcher, anything) to each of the four pad
// buttons. Settings are persisted to the widget entry in shell.json via
// updateEntryInline; when that API is unavailable (detached testing) they
// fall back to ~/.config/omarchy-wacom-support/buttons.json, which the
// daemon also honors as a manual override.
Panel {
  id: root
  moduleName: "io.github.godisopensource.omarchy-wacom-support"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  property bool editAutoStart: true
  property string savedNote: ""
  property string lastError: ""

  readonly property bool tabletPresent: hostWidget ? hostWidget.tabletPresent === true : false
  readonly property string tabletName: hostWidget ? String(hostWidget.tabletName || "") : ""
  readonly property string tabletNode: hostWidget ? String(hostWidget.tabletNode || "") : ""
  readonly property bool daemonRunning: hostWidget ? hostWidget.daemonRunning === true : false
  readonly property string pluginDir: hostWidget && hostWidget.pluginDir ? String(hostWidget.pluginDir)
    : (Quickshell.env("HOME") + "/.config/omarchy/plugins/" + moduleName)
  readonly property string ctlPath: pluginDir + "/scripts/wacom-ctl"
  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    loadFromSettings()
    refreshError()
    root.controller.show()
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  function currentSettings() {
    if (root.settings) return root.settings
    if (hostWidget && hostWidget.settings) return hostWidget.settings
    return {}
  }

  function loadFromSettings() {
    var rows = Model.buttonRows(currentSettings())
    for (var i = 0; i < rows.length; i++) {
      var delegate = buttonRows.itemAt(i)
      if (!delegate) continue
      delegate.editLabel = rows[i].label
      delegate.editCommand = rows[i].command
      delegate.presetValue = presetFor(rows[i].command)
    }
    editAutoStart = Model.autoStartOf(currentSettings())
    savedNote = ""
  }

  function presetFor(command) {
    var options = Model.presets()
    for (var i = 0; i < options.length; i++) {
      if (String(options[i].value) === String(command)) return String(options[i].value)
    }
    return ""
  }

  function collectEntry() {
    var entry = { id: moduleName, autoStart: editAutoStart }
    for (var i = 0; i < Model.BUTTON_COUNT; i++) {
      var delegate = buttonRows.itemAt(i)
      var n = i + 1
      entry["label" + n] = delegate ? delegate.editLabel : ""
      entry["button" + n] = delegate ? delegate.editCommand : ""
    }
    return entry
  }

  function save() {
    var entry = collectEntry()
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function") {
      root.bar.shell.updateEntryInline(moduleName, entry)
      savedNote = "Saved to bar settings — restarting daemon…"
    } else {
      // Detached/testing fallback: the daemon reads this override file first.
      saveProc.command = ["bash", ctlPath, "save-override", JSON.stringify(entry)]
      saveProc.running = true
      savedNote = "Saved to buttons.json override — restarting daemon…"
    }
    Quickshell.execDetached(["bash", ctlPath, "restart"])
    refreshLater.restart()
  }

  function resetDefaults() {
    var defaults = Model.defaultSettings()
    for (var i = 0; i < Model.BUTTON_COUNT; i++) {
      var delegate = buttonRows.itemAt(i)
      if (!delegate) continue
      var n = i + 1
      delegate.editLabel = defaults["label" + n]
      delegate.editCommand = defaults["button" + n]
      delegate.presetValue = presetFor(defaults["button" + n])
    }
    savedNote = "Defaults loaded — press Save to apply."
  }

  function daemonLogPath() {
    var stateHome = Quickshell.env("XDG_STATE_HOME")
    if (!stateHome || stateHome === "") stateHome = Quickshell.env("HOME") + "/.local/state"
    return stateHome + "/omarchy-wacom-support/daemon.log"
  }

  function refreshError() {
    if (errProc.running) return
    errProc.command = ["sh", "-c", "tail -n 30 '" + daemonLogPath() + "' 2>/dev/null | grep -iE 'failed|error' | tail -n 4"]
    errProc.running = true
  }

  function daemonAction(action) {
    Quickshell.execDetached(["bash", ctlPath, action])
    refreshLater.restart()
  }

  Timer {
    id: refreshLater
    interval: 2000
    repeat: false
    onTriggered: {
      root.refreshError()
      if (hostWidget && typeof hostWidget.refresh === "function") hostWidget.refresh()
    }
  }

  Process {
    id: saveProc
    running: false
    command: []
  }

  Process {
    id: errProc
    running: false
    command: []
    stdout: StdioCollector {
      id: errOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.lastError = String(errOut.text || "").trim()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(330))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        Text {
          width: parent.width
          text: "Wacom pad buttons"
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          width: parent.width
          text: tabletPresent
            ? (tabletName !== "" ? tabletName : "Wacom tablet") + (tabletNode !== "" ? " · " + tabletNode : "")
            : "No Wacom tablet detected"
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          text: daemonRunning ? "Daemon: running" : "Daemon: stopped"
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            text: "Start"
            enabled: !daemonRunning
            opacity: !daemonRunning ? 1 : 0.4
            onClicked: root.daemonAction("start")
          }
          Button {
            text: "Stop"
            enabled: daemonRunning
            opacity: daemonRunning ? 1 : 0.4
            onClicked: root.daemonAction("stop")
          }
          Button {
            text: "Restart"
            onClicked: root.daemonAction("restart")
          }
        }

        Toggle {
          width: parent.width
          label: "Auto-start daemon"
          description: "Launch the button daemon when a tablet is detected"
          checked: root.editAutoStart
          onClicked: root.editAutoStart = !root.editAutoStart
        }

        Text {
          width: parent.width
          visible: root.lastError !== ""
          text: "Last command error:\n" + root.lastError
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Repeater {
          id: buttonRows
          model: Model.BUTTON_COUNT

          delegate: Column {
            width: parent.width
            spacing: Style.space(6)

            property int btnIndex: index + 1
            property string editLabel: ""
            property string editCommand: ""
            property string presetValue: ""
            readonly property bool isCustom: presetValue === "" && editCommand.trim() !== ""

            Text {
              width: parent.width
              text: "Button " + btnIndex + (isCustom ? " (custom)" : "")
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Dropdown {
              width: parent.width
              label: "Preset"
              showLabel: false
              options: Model.presets()
              value: presetValue
              onChanged: function(next) {
                presetValue = next
                if (next !== "") editCommand = next
              }
            }

            TextField {
              width: parent.width
              placeholderText: "Label (e.g. Workspace 1)"
              text: editLabel
              onTextChanged: editLabel = text
            }

            Row {
              width: parent.width
              spacing: Style.space(6)

              TextField {
                width: parent.width - testButton.width - parent.spacing
                placeholderText: "Command (empty = unmapped)"
                text: editCommand
                onTextChanged: {
                  editCommand = text
                  presetValue = root.presetFor(text)
                }
              }

              Button {
                id: testButton
                text: "Test"
                enabled: editCommand.trim() !== ""
                opacity: editCommand.trim() !== "" ? 1 : 0.4
                onClicked: {
                  if (editCommand.trim() !== "")
                    Quickshell.execDetached(["sh", "-c", editCommand])
                }
              }
            }
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            text: "Save & apply"
            onClicked: root.save()
          }
          Button {
            text: "Defaults"
            onClicked: root.resetDefaults()
          }
        }

        Text {
          width: parent.width
          visible: savedNote !== ""
          text: savedNote
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          text: "Passive listener: apps with native support (e.g. Rnote) keep receiving the buttons too."
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }
  }
}
