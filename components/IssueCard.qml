import QtQuick
import qs.Commons

// A single compact issue row used on the board. Dragging it horizontally moves
// it between columns (the host BoardView turns that into a status change);
// a plain click opens the issue in the detail drawer.
Item {
  id: card

  property var app: null
  property var host: null
  property var issue: null
  property int colWidth: 292

  width: colWidth
  height: 44

  Rectangle {
    anchors.fill: parent
    radius: 8
    color: Qt.darker(Color.background, 1.25)
    border.color: Qt.darker(Color.foreground, 1.9)
    border.width: 1

    Rectangle {
      width: 3
      height: parent.height - 14
      anchors.left: parent.left
      anchors.leftMargin: 6
      anchors.verticalCenter: parent.verticalCenter
      radius: 2
      color: app ? app.statusColor(issue) : Color.muted
    }

    Text {
      anchors.left: parent.left
      anchors.leftMargin: 16
      anchors.top: parent.top
      anchors.topMargin: 6
      text: issue ? issue.key : ""
      color: Qt.darker(Color.foreground, 1.25)
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.bold: true
    }

    Text {
      anchors.left: parent.left
      anchors.leftMargin: 16
      anchors.top: parent.top
      anchors.topMargin: 20
      anchors.right: parent.right
      anchors.rightMargin: 12
      text: issue ? issue.summary : ""
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }

    Text {
      anchors.right: parent.right
      anchors.rightMargin: 10
      anchors.verticalCenter: parent.verticalCenter
      text: issue && issue.assigneeName ? app ? app.initials(issue.assigneeName) : "" : "?"
      color: Qt.darker(Color.foreground, 1.4)
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }

  MouseArea {
    id: cardMouse
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.PointingHandCursor
    preventStealing: true

    // While a drag is in progress the host moves its own ghost; the card
    // itself stays in its column so the list does not shuffle under you.
    drag.target: card.host ? card.host.dragGhostItem : card
    drag.axis: Drag.XAndYAxis
    drag.threshold: 8

    onPressed: function(mouse) {
      if (card.host) card.host.prepareDrag(card.issue, cardMouse, mouse)
    }
    drag.onActiveChanged: {
      if (card.host) card.host.onCardDrag(cardMouse.drag.active, card.issue ? card.issue.key : "")
    }
    onClicked: {
      if (app) app.openIssue(card.issue ? card.issue.key : "")
    }
  }
}
