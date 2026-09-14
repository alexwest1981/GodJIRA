import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: backlogView
  anchors.fill: parent

  property var app: null
  property int rev: app ? app.snapshotRev : -1
  property string selKey: app ? app.selectedIssueKey : ""
  property bool mineFilter: app ? app.onlyMine : false
  property string filterText: ""
  property var groups: []
  property string boardTitle: ""
  property var collapsed: ({})

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

  function countDone(list) {
    var n = 0
    for (var i = 0; i < list.length; i++) if (list[i].statusCategory === "done") n++
    return n
  }

  // Jira's Backlog page is the sprints plus the unplanned backlog, and the
  // sprints come first, oldest start date first. Same shape here.
  function build() {
    if (!app) { groups = []; boardTitle = ""; return }
    var board = app.currentBoard()
    if (!board) { groups = []; boardTitle = ""; return }
    boardTitle = (board.projectKey || "") + " · " + board.name

    var all = app.visibleIssues((board.issues || []).concat(board.backlog || []))
    var sprints = (board.sprints || []).slice()
    sprints.sort(function(a, b) { return (a.startMs || 0) - (b.startMs || 0) })

    var out = []
    for (var s = 0; s < sprints.length; s++) {
      var sp = sprints[s]
      var mine = []
      for (var i = 0; i < all.length; i++) {
        if (String(all[i].sprintId) === String(sp.id)) mine.push(all[i])
      }
      var done = countDone(mine)
      out.push({
        id: String(sp.id), name: sp.name, range: app.sprintRange(sp),
        state: sp.state, backlog: false,
        issues: filterModel(mine), total: mine.length, done: done, open: mine.length - done
      })
    }

    var rest = []
    for (var j = 0; j < all.length; j++) if (!all[j].sprintId) rest.push(all[j])
    var restDone = countDone(rest)
    out.push({
      id: "backlog", name: "Backlog", range: (board.sprints || []).length > 0 ? "utan sprint" : "",
      state: "backlog", backlog: true,
      issues: filterModel(rest), total: rest.length, done: restDone, open: rest.length - restDone
    })

    groups = out
  }

  function stateLabel(g) {
    if (g.backlog) return "oplanerat"
    if (g.state === "active") return "aktiv"
    if (g.state === "closed") return "avslutad"
    return "kommande"
  }

  function stateColor(g) {
    if (g.backlog) return Qt.darker(Color.foreground, 1.6)
    if (g.state === "active") return Color.accent
    if (g.state === "closed") return Qt.darker(Color.foreground, 1.5)
    return Qt.darker(Color.foreground, 1.25)
  }

  function countLabel(g) {
    if (g.total === 0) return "tom"
    if (g.backlog) return g.total + (g.total === 1 ? " ärende" : " ärenden")
    return g.open + " kvar av " + g.total
  }

  function isCollapsed(id) { return !!collapsed[id] }

  function toggle(id) {
    var c = {}
    for (var k in collapsed) c[k] = collapsed[k]
    c[id] = !c[id]
    collapsed = c
  }

  function visibleRows() {
    var n = 0
    for (var i = 0; i < groups.length; i++) n += groups[i].issues.length
    return n
  }

  onRevChanged: build()
  onFilterTextChanged: build()
  onMineFilterChanged: build()

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
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        height: Style.spacing.controlHeight
        spacing: Style.space(10)

        Item {
          width: mineToggleRow.implicitWidth
          height: parent.height
          Row {
            id: mineToggleRow
            anchors.centerIn: parent
            spacing: 2
            Button {
              text: "Alla"
              fontSize: Style.font.caption
              selected: !(app && app.onlyMine)
              tooltipText: "Visa alla ärenden"
              horizontalPadding: Style.space(8)
              onClicked: if (app) app.onlyMine = false
            }
            Button {
              text: "Mina"
              fontSize: Style.font.caption
              selected: !!(app && app.onlyMine)
              tooltipText: "Visa endast ärenden som är tilldelade dig"
              horizontalPadding: Style.space(8)
              onClicked: if (app) app.onlyMine = true
            }
          }
        }

        Item {
          width: 230
          height: parent.height
          TextField {
            id: searchField
            anchors.centerIn: parent
            width: 230
            placeholderText: "Filtrera…"
            text: backlogView.filterText
            onTextChanged: backlogView.filterText = text
          }
        }
      }
    }

    // ---- body
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
              ? "Ingen backlog och inga sprintar på den valda tavlan."
              : "Läser in…"
            color: Qt.darker(Color.foreground, 1.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            visible: backlogView.visibleRows() === 0 && backlogView.groups.length === 0
          }

          Flickable {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.topMargin: 4
            anchors.bottomMargin: 8
            clip: true
            contentWidth: width
            contentHeight: groupCol.height
            boundsBehavior: Flickable.StopAtBounds

            Column {
              id: groupCol
              width: parent.width
              spacing: Style.space(8)

              Repeater {
                model: backlogView.groups

                delegate: Column {
                  id: group
                  required property var modelData
                  width: groupCol.width
                  spacing: 4

                  // ---- group header: chevron, name, dates, count
                  Rectangle {
                    width: group.width
                    height: 34
                    radius: 8
                    color: "transparent"

                    // Declared first so the toggle button and its tooltip sit
                    // above it; the header background only catches the rest.
                    MouseArea {
                      anchors.fill: parent
                      acceptedButtons: Qt.LeftButton
                      cursorShape: Qt.PointingHandCursor
                      onClicked: backlogView.toggle(modelData.id)
                    }

                    Row {
                      anchors.left: parent.left
                      anchors.leftMargin: 4
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(10)

                      Button {
                        width: 22
                        height: 22
                        text: backlogView.isCollapsed(modelData.id) ? "+" : "-"
                        fontSize: Style.font.caption
                        horizontalPadding: 0
                        tooltipText: backlogView.isCollapsed(modelData.id)
                          ? "Visa ärendena i " + modelData.name
                          : "Fäll ihop " + modelData.name
                        onClicked: backlogView.toggle(modelData.id)
                      }

                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.name
                        color: Color.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        font.bold: true
                      }
                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.range
                        color: Qt.darker(Color.foreground, 1.45)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: backlogView.stateLabel(modelData)
                        color: backlogView.stateColor(modelData)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                    }

                    Row {
                      anchors.right: parent.right
                      anchors.rightMargin: 6
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(8)

                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: backlogView.countLabel(modelData)
                        color: Qt.darker(Color.foreground, 1.3)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }

                  // ---- the sprint's (or backlog's) issues
                  Repeater {
                    model: backlogView.isCollapsed(group.modelData.id) ? [] : group.modelData.issues

                    delegate: Rectangle {
                      id: issueRow
                      required property var modelData
                      width: group.width
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
                          color: backlogView.app.statusColor(issueRow.modelData)
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          text: issueRow.modelData.key
                          color: Qt.darker(Color.foreground, 1.25)
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                          font.bold: true
                          width: 86
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          text: issueRow.modelData.summary
                          color: Color.foreground
                          font.family: Style.font.family
                          font.pixelSize: Style.font.bodySmall
                          width: parent.width - 86 - 90 - 60 - Style.space(36)
                          elide: Text.ElideRight
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          text: issueRow.modelData.typeName || ""
                          color: Qt.darker(Color.foreground, 1.4)
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                          width: 80
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          text: issueRow.modelData.storyPoints != null ? String(issueRow.modelData.storyPoints) : "–"
                          color: Qt.darker(Color.foreground, 1.3)
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                          width: 24
                          horizontalAlignment: Text.AlignRight
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          text: issueRow.modelData.assigneeName
                            ? backlogView.app.initials(issueRow.modelData.assigneeName)
                            : "?"
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
                        onClicked: backlogView.app.openIssue(issueRow.modelData.key)
                      }
                    }
                  }
                }
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
