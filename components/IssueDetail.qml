import QtQuick
import qs.Commons
import qs.Ui

// Detail drawer for a single issue. Reads state from the root (app): the
// selected key, its transitions and helpers to move/clear.
Rectangle {
  id: detail
  property var app: null

  property var issue: app && app.selectedIssueKey ? app.issueByKey(app.selectedIssueKey) : null
  property bool busy: app ? app.transitionsLoading : false

  anchors.fill: parent

  color: Qt.darker(Color.background, 1.05)
  border.color: Util.alpha(Color.foreground, 0.08)
  border.width: 1

  visible: issue !== null

  function chipVisible(value) {
    return value !== undefined && value !== null && String(value) !== ""
  }
  function canMove() {
    return detail.app && detail.app.issueTransitions.length > 0
  }
  function statusText() {
    if (detail.busy) return "Hämtar statusar…"
    if (!detail.app) return ""
    return detail.app.issueTransitions.length === 0
      ? "Inga statusändringar tillgängliga."
      : ""
  }

  Column {
    anchors.fill: parent
    anchors.topMargin: 14
    spacing: 0

    // header
    Row {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      spacing: Style.space(8)

      Text {
        text: detail.issue ? detail.issue.key : ""
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }

      Rectangle {
        width: 8; height: 8; radius: 4
        anchors.verticalCenter: parent.verticalCenter
        color: detail.app && detail.issue ? app.statusColor(detail.issue) : "transparent"
        visible: detail.issue !== null
      }

      Item { width: parent.width - 220; height: 1 }

      Button {
        text: "Öppna"
        fontSize: Style.font.caption
        tooltipText: "Öppna i webbläsare"
        onClicked: app.openInBrowser(detail.issue.key)
      }
      Button {
        text: "x"
        tooltipText: "Stäng detalj"
        fontSize: Style.font.caption
        onClicked: app.clearIssue()
      }
    }

    Text {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      text: detail.issue ? detail.issue.summary : ""
      wrapMode: Text.Wrap
      color: Qt.darker(Color.foreground, 1.15)
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    // meta chips
    Flow {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      spacing: Style.space(6)

      Rectangle {
        height: 22
        radius: 5
        color: Util.alpha(Color.foreground, 0.08)
        width: chip0.implicitWidth + 14
        visible: detail.chipVisible(detail.issue ? detail.issue.statusName : "")
        Text {
          id: chip0
          anchors.centerIn: parent
          text: detail.issue ? (detail.issue.statusName || "") : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
        }
      }
      Rectangle {
        height: 22
        radius: 5
        color: Util.alpha(Color.foreground, 0.08)
        width: chip1.implicitWidth + 14
        visible: detail.chipVisible(detail.issue ? (detail.issue.typeName || detail.issue.type) : "")
        Text {
          id: chip1
          anchors.centerIn: parent
          text: detail.issue ? (detail.issue.typeName || detail.issue.type || "") : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
      Rectangle {
        height: 22
        radius: 5
        color: Util.alpha(Color.foreground, 0.08)
        width: chip2.implicitWidth + 14
        visible: detail.chipVisible(detail.issue ? detail.issue.priorityName : "")
        Text {
          id: chip2
          anchors.centerIn: parent
          text: detail.issue ? (detail.issue.priorityName || "") : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
      Rectangle {
        height: 22
        radius: 5
        color: Util.alpha(Color.foreground, 0.08)
        width: chip3.implicitWidth + 14
        visible: detail.issue !== null
        Text {
          id: chip3
          anchors.centerIn: parent
          text: detail.issue ? (detail.issue.assigneeName || "Otilldelad") : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
      Rectangle {
        height: 22
        radius: 5
        color: Util.alpha(Color.foreground, 0.08)
        width: chip4.implicitWidth + 14
        visible: detail.chipVisible(detail.issue ? detail.issue.projectKey : "")
        Text {
          id: chip4
          anchors.centerIn: parent
          text: detail.issue ? (detail.issue.projectKey || "") : ""
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }

    Item { width: 1; height: 12 }

    // description
    Text {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      text: "Beskrivning"
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      font.bold: true
    }
    Text {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      text: detail.issue && (detail.issue.description || "").trim()
        ? detail.issue.description
        : "Ingen beskrivning."
      wrapMode: Text.Wrap
      color: detail.issue && (detail.issue.description || "").trim()
        ? Qt.darker(Color.foreground, 1.25)
        : Qt.darker(Color.foreground, 1.6)
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
      maximumLineCount: 16
    }

    Rectangle {
      width: parent.width - 24
      height: 1
      anchors.horizontalCenter: parent.horizontalCenter
      color: Util.alpha(Color.foreground, 0.1)
    }

    Item { width: 1; height: 10 }

    // transitions
    Text {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      text: "Flytta ärendet"
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      font.bold: true
    }
    Text {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      text: detail.statusText()
      color: Qt.darker(Color.foreground, 1.4)
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }

    Flow {
      id: transFlow
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      spacing: Style.space(6)
      visible: !busy && detail.canMove()

      Repeater {
        model: detail.app ? detail.app.issueTransitions : []
        delegate: Rectangle {
          required property var modelData
          width: transFlow.width - 2
          height: 28
          radius: 6
          color: Qt.darker(Color.background, 1.35)
          border.color: Util.alpha(Color.foreground, 0.12)
          border.width: 1

          Text {
            anchors.left: parent.left
            anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: modelData.toStatusName || modelData.name
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Text {
            anchors.right: parent.right
            anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: "→"
            color: Qt.darker(Color.foreground, 1.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: app.moveIssue(detail.issue.key, modelData.toStatusName || modelData.toStatusId)
          }
        }
      }
    }

    Item { width: 1; height: 10 }

    Rectangle {
      width: parent.width - 24
      height: 1
      anchors.horizontalCenter: parent.horizontalCenter
      color: Util.alpha(Color.foreground, 0.1)
    }

    Item { width: 1; height: 8 }

    // delete (only with the right to do so on this board)
    Row {
      width: parent.width - 24
      anchors.horizontalCenter: parent.horizontalCenter
      visible: detail.app !== null && detail.app.boardCanDelete() && detail.issue !== null

      Button {
        id: deleteBtn
        width: 160
        text: detail.deleteArmed
          ? "Klicka igen för att radera"
          : "Radera ärende"
        fontSize: Style.font.caption
        onClicked: {
          if (!detail.deleteArmed) {
            detail.deleteArmed = true
            detail.deleteResetTimer.restart()
          } else {
            detail.deleteResetTimer.stop()
            detail.deleteArmed = false
            detail.app.deleteIssue(detail.issue.key)
          }
        }
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 12
        anchors.left: deleteBtn.right
        width: parent.width - deleteBtn.width - 12
        text: "Kräver admin-/borttagningsrätt i projektet."
        wrapMode: Text.Wrap
        color: Qt.darker(Color.foreground, 1.6)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        visible: false
      }
    }
  }

  property bool deleteArmed: false
  property Timer deleteResetTimer: Timer {
    interval: 4000
    onTriggered: detail.deleteArmed = false
  }

  onIssueChanged: {
    detail.deleteArmed = false
  }
}
