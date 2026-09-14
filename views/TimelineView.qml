import QtQuick
import qs.Commons
import qs.Ui

// Timeline: the board's sprints as lanes on a date axis, with the backlog as
// the last lane. The lane width follows the sprint's window, so a two-week
// sprint is twice as wide as a one-week one, and the bar under each header
// fills up as the sprint runs.
//
// An issue moves to another sprint (or back to the backlog) from the small
// menu on the card, or from the sprint section on the issue detail page.
Item {
  id: timelineView
  anchors.fill: parent

  property var app: null
  property int rev: app ? app.snapshotRev : -1
  property bool mineFilter: app ? app.onlyMine : false

  property var lanes: []
  property string boardTitle: ""
  property bool hasSprints: false
  property string menuKey: ""
  property real menuX: 0
  property real menuY: 0

  function rebuild() {
    menuKey = ""
    var board = app ? app.currentBoard() : null
    if (!board) { lanes = []; boardTitle = ""; hasSprints = false; return }
    boardTitle = (board.projectKey || "") + " · " + board.name

    var sprints = (board.sprints || []).slice()
    sprints.sort(function(a, b) { return (a.startMs || 0) - (b.startMs || 0) })
    hasSprints = sprints.length > 0

    var all = app.visibleIssues((board.issues || []).concat(board.backlog || []))
    var out = []
    for (var i = 0; i < sprints.length; i++) {
      var sp = sprints[i]
      var list = []
      for (var j = 0; j < all.length; j++) {
        if (String(all[j].sprintId) === String(sp.id)) list.push(all[j])
      }
      out.push({
        id: String(sp.id),
        name: sp.name,
        state: sp.state,
        startMs: sp.startMs,
        endMs: sp.endMs,
        range: app.sprintRange(sp),
        progress: app.sprintProgress(sp),
        issues: list,
        backlog: false
      })
    }

    var backlog = []
    for (var k = 0; k < all.length; k++) if (!all[k].sprintId) backlog.push(all[k])
    out.push({
      id: "backlog",
      name: "Backlog",
      state: "backlog",
      startMs: 0,
      endMs: 0,
      range: count(backlog.length, "ärende utan sprint", "ärenden utan sprint"),
      progress: 0,
      issues: backlog,
      backlog: true
    })
    lanes = out
  }

  // A lane is as wide as its sprint window, bounded so a 3-day and a 3-month
  // sprint both stay readable - and narrow enough that the backlog lane is
  // still on screen next to a couple of two-week sprints.
  function laneWidth(lane) {
    if (lane.backlog) return 300
    var span = (lane.endMs || 0) - (lane.startMs || 0)
    if (span <= 0) return 300
    var days = span / 86400000
    return Math.max(260, Math.min(560, 60 + days * 18))
  }

  function count(n, one, many) {
    return n + " " + (n === 1 ? one : many)
  }

  function laneStateLabel(lane) {
    if (lane.backlog) return "oplanerat"
    if (lane.state === "active") return "aktiv"
    if (lane.state === "closed") return "avslutad"
    return "kommande"
  }

  function laneStateColor(lane) {
    if (lane.backlog) return Qt.darker(Color.foreground, 1.6)
    if (lane.state === "active") return Color.accent
    if (lane.state === "closed") return "#4f9d69"
    return "#c9a227"
  }

  function openMenu(laneId, key, item) {
    var p = item.mapToItem(timelineView, 0, 0)
    menuX = p.x + item.width
    menuY = p.y
    menuKey = key
  }

  onRevChanged: rebuild()
  onMineFilterChanged: rebuild()
  Component.onCompleted: rebuild()

  Column {
    anchors.fill: parent
    spacing: 0

    // ---- header
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
          text: "Timeline"
          color: Qt.darker(Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        text: timelineView.hasSprints
          ? timelineView.count(timelineView.lanes.length - 1, "sprint", "sprintar")
            + " · flytta ett ärende mellan banorna via menyn på kortet"
          : ""
        color: Qt.darker(Color.foreground, 1.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }

    // ---- lanes
    Item {
      width: parent.width
      height: parent.height - 52

      Text {
        anchors.centerIn: parent
        width: parent.width - 80
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: timelineView.hasSprints
          ? "Inga ärenden att visa."
          : "Den här tavlan har inga sprintar. Skapa sprintar i Jira så visas de här som banor."
        color: Qt.darker(Color.foreground, 1.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        visible: timelineView.lanes.length === 0 ||
                 (timelineView.lanes.length === 1 && timelineView.lanes[0].issues.length === 0)
      }

      Flickable {
        id: laneScroll
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.topMargin: 4
        anchors.bottomMargin: 8
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        contentWidth: laneRow.width
        contentHeight: laneRow.height

        Row {
          id: laneRow
          spacing: Style.space(10)

          Repeater {
            model: timelineView.lanes

            Rectangle {
              required property var modelData

              id: laneCard
              width: timelineView.laneWidth(modelData)
              height: Math.max(laneBody.height + 76, 200)
              radius: 10
              color: Qt.darker(Color.background, 1.12)
              border.color: Util.alpha(Color.foreground, 0.08)
              border.width: 1

              Column {
                id: laneBody
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 12
                spacing: 8

                Row {
                  width: parent.width
                  spacing: Style.space(8)

                  Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: timelineView.laneStateColor(modelData)
                  }
                  Text {
                    text: modelData.name
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: true
                  }
                  Item { width: parent.width - 200; height: 1 }
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: timelineView.laneStateLabel(modelData)
                    color: Qt.darker(Color.foreground, 1.5)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }

                Text {
                  width: parent.width
                  text: modelData.range + "   ·   "
                        + timelineView.count(modelData.issues.length, "ärende", "ärenden")
                  color: Qt.darker(Color.foreground, 1.45)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }

                // how much of the sprint window has passed
                Rectangle {
                  width: parent.width
                  height: 4
                  radius: 2
                  visible: !modelData.backlog
                  color: Qt.darker(Color.background, 1.4)

                  Rectangle {
                    width: parent.width * modelData.progress
                    height: parent.height
                    radius: 2
                    color: timelineView.laneStateColor(modelData)
                  }
                }

                Rectangle {
                  width: parent.width
                  height: 1
                  color: Util.alpha(Color.foreground, 0.08)
                }

                Repeater {
                  model: modelData.issues

                  Rectangle {
                    required property var modelData

                    id: laneIssue
                    width: laneBody.width
                    height: 46
                    radius: 8
                    color: Qt.darker(Color.background, 1.25)
                    border.color: timelineView.menuKey === modelData.key
                      ? Color.accent
                      : Util.alpha(Color.foreground, 0.08)
                    border.width: 1

                    Rectangle {
                      width: 3
                      height: parent.height - 14
                      anchors.left: parent.left
                      anchors.leftMargin: 6
                      anchors.verticalCenter: parent.verticalCenter
                      radius: 2
                      color: timelineView.app.statusColor(modelData)
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: 16
                      anchors.top: parent.top
                      anchors.topMargin: 6
                      text: modelData.key
                      color: Qt.darker(Color.foreground, 1.25)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: 16
                      anchors.top: parent.top
                      anchors.topMargin: 22
                      anchors.right: moveBtn.left
                      anchors.rightMargin: 6
                      text: modelData.summary
                      color: Color.foreground
                      font.family: Style.font.family
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                    }

                    Button {
                      id: moveBtn
                      anchors.right: parent.right
                      anchors.rightMargin: 6
                      anchors.verticalCenter: parent.verticalCenter
                      width: 30
                      horizontalPadding: 2
                      text: "⇄"
                      fontSize: Style.font.caption
                      tooltipText: "Flytta till en annan sprint"
                      onClicked: timelineView.openMenu(laneCard.modelData.id, modelData.key, moveBtn)
                    }

                    MouseArea {
                      anchors.fill: parent
                      anchors.rightMargin: 34
                      cursorShape: Qt.PointingHandCursor
                      onClicked: timelineView.app.openIssue(modelData.key)
                    }
                  }
                }

                Text {
                  width: parent.width
                  visible: modelData.issues.length === 0
                  text: modelData.backlog ? "Backloggen är tom." : "Inga ärenden i sprinten."
                  color: Qt.darker(Color.foreground, 1.6)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }
      }

      // ---- move menu (sprint picker for one issue)
      MouseArea {
        anchors.fill: parent
        visible: timelineView.menuKey !== ""
        onClicked: timelineView.menuKey = ""
      }

      Rectangle {
        id: moveMenu
        visible: timelineView.menuKey !== ""
        z: 40
        x: Math.min(timelineView.menuX + 6, laneScroll.width - width - 4)
        y: Math.min(Math.max(4, timelineView.menuY), laneScroll.height - height - 4)
        width: 210
        height: menuCol.height + 16
        radius: 8
        color: Qt.darker(Color.background, 1.02)
        border.color: Util.alpha(Color.foreground, 0.14)
        border.width: 1

        property string movingKey: timelineView.menuKey

        Column {
          id: menuCol
          x: 8
          y: 8
          width: parent.width - 16
          spacing: 4

          Text {
            text: "Flytta " + moveMenu.movingKey
            color: Qt.darker(Color.foreground, 1.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Repeater {
            model: timelineView.lanes

            Rectangle {
              required property var modelData

              width: menuCol.width
              height: 26
              radius: 5
              color: currentLane ? Util.alpha(Color.accent, 0.18) : "transparent"

              property bool currentLane: {
                var issue = timelineView.app ? timelineView.app.issueByKey(moveMenu.movingKey) : null
                if (!issue) return false
                var id = String(issue.sprintId || "")
                return (modelData.backlog && id === "") || (!modelData.backlog && id === modelData.id)
              }

              Text {
                anchors.left: parent.left
                anchors.leftMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.name + (parent.currentLane ? "  (nu)" : "")
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                enabled: !parent.currentLane
                onClicked: {
                  timelineView.app.assignIssue(moveMenu.movingKey, modelData.backlog ? "backlog" : modelData.id)
                  timelineView.menuKey = ""
                }
              }
            }
          }
        }
      }
    }
  }
}
