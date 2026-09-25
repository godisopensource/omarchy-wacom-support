import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Bar widget for the Wacom Support plugin.
//
// The widget hides itself (visible + zero size) when no Wacom tablet is
// connected, and polls presence every few seconds so hotplug just works.
// When a tablet appears and autoStart is on, the pad-button daemon is
// started automatically. Left click opens the configuration panel.
BarWidget {
  id: root
  moduleName: "io.github.godisopensource.omarchy-wacom-support"

  property bool tabletPresent: false
  property string tabletName: ""
  property string tabletNode: ""
  property bool daemonRunning: false
  property int daemonPid: 0
  property bool probed: false

  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/" + moduleName
  readonly property string ctlPath: pluginDir + "/scripts/wacom-ctl"
  readonly property string statusPath: pluginDir + "/scripts/wacom-status"

  function autoStartEnabled() {
    var value = setting("autoStart", true)
    return value === true || value === "true" || value === 1
  }

  function tooltipText() {
    if (!tabletPresent) return "Wacom: no tablet detected"
    var line = tabletName !== "" ? tabletName : "Wacom tablet"
    line += daemonRunning ? " — daemon running" : " — daemon stopped"
    return line
  }

  function refresh() {
    if (statusProc.running) return
    statusProc.command = ["bash", statusPath, "--json"]
    statusProc.running = true
  }

  function applyStatus(raw) {
    var status = Model.parseStatus(raw)
    tabletPresent = status.present
    tabletName = status.name
    tabletNode = status.node
    daemonRunning = status.daemon
    daemonPid = status.pid
    probed = true
    if (tabletPresent && !daemonRunning && autoStartEnabled())
      Quickshell.execDetached(["bash", ctlPath, "start"])
  }

  // ---- Panel lifecycle (shell summon/hide/toggle routing) ----
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  // Hidden widgets must not reserve bar space.
  visible: tabletPresent
  implicitWidth: tabletPresent ? button.implicitWidth : 0
  implicitHeight: tabletPresent ? button.implicitHeight : 0

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Component.onCompleted: refresh()

  Timer {
    id: pollTimer
    interval: 4000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "✎"
    tooltipText: root.tooltipText()
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }

  Process {
    id: statusProc
    running: false
    command: []
    stdout: StdioCollector {
      id: statusOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyStatus(statusOut.text)
      else if (!root.probed) root.applyStatus("")
    }
  }
}
