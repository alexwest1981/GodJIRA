import QtQuick
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "custom.jira"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: " "
    labelVisible: false
    tooltipText: "GodJIRA – öppna Jira"
    fixedWidth: Math.max(46, root.bar ? root.bar.barSize : 46)
    fixedHeight: Math.max(26, root.bar ? root.bar.barSize : 26)

    Image {
      anchors.centerIn: parent
      width: 44
      height: 23
      source: Qt.resolvedUrl("./assets/godzilla.svg")
      sourceSize.width: 192
      sourceSize.height: 96
      smooth: true
      mipmap: true
    }

    onPressed: function(button) {
      if (!root.bar) return
      if (button === Qt.RightButton) {
        // Right click forces a refresh of an already-open window.
        root.bar.run("omarchy-shell shell call custom.jira refresh '{}'")
      } else {
        root.bar.run("omarchy-shell shell toggle custom.jira '{}'")
      }
    }
  }

  // ------------------------------------------------------------- watcher
  // Polls the bridge for board changes and raises a desktop notification when
  // something moved / was added / updated / removed on a tracked board. The
  // bridge keeps a baseline on disk, so only real (external) changes notify —
  // the user's own edits inside the app bump the baseline and stay quiet.
  readonly property string bridgePath: {
    var url = Qt.resolvedUrl("./bin/jira_bridge.py").toString()
    return url.replace(/^file:\/\//, "")
  }

  Process {
    id: watchProc
    command: ["python3", root.bridgePath, "watch"]
  }

  function watchTick() {
    if (watchProc.running) return
    watchProc.running = true
  }

  Timer {
    id: watchTimer
    interval: 30000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.watchTick()
  }
}
