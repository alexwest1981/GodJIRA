import QtQuick
import qs.Commons

Item {
  property var app: null
  property string title: ""

  Rectangle {
    anchors.fill: parent
    color: "transparent"

    Column {
      anchors.centerIn: parent
      spacing: Style.space(8)

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: title
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.huge
        font.bold: true
      }
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: "Förhandsvisning – den här vyn kommer i en senare version."
        color: Qt.darker(Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }
    }
  }
}
