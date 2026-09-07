import QtQuick
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
    text: "J"
    hasVisualContent: true
    horizontalMargin: 8.75
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
}
