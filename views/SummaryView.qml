import QtQuick
import qs.Commons
import qs.Ui

Item {
  property var app: null
  property int rev: app ? app.snapshotRev : -1

  function refreshModel() {
    modelView.statusText = ""
    modelView.rebuild()
  }

  onRevChanged: refreshModel()

  Rectangle {
    id: modelView
    anchors.fill: parent
    color: "transparent"

    property string statusText: ""
    property var myBoard: null
    property var groupModel: []

    function rebuild() {
      var board = app ? app.currentBoard() : null
      myBoard = board
      if (!board) { groupModel = []; statusText = "Ingen tavla vald."; return }
      var groups = [
        { name: "Att göra", cat: "new", color: "#c9a227", keys: [] },
        { name: "Pågår", cat: "indeterminate", color: "#4a8fd6", keys: [] },
        { name: "Klart", cat: "done", color: "#4f9d69", keys: [] }
      ]
      var issues = (board.issues || []).concat(board.backlog || [])
      for (var i = 0; i < issues.length; i++) {
        var iss = issues[i]
        var cat = iss.statusCategory || "indeterminate"
        var target = null
        for (var g = 0; g < groups.length; g++) if (groups[g].cat === cat) { target = groups[g]; break }
        if (!target) target = groups[1]
        target.keys.push(iss)
      }
      groupModel = groups

      var mine = []
      var recent = []
      for (var j = 0; j < issues.length; j++) {
        var o = issues[j]
        if (o.assigneeEmail && o.assigneeEmail === app.myEmail()) {
          if (o.statusCategory === "indeterminate") mine.push(o)
        }
        if (o.lastTransitionMs) recent.push(o)
      }
      mineModel = mine
      recent.sort(function(a, b) { return (b.lastTransitionMs || 0) - (a.lastTransitionMs || 0) })
      recentModel = recent.slice(0, 8)
    }

    property var mineModel: []
    property var recentModel: []

    Column {
      anchors.fill: parent
      spacing: 0

      Flickable {
        width: parent.width
        height: parent.height - footerBar.height
        contentHeight: contentCol.height
        clip: true

        Column {
          id: contentCol
          width: parent.width
          anchors.left: parent.left
          anchors.leftMargin: 20
          anchors.rightMargin: 20
          spacing: Style.space(16)

          Item { width: 1; height: 4 }

          // ---- headline
          Column {
            width: parent.width
            spacing: 4

            Text {
              text: {
                var b = modelView.myBoard
                if (!b) return "Jira"
                return b.projectKey + " · " + b.name
              }
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              width: parent.width
              text: {
                var b = modelView.myBoard
                if (!b) return ""
                var extra = b.type === "kanban" ? "Kanban" : "Scrum"
                if (b.sprint) extra = app.sprintLabel(b) + "   ·   " + extra
                return extra
              }
              color: Qt.darker(Color.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
          }

          // ---- category cards
          Row {
            width: parent.width
            spacing: Style.space(10)

            Repeater {
              model: modelView.groupModel

              Rectangle {
                required property var modelData

                width: (parent.width - Style.space(10) * 2) / 3
                height: 76
                radius: 10
                color: Qt.darker(Color.background, 1.1)
                border.color: Util.alpha(Color.foreground, 0.08)
                border.width: 1

                Rectangle {
                  width: 3
                  height: parent.height - 20
                  anchors.left: parent.left
                  anchors.leftMargin: 10
                  anchors.verticalCenter: parent.verticalCenter
                  radius: 2
                  color: modelData.color
                }
                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: 24
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.name
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                Text {
                  anchors.right: parent.right
                  anchors.rightMargin: 16
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.keys.length
                  color: Qt.darker(Color.foreground, 1.3)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.huge
                  font.bold: true
                }
              }
            }
          }

          // ---- my open items
          Column {
            width: parent.width
            spacing: Style.space(6)

            Text {
              text: "Mina öppna ärenden"
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Repeater {
              model: modelView.mineModel

              Rectangle {
                required property var modelData

                width: parent.width
                height: 38
                radius: 8
                color: Qt.darker(Color.background, 1.08)

                Row {
                  anchors.fill: parent
                  anchors.leftMargin: 12
                  anchors.rightMargin: 12
                  spacing: Style.space(10)

                  Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: app.statusColor(modelData)
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.key
                    color: Qt.darker(Color.foreground, 1.25)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    width: 90
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.summary
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.bodySmall
                    width: parent.width - 90 - 130 - Style.space(20)
                    elide: Text.ElideRight
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.statusName || ""
                    color: Qt.darker(Color.foreground, 1.4)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    width: 100
                    horizontalAlignment: Text.AlignRight
                  }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: {
                      var initials = app.initials(modelData.assigneeName)
                      return initials ? "" : ""
                    }
                    visible: false
                  }
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: app.openIssue(modelData.key)
                }
              }
            }
          }

          // ---- recently moved
          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: modelView.recentModel.length > 0

            Text {
              text: "Senast uppdaterade"
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Repeater {
              model: modelView.recentModel

              Rectangle {
                required property var modelData

                width: parent.width
                height: 30
                radius: 6

                Row {
                  anchors.left: parent.left
                  anchors.leftMargin: 12
                  anchors.right: parent.right
                  anchors.rightMargin: 12
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(10)

                  Text {
                    text: modelData.key
                    color: Qt.darker(Color.foreground, 1.25)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    width: 90
                  }
                  Text {
                    text: modelData.summary
                    color: Qt.darker(Color.foreground, 1.15)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                    width: parent.width - 90 - 120
                  }
                  Text {
                    text: {
                      var when = modelData.lastTransitionText || ""
                      if (modelData.lastTransitionMs) {
                        var d = new Date(modelData.lastTransitionMs)
                        when = Qt.formatDateTime(d, "d MMM HH:mm")
                      }
                      return when
                    }
                    color: Qt.darker(Color.foreground, 1.5)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    width: 110
                    horizontalAlignment: Text.AlignRight
                  }
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: app.openIssue(modelData.key)
                }
              }
            }
          }
        }
      }

      Rectangle {
        id: footerBar
        width: parent.width
        height: 30
        color: "transparent"

        Text {
          anchors.left: parent.left
          anchors.leftMargin: 20
          anchors.verticalCenter: parent.verticalCenter
          text: modelView.statusText || ("Tavla: " + (modelView.myBoard ? modelView.myBoard.issues.length + " ärenden på tavlan, " + modelView.myBoard.backlog.length + " i backlog" : "inget valt"))
          color: Qt.darker(Color.foreground, 1.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
