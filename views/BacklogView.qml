import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: backlogView
  anchors.fill: parent

  property var app: null
  property int rev: app ? app.snapshotRev : -1
  property string selKey: app ? app.selectedIssueKey : ""
  property string filterText: ""
  property var backlogIssues: []
  property string boardTitle: ""

  function filterModel(list) {
    var f = backlogView.filterText.toLowerCase().trim()
    var out = []
    for (var i = 0; i < list.length; i++) {
      var it = list[i]
      if (!f) { out.push(it); continue }
      var hay = (it.key + " " + it.summary + " " + (it.typeName || "")).toLowerCase()
      if (hay.indexOf(f) !== -1) out.push(it)
    }
    return out
  }

  function build() {
    if (!app) { backlogIssues = []; boardTitle = ""; return }
    var board = app.currentBoard()
    if (!board) { backlogIssues = []; boardTitle = ""; return }
    boardTitle = (board.projectKey || "") + " · " + board.name
    backlogIssues = filterModel(board.backlog || [])
  }

  onRevChanged: build()
  onFilterTextChanged: build()

  Component.onCompleted: build()

  Column {
    anchors.fill: parent
    spacing: 0

    Rectangle {
      width: parent.width
      height: 52
      color: "transparent"

      Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(12)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: boardTitle
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Backlog"
          color: Qt.darker(Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: app && app.snapshot ? app.snapshot.boards.length + " tavlor" : ""
          visible: false
        }
      }

      TextField {
        id: searchField
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        width: 230
        placeholderText: "Filtrera…"
        text: backlogView.filterText
        onTextChanged: backlogView.filterText = text
      }
    }

    // body
    Item {
      width: parent.width
      height: parent.height - 52

      Row {
        anchors.fill: parent
        spacing: 0

        Rectangle {
          id: listArea
          width: parent.width - (rhsBack.visible ? rhsBack.width : 0)
          height: parent.height
          color: "transparent"
          clip: true

          Text {
            anchors.centerIn: parent
            text: app && app.snapshot && app.snapshot.boards.length > 0
              ? "Ingen backlog på den valda tavlan."
              : "Läser in…"
            color: Qt.darker(Color.foreground, 1.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            visible: backlogView.backlogIssues.length === 0
          }

          ListView {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.topMargin: 4
            anchors.bottomMargin: 8
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: backlogView.backlogIssues
            spacing: 4

            delegate: Rectangle {
              required property var modelData
              required property int index
              width: ListView.view.width
              height: 46
              radius: 8
              color: Qt.darker(Color.background, 1.2)
              border.color: Util.alpha(Color.foreground, 0.06)
              border.width: 1

              Row {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: Style.space(12)

                Rectangle {
                  width: 8; height: 8; radius: 4
                  anchors.verticalCenter: parent.verticalCenter
                  color: backlogView.app.statusColor(modelData)
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.key
                  color: Qt.darker(Color.foreground, 1.25)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  width: 86
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.summary
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                  width: parent.width - 86 - 90 - 60 - Style.space(36)
                  elide: Text.ElideRight
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.typeName || ""
                  color: Qt.darker(Color.foreground, 1.4)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  width: 80
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.storyPoints != null ? String(modelData.storyPoints) : "–"
                  color: Qt.darker(Color.foreground, 1.3)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  width: 24
                  horizontalAlignment: Text.AlignRight
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.assigneeName ? backlogView.app.initials(modelData.assigneeName) : "?"
                  color: Qt.darker(Color.foreground, 1.4)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  width: 26
                  horizontalAlignment: Text.AlignRight
                }
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: backlogView.app.openIssue(modelData.key)
              }
            }
          }
        }

        Rectangle {
          id: rhsBack
          width: 364
          color: "transparent"
          clip: true
          visible: backlogView.selKey !== ""

          Loader {
            anchors.fill: parent
            source: "../components/IssueDetail.qml"
            onLoaded: {
              item.app = backlogView.app
            }
          }
        }
      }
    }
  }
}
